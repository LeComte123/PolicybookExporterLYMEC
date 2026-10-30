import shutil
import subprocess
import sys

import pytest

import pbook
import validate

CONGRESSES = """\
- id: 2026-Vilnius
  name: LYMEC Autumn Congress 2026
  city: Vilnius
  date_start: 2026-10-23
  date_end: 2026-10-25
"""


def make_meta(rid: str, number: str, **overrides) -> dict:
    meta = {
        "id": rid,
        "number": number,
        "title": f"Title of {rid}",
        "congress": "2026-Vilnius",
        "adopted": "2026-10-25",
        "category": "climate-energy",
        "status": "in_force",
        "replaces": [],
        "superseded_by": None,
        "amends": [],
        "submitted_by": ["Julis"],
        "co_signers": [],
        "openslides_number": rid[-2:],
        "source": "openslides",
    }
    meta.update(overrides)
    return meta


@pytest.fixture
def repo(tmp_path):
    """A mini repository with the real categories.yml and two valid resolutions."""
    shutil.copy(pbook.ROOT / "categories.yml", tmp_path / "categories.yml")
    (tmp_path / "congresses.yml").write_text(CONGRESSES, encoding="utf-8")
    add(tmp_path, make_meta("2026-Vilnius-12", "6.21"))
    add(tmp_path, make_meta("2026-Vilnius-18", "6.22"))
    return tmp_path


def add(root, meta, body="LYMEC calls for:\n\n- something", name=None):
    path = root / "resolutions" / (name or pbook.file_name(meta))
    pbook.write_resolution(path, meta, body)
    return path


def path_of(root, rid):
    (path,) = (root / "resolutions").glob(f"*_{rid}.md")
    return path


def load(root, rid):
    return pbook.load_resolution(path_of(root, rid))


def errors(root, fix=False):
    return validate.validate(root, fix=fix).errors


def test_real_repository_data_is_valid():
    report = validate.validate(pbook.ROOT)
    assert report.errors == []


def test_clean_repo_passes(repo):
    report = validate.validate(repo)
    assert report.errors == [] and report.warnings == []
    assert report.checked == 2


def test_missing_required_field(repo):
    meta = make_meta("2026-Vilnius-20", "6.23")
    del meta["title"]
    add(repo, meta)
    assert any("required field 'title'" in e for e in errors(repo))


def test_file_name_must_be_number_and_id(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23"), name="2026-Vilnius-20.md")
    assert any("the file name must be 6.23_2026-Vilnius-20.md" in e for e in errors(repo))


def test_changed_number_renames_the_file_with_fix(repo):
    """Renumbering in the web editor: change the number, the workflow renames the file."""
    path = path_of(repo, "2026-Vilnius-12")
    path.write_text(path.read_text().replace("number: '6.21'", "number: '6.05'"))
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert sorted(p.name for p in (repo / "resolutions").iterdir()) == ["6.05_2026-Vilnius-12.md", "6.22_2026-Vilnius-18.md"]


def test_id_must_start_with_the_congress(repo):
    add(repo, make_meta("2025-Riga-20", "6.23"))
    assert any("id '2025-Riga-20' must start with its congress" in e for e in errors(repo))


def test_duplicate_number_in_force(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.21"))
    assert any("number 6.21 is used by two resolutions in force" in e for e in errors(repo))


def test_archived_resolution_may_share_a_number(repo):
    """After a renumbering an archived resolution keeps its old number, which another one may now have."""
    add(repo, make_meta("2026-Vilnius-20", "6.21", status="archived"))
    assert errors(repo) == []


def test_unquoted_number_is_rejected(repo):
    path = add(repo, make_meta("2026-Vilnius-20", "6.23"))
    path.write_text(path.read_text().replace("number: '6.23'", "number: 6.23"))
    assert any("write the number in quotes" in e for e in errors(repo))


def test_number_chapter_must_match_category(repo):
    add(repo, make_meta("2026-Vilnius-20", "4.26"))
    assert any("is in chapter 4, but category 'climate-energy' is chapter 6" in e for e in errors(repo))


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("status", "active", "status 'active' is not allowed"),
        ("source", "email", "source 'email' is not allowed"),
        ("adopted", "25.10.2026", "is not a date"),
        ("category", "digital", "category 'digital' does not exist"),
        ("congress", "2026-XXX", "congress '2026-XXX' is not in congresses.yml"),
        ("replaces", "2026-Vilnius-12", "'replaces' must be a list"),
    ],
)
def test_invalid_field_values(repo, field, value, expected):
    add(repo, make_meta("2026-Vilnius-20", "6.23", **{field: value}))
    assert any(expected in e for e in errors(repo))


