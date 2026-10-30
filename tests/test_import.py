import csv
import datetime as dt
import re
import shutil

import pytest

import import_openslides as imp
import pbook
import validate
from openslides_pdf import parse_pdf

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")

FIXTURES = pbook.ROOT / "tests" / "fixtures"
CLEAN = {"05": "3.32", "06": "4.26", "11": "1.40", "16": "4.27", "20": "4.28"}  # motion -> Policy Book number
CONFLICTED = ["08", "12", "18"]
HEADER = ["Number", "Submitters", "Title", "Text", "Category", "State"]
CONGRESSES = """\
# congress list (this comment must survive a stub being added)
- id: 2026-Vilnius
  name: LYMEC Autumn Congress 2026
  city: Vilnius
  date_start: 2026-10-23
  date_end: 2026-10-25
"""


@pytest.fixture
def repo(tmp_path):
    shutil.copy(pbook.ROOT / "categories.yml", tmp_path / "categories.yml")
    (tmp_path / "congresses.yml").write_text(CONGRESSES, encoding="utf-8")
    (tmp_path / "imports").mkdir()
    return tmp_path


def upload(repo, fixture, name):
    target = repo / "imports" / name
    shutil.copy(FIXTURES / fixture, target)
    return target


def write_csv(path, rows, sep=";", encoding="utf-8"):
    with open(path, "w", encoding=encoding, newline="") as f:
        csv.writer(f, delimiter=sep, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows([HEADER] + rows)
    return path


def motion_row(number, text, title="1. A Title", category="Chapter 6 - Climate action and Energy", state="accepted"):
    return [number, "Anna Example (JOVD · No. In person)", title, text, category, state]


def written(repo):
    """Ids of the resolution files (<number>_<id>.md)."""
    return sorted(p.stem.split("_", 1)[1] for p in (repo / "resolutions").glob("*.md"))


def path_of(repo, rid):
    (path,) = (repo / "resolutions").glob(f"*_{rid}.md")
    return path


def load(repo, rid):
    return pbook.load_resolution(path_of(repo, rid))


# ---------------------------------------------------------------- real OpenSlides export


def test_real_export_imports_clean_motions_and_skips_conflicts(repo):
    upload(repo, "2026-Vilnius.csv", "2026-Vilnius.csv")
    upload(repo, "2026-Vilnius.pdf", "2026-Vilnius.pdf")
    plans = imp.run([], repo)

    assert written(repo) == [f"2026-Vilnius-{n}" for n in CLEAN]
    files = sorted(p.name for p in (repo / "resolutions").iterdir())
    assert files == sorted(f"{number}_2026-Vilnius-{n}.md" for n, number in CLEAN.items())  # 1.40_2026-Vilnius-11.md, ...
    assert {load(repo, f"2026-Vilnius-{n}").meta["number"] for n in CLEAN} == set(CLEAN.values())
    assert [load(repo, f"2026-Vilnius-{n}").meta["number"] for n in CLEAN] == list(CLEAN.values())
    assert len(plans[0].errors) == len(CONFLICTED)  # the conflicts themselves are tested with colliding.csv
    assert validate.validate(repo).errors == []
    assert (repo / "dist" / "import-report.md").exists()
    # the uploads are kept in imported/, and the inbox is empty again
    assert sorted(p.name for p in (repo / "imported").iterdir()) == ["2026-Vilnius.csv", "2026-Vilnius.pdf"]
    assert list((repo / "imports").iterdir()) == []


def test_real_export_text_and_metadata(repo):
    upload(repo, "2026-Vilnius.csv", "2026-Vilnius.csv")
    imp.run([], repo)
    rows = {m.number: m for m in imp.read_csv(FIXTURES / "2026-Vilnius.csv")}
    for number in CLEAN:
        res = load(repo, f"2026-Vilnius-{number}")
        source = rows[number]
        assert res.meta["title"] == re.sub(r"^\d+\.\s+", "", source.title)
        assert res.meta["adopted"] == dt.date(2026, 10, 25)
        assert res.meta["openslides_number"] == number
        assert res.meta["source"] == "openslides" and res.meta["status"] == "in_force"
        list_items = len(re.findall(r"^- ", res.body, flags=re.M))
        assert list_items == source.html.count("<li>"), number
        assert "&nbsp;" not in res.body and "\xa0" not in res.body
        assert not re.search(r"</?\w+[^>]*>", res.body), "no HTML left"
        assert not re.search(r"^\\$", res.body, flags=re.M), "no empty line breaks left"
        assert not re.match(r"(co-)?(submitted|signed)", res.body, flags=re.I)
    # "Chatper 3 - …" (typo in OpenSlides) still lands in chapter 3
    assert load(repo, "2026-Vilnius-05").meta["category"] == "culture-education"


def test_real_export_submitters(repo):
    upload(repo, "2026-Vilnius.csv", "2026-Vilnius.csv")
    imp.run([], repo)
    meta = {n: load(repo, f"2026-Vilnius-{n}").meta for n in CLEAN}
    assert meta["05"]["submitted_by"] == ["Radikal Ungdom", "Svensk Ungdom", "LUF", "Joventut Nacionalista de Catalunya"]
    assert len(meta["05"]["co_signers"]) == 10
    assert meta["06"]["submitted_by"] == ["Nowoczesna Youth (Poland)"]
    assert meta["06"]["co_signers"][-1] == "Lithuanian Liberal Youth (Lithuania)"
    assert meta["11"]["submitted_by"] == ["USR Tineret"] and meta["11"]["co_signers"] == []
    assert meta["16"]["submitted_by"] == ["Julis"]  # no "Submitted by" line: organisation of the OpenSlides submitter
    assert meta["16"]["co_signers"][0] == "Svensk Ungdom (SU)"
    assert meta["20"]["co_signers"][0] == "Joventut Nacionalista de Catalunya"  # "Co-signed:" without "by"


def test_pdf_numbers_and_titles_match_csv():
    csv_motions = imp.read_csv(FIXTURES / "2026-Vilnius.csv")
    pdf_motions = imp.read_pdf(FIXTURES / "2026-Vilnius.pdf")
    assert [m.number for m in pdf_motions] == [m.number for m in csv_motions]
    imp.cross_check(csv_motions, pdf_motions, "2026-Vilnius.csv", "2026-Vilnius.pdf")  # does not raise
    assert [bool(m.repeated_line) for m in parse_pdf(FIXTURES / "2026-Vilnius.pdf")] == [
        m.number in CONFLICTED for m in csv_motions
    ]


def test_pdf_only_text_matches_csv_text(tmp_path, repo):
    upload(repo, "2026-Vilnius.csv", "2026-Vilnius.csv")
    imp.run([], repo)
    pdf_repo = tmp_path / "pdf"
    shutil.copytree(repo, pdf_repo, ignore=shutil.ignore_patterns("resolutions", "dist", "imported"))
    upload(pdf_repo, "2026-Vilnius.pdf", "2026-Vilnius.pdf")
    plans = imp.run([], pdf_repo)

    assert written(pdf_repo) == written(repo)
    for number in CLEAN:
        from_csv, from_pdf = load(repo, f"2026-Vilnius-{number}"), load(pdf_repo, f"2026-Vilnius-{number}")
        assert from_csv.meta == from_pdf.meta, number
        assert from_csv.body == from_pdf.body, number  # lists, paragraphs and escapes come out identical
    errors = plans[0].errors
    assert len(errors) == len(CONFLICTED) and all("appear twice in the PDF" in e for e in errors)


# ---------------------------------------------------------------- colliding amendments


def test_colliding_amendments_are_reported_and_not_imported(repo):
    upload(repo, "colliding.csv", "2026-Vilnius.csv")
    plans = imp.run([], repo)

    assert written(repo) == ["2026-Vilnius-01"]
    assert plans[0].errors == [
        "Motion 02 was not imported: colliding amendments 02-Ä01, 02-Ä02. Several amendments change the same line. "
        "Open the motion in OpenSlides, create a modified final version that resolves them, and export again."
    ]
    issue = (repo / "dist" / "import-errors.md").read_text()
    assert issue.startswith("# Import incomplete: 2026-Vilnius.csv\n\n")  # first line = title of the GitHub issue
    assert "02-Ä01, 02-Ä02" in issue and "What to do:" in issue
    assert "## Import errors (1)" in (repo / "dist" / "import-report.md").read_text()
    clean = load(repo, "2026-Vilnius-01")
    assert clean.meta["submitted_by"] == ["JOVD"] and clean.meta["co_signers"] == ["LHG", "Julis"]
    assert clean.meta["category"] == "justice"


def test_fixed_reupload_imports_the_skipped_motion(repo):
    path = upload(repo, "colliding.csv", "2026-Vilnius.csv")
    imp.run([], repo)
    first = path_of(repo, "2026-Vilnius-01").read_text()
    # The Secretariat resolves the conflict in OpenSlides and uploads a new export under the same name.
    write_csv(path, [
        motion_row("01", "<p>Submitted by: JOVD</p><p>Changed text.</p>", "1. A Clean Motion", "Chapter 2 - Justice"),
        motion_row("02", "<p>Submitted by: LHG</p><p>Resolved text.</p>", "2. Fixed", "Chapter 2 - Justice"),
    ])
    plans = imp.run([], repo)

    assert written(repo) == ["2026-Vilnius-01", "2026-Vilnius-02"]
    assert path_of(repo, "2026-Vilnius-01").read_text() == first  # never overwritten without --force
    assert sorted(p.name for p in (repo / "imported").iterdir()) == ["2026-Vilnius.csv", "2026-Vilnius_2.csv"]
    assert any("already in the database" in s for s in plans[0].skipped)
    assert plans[0].errors == []


# ---------------------------------------------------------------- re-imports


def test_same_file_twice_is_a_no_op(repo):
    upload(repo, "colliding.csv", "2026-Vilnius.csv")
    imp.run([], repo)
    before = {p.name: p.read_text() for p in (repo / "resolutions").glob("*.md")}
    assert imp.run([], repo) == []  # empty inbox
    upload(repo, "colliding.csv", "2026-Vilnius.csv")
    assert imp.run([], repo) == []  # same content as imported/2026-Vilnius.csv
    assert imp.run([repo / "imported" / "2026-Vilnius.csv"], repo) == []  # explicit path, same content
    assert {p.name: p.read_text() for p in (repo / "resolutions").glob("*.md")} == before
    assert list((repo / "imports").iterdir()) == []
    assert [p.name for p in (repo / "imported").iterdir()] == ["2026-Vilnius.csv"]


def test_force_overwrites_text_but_keeps_number_and_status(repo):
    path = write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("01", "<p>Old text.</p>")])
    imp.run([], repo)
    res = load(repo, "2026-Vilnius-01")
    res.meta["status"] = "archived"
    res.save()
    write_csv(path, [motion_row("01", "<p>New text.</p>")])
    imp.run([], repo, force=True)
    res = load(repo, "2026-Vilnius-01")
    assert res.body.strip() == "New text."
    assert res.meta["status"] == "archived" and res.meta["number"] == "6.21"
    write_csv(path, [motion_row("01", "<p>New text.</p>", category="Chapter 4 - Business")])
    imp.run([], repo, force=True)
    # moved chapter: new number there, and the file is renamed
    assert [p.name for p in (repo / "resolutions").iterdir()] == ["4.26_2026-Vilnius-01.md"]


