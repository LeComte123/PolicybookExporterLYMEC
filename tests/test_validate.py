import shutil
import subprocess
import sys

import pytest

import pbook
import validate

CONGRESSES = """\
- id: 2026-VNO
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
        "congress": "2026-VNO",
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
    add(tmp_path, make_meta("2026-VNO-12", "6.21"))
    add(tmp_path, make_meta("2026-VNO-18", "6.22"))
    return tmp_path


def add(root, meta, body="LYMEC calls for:\n\n- something", name=None):
    path = root / "resolutions" / f"{name or meta['id']}.md"
    pbook.write_resolution(path, meta, body)
    return path


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
    meta = make_meta("2026-VNO-20", "6.23")
    del meta["title"]
    add(repo, meta)
    assert any("required field 'title'" in e for e in errors(repo))


def test_id_must_match_filename(repo):
    add(repo, make_meta("2026-VNO-20", "6.23"), name="2026-VNO-21")
    assert any("does not match the filename" in e for e in errors(repo))


def test_duplicate_number(repo):
    add(repo, make_meta("2026-VNO-20", "6.21"))
    assert any("number 6.21 is used by both" in e for e in errors(repo))


def test_unquoted_number_is_rejected(repo):
    path = add(repo, make_meta("2026-VNO-20", "6.23"))
    path.write_text(path.read_text().replace("number: '6.23'", "number: 6.23"))
    assert any("write the number in quotes" in e for e in errors(repo))


def test_number_chapter_must_match_category(repo):
    add(repo, make_meta("2026-VNO-20", "4.26"))
    assert any("is in chapter 4, but category 'climate-energy' is chapter 6" in e for e in errors(repo))


@pytest.mark.parametrize(
    "field, value, expected",
    [
        ("status", "active", "status 'active' is not allowed"),
        ("source", "email", "source 'email' is not allowed"),
        ("adopted", "25.10.2026", "is not a date"),
        ("category", "digital", "category 'digital' does not exist"),
        ("congress", "2026-XXX", "congress '2026-XXX' is not in congresses.yml"),
        ("replaces", "2026-VNO-12", "'replaces' must be a list"),
    ],
)
def test_invalid_field_values(repo, field, value, expected):
    add(repo, make_meta("2026-VNO-20", "6.23", **{field: value}))
    assert any(expected in e for e in errors(repo))


@pytest.mark.parametrize("field", ["replaces", "amends"])
def test_dangling_list_link(repo, field):
    add(repo, make_meta("2026-VNO-20", "6.23", **{field: ["2020-XXX-01"]}))
    assert any(f"'{field}' points to '2020-XXX-01', which does not exist" in e for e in errors(repo))


def test_dangling_superseded_by(repo):
    add(repo, make_meta("2026-VNO-20", "6.23", superseded_by="2030-XXX-01", status="superseded"))
    assert any("'superseded_by' points to '2030-XXX-01'" in e for e in errors(repo))


def test_empty_body(repo):
    add(repo, make_meta("2026-VNO-20", "6.23"), body="")
    assert any("text is empty" in e for e in errors(repo))


def test_broken_yaml_header(repo):
    (repo / "resolutions" / "2026-VNO-20.md").write_text("---\nid: [unclosed\n---\ntext\n")
    assert any("not valid YAML" in e for e in errors(repo))


def test_replaces_is_reported_without_fix(repo):
    add(repo, make_meta("2026-VNO-20", "6.23", replaces=["2026-VNO-12"]))
    errs = errors(repo)
    assert any("set superseded_by to 2026-VNO-20" in e and "--fix" in e for e in errs)
    assert any("set status to superseded" in e for e in errs)


def test_replaces_is_derived_with_fix(repo):
    body = "Text that must survive.\n\n- item"
    add(repo, make_meta("2026-VNO-20", "6.23", replaces=["2026-VNO-12"]), body=body)
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert len(report.fixed) == 2
    old = pbook.load_resolution(repo / "resolutions" / "2026-VNO-12.md")
    assert old.meta["status"] == "superseded"
    assert old.meta["superseded_by"] == "2026-VNO-20"
    assert pbook.load_resolution(repo / "resolutions" / "2026-VNO-20.md").body == body
    # A second run has nothing left to do.
    second = validate.validate(repo, fix=True)
    assert second.errors == [] and second.fixed == []


def test_manual_superseded_by_adds_replaces_with_fix(repo):
    path = repo / "resolutions" / "2026-VNO-12.md"
    res = pbook.load_resolution(path)
    res.meta.update(superseded_by="2026-VNO-18", status="superseded")
    res.save()
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert pbook.load_resolution(repo / "resolutions" / "2026-VNO-18.md").meta["replaces"] == ["2026-VNO-12"]


def test_two_replacers_is_an_error(repo):
    add(repo, make_meta("2026-VNO-20", "6.23", replaces=["2026-VNO-12"]))
    add(repo, make_meta("2026-VNO-21", "6.24", replaces=["2026-VNO-12"]))
    assert any("replaced by more than one resolution" in e for e in errors(repo, fix=True))


def test_superseded_without_replacer(repo):
    add(repo, make_meta("2026-VNO-20", "6.23", status="superseded"))
    assert any("no resolution replaces it" in e for e in errors(repo))


def test_congress_without_dates_is_a_warning(repo):
    (repo / "congresses.yml").write_text("- id: 2026-VNO\n  name: X\n  date_start: null\n  date_end: null\n")
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
    add(repo, make_meta("2026-VNO-20", "6.23", category="digital"))
    bad = subprocess.run([sys.executable, script, "--root", repo], capture_output=True, text=True)
    assert bad.returncode == 1
    assert "1 problem(s) found" in bad.stdout