@pytest.mark.parametrize("field", ["replaces", "amends"])
def test_dangling_list_link(repo, field):
    add(repo, make_meta("2026-Vilnius-20", "6.23", **{field: ["2020-XXX-01"]}))
    assert any(f"'{field}' points to '2020-XXX-01', which does not exist" in e for e in errors(repo))


def test_dangling_superseded_by(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23", superseded_by="2030-XXX-01", status="superseded"))
    assert any("'superseded_by' points to '2030-XXX-01'" in e for e in errors(repo))


def test_empty_body(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23"), body="")
    assert any("text is empty" in e for e in errors(repo))


def test_broken_yaml_header(repo):
    (repo / "resolutions" / "6.23_2026-Vilnius-20.md").write_text("---\nid: [unclosed\n---\ntext\n")
    assert any("not valid YAML" in e for e in errors(repo))


def test_replaces_is_reported_without_fix(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23", replaces=["2026-Vilnius-12"]))
    errs = errors(repo)
    assert any("set superseded_by to 2026-Vilnius-20" in e and "--fix" in e for e in errs)
    assert any("set status to superseded" in e for e in errs)


def test_replaces_is_derived_with_fix(repo):
    body = "Text that must survive.\n\n- item"
    add(repo, make_meta("2026-Vilnius-20", "6.23", replaces=["2026-Vilnius-12"]), body=body)
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert len(report.fixed) == 2
    old = load(repo, "2026-Vilnius-12")
    assert old.meta["status"] == "superseded"
    assert old.meta["superseded_by"] == "2026-Vilnius-20"
    assert load(repo, "2026-Vilnius-20").body == body
    # A second run has nothing left to do.
    second = validate.validate(repo, fix=True)
    assert second.errors == [] and second.fixed == []


def test_manual_superseded_by_adds_replaces_with_fix(repo):
    res = load(repo, "2026-Vilnius-12")
    res.meta.update(superseded_by="2026-Vilnius-18", status="superseded")
    res.save()
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert load(repo, "2026-Vilnius-18").meta["replaces"] == ["2026-Vilnius-12"]


@pytest.mark.parametrize("written", ["'6.21'", "6.21"])  # with quotes, and without (YAML reads a decimal number)
def test_policy_book_number_as_reference_becomes_the_id_with_fix(repo, written):
    path = add(repo, make_meta("2026-Vilnius-20", "6.23", replaces=["PLACEHOLDER"]))
    path.write_text(path.read_text().replace("- PLACEHOLDER", f"- {written}"))
    assert any("write 2026-Vilnius-12 instead of 6.21 in 'replaces'" in e for e in errors(repo))
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert load(repo, "2026-Vilnius-20").meta["replaces"] == ["2026-Vilnius-12"]
    assert load(repo, "2026-Vilnius-12").meta["superseded_by"] == "2026-Vilnius-20"


def test_unknown_number_as_reference(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23", amends=["6.99"]))
    assert any("'amends' points to number 6.99, but no single resolution has that number" in e for e in errors(repo))


def test_adoption_date_outside_the_congress_is_fixed(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23", adopted="2026-11-02"))
    assert any("adopted 2026-11-02 is not within the dates of congress 2026-Vilnius" in e for e in errors(repo))
    assert validate.validate(repo, fix=True).errors == []
    assert str(load(repo, "2026-Vilnius-20").meta["adopted"]) == "2026-10-25"


def test_two_replacers_is_an_error(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23", replaces=["2026-Vilnius-12"]))
    add(repo, make_meta("2026-Vilnius-21", "6.24", replaces=["2026-Vilnius-12"]))
    assert any("replaced by more than one resolution" in e for e in errors(repo, fix=True))


def test_superseded_without_replacer(repo):
    add(repo, make_meta("2026-Vilnius-20", "6.23", status="superseded"))
    assert any("no resolution replaces it" in e for e in errors(repo))


def test_congress_without_dates_is_a_warning(repo):
    (repo / "congresses.yml").write_text("- id: 2026-Vilnius\n  name: X\n  date_start: null\n  date_end: null\n")
    report = validate.validate(repo)
    assert report.errors == []
    assert any("no date_start/date_end" in w for w in report.warnings)


def test_next_number_continues_after_last_number(repo):
    categories = pbook.load_categories(repo)
    resolutions = pbook.load_resolutions(repo)
    assert pbook.next_number(6, categories, resolutions) == "6.23"
    assert pbook.next_number(9, categories, resolutions) == "9.86"


def test_cli_exit_codes(repo):
    script = pbook.ROOT / "scripts" / "validate.py"
    ok = subprocess.run([sys.executable, script, "--root", repo], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout
    assert "OK: 2 resolution(s) checked" in ok.stdout
    add(repo, make_meta("2026-Vilnius-20", "6.23", category="digital"))
    bad = subprocess.run([sys.executable, script, "--root", repo], capture_output=True, text=True)
    assert bad.returncode == 1
    assert "1 problem(s) found" in bad.stdout


# ---------------------------------------------------------------- the status list (resolution_status_list.csv)


def test_status_list_archives_a_resolution_with_fix(repo, make_status_list):
    make_status_list(repo, [("6.21", "Title of 2026-Vilnius-12", "archived"), ("6.22", "", "in force")])
    assert any("the status list marks it as archived" in e for e in errors(repo))
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert load(repo, "2026-Vilnius-12").meta["status"] == "archived"
    assert load(repo, "2026-Vilnius-18").meta["status"] == "in_force"
    assert validate.validate(repo, fix=True).fixed == []  # nothing left to do


def test_status_list_replaced_by_makes_it_superseded(repo, make_status_list):
    make_status_list(repo, [("6.21", "", "archived", "6.22"), ("6.22", "", "in force")])
    assert validate.validate(repo, fix=True).errors == []
    old = load(repo, "2026-Vilnius-12")
    assert old.meta["status"] == "superseded" and old.meta["superseded_by"] == "2026-Vilnius-18"
    assert load(repo, "2026-Vilnius-18").meta["replaces"] == ["2026-Vilnius-12"]


def test_status_list_title_must_match(repo, make_status_list):
    """A row with a title only archives the resolution with that title (e.g. after a renumbering)."""
    make_status_list(repo, [("6.21", "A completely different resolution", "archived")])
    assert any('marked archived with the title "A completely different resolution"' in e for e in errors(repo, fix=True))
    assert load(repo, "2026-Vilnius-12").meta["status"] == "in_force"


def test_status_list_row_already_satisfied_by_an_archived_resolution(repo, make_status_list):
    add(repo, make_meta("2026-Vilnius-20", "6.21", status="archived"))  # the old 6.21; the one in force got the number later
    make_status_list(repo, [("6.21", "", "archived"), ("6.22", "", "in force")])
    assert validate.validate(repo, fix=True).errors == []
    assert load(repo, "2026-Vilnius-12").meta["status"] == "in_force"


def test_status_list_gets_rows_for_new_resolutions(repo, make_status_list):
    import status_list

    make_status_list(repo, [("6.21", "", "in force")])
    assert any("no row yet for 6.22" in w for w in validate.validate(repo).warnings)
    report = validate.validate(repo, fix=True)
    assert any("rows added for 6.22" in f for f in report.fixed)
    rows = status_list.load(repo)
    assert [(r.number, r.title, r.status) for r in rows] == [("6.21", "", "in force"), ("6.22", "Title of 2026-Vilnius-18", "in force")]


def test_status_list_in_force_but_file_archived_is_a_warning(repo, make_status_list):
    path = path_of(repo, "2026-Vilnius-12")
    path.write_text(path.read_text().replace("status: in_force", "status: archived"))
    make_status_list(repo, [("6.21", "", "in force"), ("6.22", "", "in force")])
    report = validate.validate(repo)
    assert report.errors == []
    assert any("is archived, but the status list says 'in force'" in w for w in report.warnings)


def test_status_list_without_column_names_is_an_error(repo):
    (repo / "resolution_status_list.csv").write_text("6.21,in force\n")
    assert any("the first line must be the column names" in e for e in errors(repo))


def test_status_list_wrong_values(repo, make_status_list):
    make_status_list(repo, [("6.21", "", "in force"), ("6.22", "", "abolished"), ("6,23", "", "")])
    errs = errors(repo)
    assert any("line 3 (6.22): status 'abolished' is not allowed" in e for e in errs)
    assert any("line 4: '6,23' is not a Policy Book number" in e for e in errs)


def test_status_list_saved_by_excel_is_repaired(repo):
    """Excel in many European languages saves with semicolons, in the Windows encoding, and drops the 0 of 6.10."""
    text = "Number;Status;Archived at;Replaced by;Title;Notes\r\n6.22;in force;;;Title of 2026-Vilnius-18;Café\r\n6.21;In Force;;;;\r\n6.1;not used;;;;\r\n"
    (repo / "resolution_status_list.csv").write_bytes(text.encode("cp1252"))
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    lines = (repo / "resolution_status_list.csv").read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Number,Status,Archived at,Replaced by,Title,Adopted at,") and lines[0].endswith(",Notes")
    assert [line.split(",")[:2] for line in lines[1:]] == [["6.10", "not used"], ["6.21", "in force"], ["6.22", "in force"]]
    assert lines[3].endswith(",Café")
    assert validate.validate(repo, fix=True).fixed == []  # already in the standard form