# ---------------------------------------------------------------- CSV variants and file problems


@pytest.mark.parametrize("sep, encoding", [(";", "utf-8"), (",", "utf-8-sig"), ("\t", "utf-8"), (";", "cp1252")])
def test_csv_separator_and_encoding_variants(repo, sep, encoding):
    text = "<p>Co-signed by: Centerpartiets Ungdomsförbund, JOVD</p><p>Text with “quotes” – and ü.</p>"
    write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("3", text, "3. Grüße")], sep=sep, encoding=encoding)
    imp.run([], repo)
    res = load(repo, "2026-Vilnius-03")
    assert res.meta["title"] == "Grüße"
    assert res.meta["co_signers"] == ["Centerpartiets Ungdomsförbund", "JOVD"]
    assert res.body.strip() == "Text with “quotes” – and ü."


def test_column_names_are_case_insensitive(repo):
    path = write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("01", "<p>Text.</p>")])
    path.write_text(path.read_text().replace('"Number";"Submitters";"Title"', '"NUMBER";"submitters";"title"'))
    imp.run([], repo)
    assert written(repo) == ["2026-Vilnius-01"]


def test_wrong_file_name_fails_with_rename_hint(repo):
    upload(repo, "colliding.csv", "Motions.csv")
    with pytest.raises(imp.ImportFailure, match="Rename the file"):
        imp.run([], repo)
    assert not (repo / "resolutions").exists()


def test_failed_import_writes_the_issue_text(repo):
    upload(repo, "colliding.csv", "Motions.csv")
    assert imp.main(["--root", str(repo)]) == 1
    issue = (repo / "dist" / "import-failed.md").read_text()
    assert issue.startswith("# Import failed: Motions.csv\n\n")
    assert "Rename the file" in issue and "Nothing was imported or published" in issue
    (repo / "imports" / "Motions.csv").rename(repo / "imports" / "2026-Vilnius.csv")
    assert imp.main(["--root", str(repo)]) == 0
    assert not (repo / "dist" / "import-failed.md").exists()


def test_other_files_in_imports_are_left_alone(repo):
    (repo / "imports" / "migration").mkdir()
    for name in ("migration/Policy Book Vienna.pdf", "resolution_status_list.xlsx", ".gitkeep"):
        (repo / "imports" / name).write_text("not an OpenSlides export")
    assert imp.main(["--root", str(repo)]) == 0  # nothing to import
    upload(repo, "colliding.csv", "2026-Vilnius.csv")
    imp.run([], repo)
    assert written(repo) == ["2026-Vilnius-01"]
    left = sorted(str(p.relative_to(repo / "imports")) for p in (repo / "imports").rglob("*") if p.is_file())
    assert left == [".gitkeep", "migration/Policy Book Vienna.pdf", "resolution_status_list.xlsx"]


def test_missing_column_fails(repo):
    path = write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("01", "<p>Text.</p>")])
    path.write_text(path.read_text().replace('"State"', '"Status"'))
    with pytest.raises(imp.ImportFailure, match="no column 'State'"):
        imp.run([], repo)


def test_csv_and_pdf_that_disagree_fail_and_write_nothing(repo):
    path = upload(repo, "2026-Vilnius.csv", "2026-Vilnius.csv")
    upload(repo, "2026-Vilnius.pdf", "2026-Vilnius.pdf")
    path.write_text(path.read_text(encoding="utf-8").replace("Let Bulls Smell Flowers", "Let Cows Smell Flowers"), encoding="utf-8")
    with pytest.raises(imp.ImportFailure, match="do not match: motion 05 is called"):
        imp.run([], repo)
    assert not (repo / "resolutions").exists()
    assert sorted(p.name for p in (repo / "imports").iterdir()) == ["2026-Vilnius.csv", "2026-Vilnius.pdf"]  # stays in the inbox
    assert not (repo / "imported").exists()


def test_rejected_motions_and_amendments_are_skipped(repo):
    write_csv(
        repo / "imports" / "2026-Vilnius.csv",
        [
            motion_row("01", "<p>Text.</p>", state="rejected"),
            motion_row("01-Ä01", "<p>Text.</p>", title="01-Ä01: Amendment to 01"),
            motion_row("02", "<p>Text.</p>", state="Accepted"),
            motion_row("03", "<p>Text.</p>", title="Amendment of Article 7 TEU"),  # a real motion, not an amendment
        ],
    )
    plans = imp.run([], repo)
    assert written(repo) == ["2026-Vilnius-02", "2026-Vilnius-03"]
    assert len(plans[0].skipped) == 2


# ---------------------------------------------------------------- numbers, replaces, congresses


def add_existing(repo, rid, number, title, **extra):
    meta = {
        "id": rid, "number": number, "title": title, "congress": "2026-Vilnius", "adopted": dt.date(2026, 10, 25),
        "category": "climate-energy", "status": "in_force", "replaces": [], "superseded_by": None, "amends": [],
        "submitted_by": [], "co_signers": [], "source": "migration",
    }
    meta.update(extra)
    pbook.write_resolution(pbook.resolution_path(repo, meta), meta, "Old text.")


def test_replaces_confirmed_by_the_status_list(repo, make_status_list):
    add_existing(repo, "2026-Vilnius-90", "6.03", "The future is Nuclear")
    make_status_list(repo, [("6.03", "The future is Nuclear", "archived", None, "2026-Vilnius")])
    text = "<p>Considering that:</p><ul><li>Atoms.</li></ul><p>This motion replaces: 6.03 The future is Nuclear</p>"
    write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("18", text)])
    plans = imp.run([], repo)
    assert load(repo, "2026-Vilnius-18").meta["replaces"] == ["2026-Vilnius-90"]  # statement at the end of the text
    assert "This motion replaces: 6.03" in load(repo, "2026-Vilnius-18").body  # the text itself is unchanged
    assert any("replaces 6.03 The future is Nuclear" in r and "confirmed by the status list" in r for r in plans[0].replacements)
    assert plans[0].unconfirmed == [] and not (repo / "dist" / "status-check.md").exists()
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    assert load(repo, "2026-Vilnius-90").meta["status"] == "superseded"


def test_statement_not_in_the_status_list_is_only_reported(repo, make_status_list):
    add_existing(repo, "2026-Vilnius-90", "6.03", "The future is Nuclear")
    make_status_list(repo, [("6.03", "The future is Nuclear", "in force")])
    text = "<p>This motion replaces: 6.03 The future is Nuclear</p><p>Considering that:</p><ul><li>Atoms.</li></ul>"
    write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("18", text)])
    plans = imp.run([], repo)
    assert load(repo, "2026-Vilnius-18").meta["replaces"] == []
    assert validate.validate(repo, fix=True).errors == []
    assert load(repo, "2026-Vilnius-90").meta["status"] == "in_force"
    issue = (repo / "dist" / "status-check.md").read_text()
    assert issue.startswith("# Please check the status list: 2026-Vilnius\n")
    assert "set 6.03 to 'archived' in resolution_status_list.xlsx and write 6.21 under 'Replaced by'" in issue
    assert len(plans[0].unconfirmed) == 1


def test_archives_statement_and_status_list(repo, make_status_list):
    add_existing(repo, "2026-Vilnius-90", "6.02", "Old one")
    add_existing(repo, "2026-Vilnius-91", "6.05", "Other old one")
    make_status_list(repo, [("6.02", "", "archived", None, "2026-Vilnius"), ("6.05", "", "archived", None, "2026-Vilnius")])
    write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("19", "<p>Archiving Resolutions 6.02, 6.09</p><p>Text.</p>")])
    plans = imp.run([], repo)
    assert any("archives 6.02 Old one" in r for r in plans[0].replacements)
    assert any("6.09 is not in the database yet" in r for r in plans[0].replacements)
    # the other direction: archived in the list at this congress, not named by any motion
    assert any("marks 6.05 as archived at 2026-Vilnius, but no motion of this upload mentions it" in n for n in plans[0].notes)
    assert validate.validate(repo, fix=True).errors == []
    assert load(repo, "2026-Vilnius-90").meta["status"] == "archived"
    assert load(repo, "2026-Vilnius-91").meta["status"] == "archived"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("This motion replaces: 6.03 The future is Nuclear", [("replaces", "6.03")]),
        ("**This resolution replaces** 4.10 and 4.12.", [("replaces", "4.10"), ("replaces", "4.12")]),
        ("Replaces: 9.05", [("replaces", "9.05")]),
        ("This resolution archives Resolution 2.21 Aiming at Ending Female Genital Mutilation", [("archives", "2.21")]),
        ("Archiving Resolutions 6.02, 6.03, 6.05, 6.09", [("archives", n) for n in ("6.02", "6.03", "6.05", "6.09")]),
        ("This motion repeals 3.10", [("archives", "3.10")]),
        ("- Replacing coal with 2.50 GW of wind power", []),  # numbers in ordinary sentences are ignored
        ("Considering that replacing 3.20 billion euros of subsidies is hard", []),
    ],
)
def test_statements(text, expected):
    assert imp.statements(f"Considering that:\n\n{text}\n\nLYMEC calls for:") == expected


def test_replaced_number_not_in_database_is_reported(repo):
    write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("18", "<p>This motion replaces: 6.03 The future is Nuclear</p>")])
    plans = imp.run([], repo)
    assert load(repo, "2026-Vilnius-18").meta["replaces"] == []
    assert any("6.03 is not in the database yet" in r for r in plans[0].replacements)


def test_similar_title_is_suggested(repo):
    add_existing(repo, "2010-Sofia-01", "6.15", "Create a European market for Carbon Capture and Storage", congress="2010-Sofia")
    (repo / "congresses.yml").write_text(CONGRESSES + "- id: 2010-Sofia\n  name: Old\n  date_start: 2010-01-01\n  date_end: 2010-01-02\n")
    write_csv(repo / "imports" / "2026-Vilnius.csv", [motion_row("07", "<p>Text.</p>", "7. A European Market for Carbon Capture and Storage")])
    plans = imp.run([], repo)
    assert any("may replace 6.15" in r and "2010-Sofia-01" in r for r in plans[0].replacements)


def test_numbers_continue_in_motion_order(repo):
    add_existing(repo, "2026-Vilnius-90", "6.30", "Existing")
    rows = [motion_row("12", "<p>B.</p>"), motion_row("3", "<p>A.</p>")]
    write_csv(repo / "imports" / "2026-Vilnius.csv", rows)
    imp.run([], repo)
    assert load(repo, "2026-Vilnius-03").meta["number"] == "6.31"
    assert load(repo, "2026-Vilnius-12").meta["number"] == "6.32"


def test_new_congress_gets_a_stub_entry(repo):
    write_csv(repo / "imports" / "2027-kraków.csv", [motion_row("01", "<p>Text.</p>")])  # typed in lower case, with accent
    plans = imp.run([], repo, today=dt.date(2027, 5, 2))
    congresses = pbook.load_congresses(repo)
    assert congresses[-1] == {
        "id": "2027-Krakow", "name": "LYMEC Congress 2027", "city": "Krakow",
        "date_start": dt.date(2027, 5, 2), "date_end": dt.date(2027, 5, 2),
    }
    assert (repo / "congresses.yml").read_text().startswith("# congress list")
    assert "&id" not in (repo / "congresses.yml").read_text()  # plain dates, no YAML aliases
    assert load(repo, "2027-Krakow-01").meta["adopted"] == dt.date(2027, 5, 2)
    assert any("Correct the name and the dates" in n for n in plans[0].notes)
    issue = (repo / "dist" / "new-congress.md").read_text()
    assert issue.startswith("# New congress: 2027-Krakow\n") and "id: 2027-Krakow" in issue
    assert validate.validate(repo).errors == []

    # The Secretariat enters the real dates: the adoption date follows with validate --fix.
    text = (repo / "congresses.yml").read_text()
    (repo / "congresses.yml").write_text(text.replace("date_start: 2027-05-02", "date_start: 2027-04-25").replace(
        "date_end: 2027-05-02", "date_end: 2027-04-27"))
    assert any("not within the dates of congress 2027-Krakow" in e for e in validate.validate(repo).errors)
    assert validate.validate(repo, fix=True).errors == []
    assert load(repo, "2027-Krakow-01").meta["adopted"] == dt.date(2027, 4, 27)


def test_file_name_matches_a_known_congress_in_any_spelling(repo):
    write_csv(repo / "imports" / "2026-VILNIUS.csv", [motion_row("01", "<p>Text.</p>")])
    plans = imp.run([], repo)
    assert written(repo) == ["2026-Vilnius-01"] and plans[0].congress_stub is None
    assert not (repo / "dist" / "new-congress.md").exists()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("A, B (Poland), C", ["A", "B (Poland)", "C"]),
        ("Momentum TizenX , LHG, ", ["Momentum TizenX", "LHG"]),
        ("X (a, b); Y", ["X (a, b)", "Y"]),
    ],
)
def test_split_orgs(text, expected):
    assert imp.split_orgs(text) == expected


def test_orgs_from_submitters_never_keeps_names():
    assert imp.orgs_from_submitters("Ben Jokinen (SU · No. Online), Ann Lee (JOVD)") == ["SU", "JOVD"]
    assert imp.orgs_from_submitters("Ben Jokinen") == []
