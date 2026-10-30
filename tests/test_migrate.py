"""The one-time migration of the Vienna 2026 Policy Book (scripts/migrate_vienna.py)."""

import shutil

import pymupdf
import pytest

import migrate_vienna as mv
import pbook
import validate

pytestmark = pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not installed")

# A tiny book in the shape of the Markdown export: the automatic numbers lost their chapter digit
BOOK_MD = """\
# **POLICY BOOK**

33. A Better Response

**Chapter 1 \\- EU Institutions and Institutional Reform**

33. ##  **A Better Response**

Movers: Jeunes MR, Jong VLD Adopted at LYMEC Online Autumn Congress, on 14th November 2020

## **Noting that:**

* One \\- with a dash\\!
* \tA tab after the bullet

●

## **LYMEC calls for:**

* ## **A heading in a list item**

**Chapter 2 – Justice and citizens rights**

## **2.09 A Uniform ID Card (Former 2.14)**

This resolution archives Resolution 2.21 Ending Female Genital Mutilation

       * A list indented like code
       * Second item

##  **– Telecommunication Considering that:**

* Freedom of movement.
"""
MARKS = [("1.33", "A Better Response"), ("2.09", "A Uniform ID Card (Former 2.14)"), ("2.10", "– Telecommunication Considering that:")]


def make_book(root):
    folder = root / "imports" / "migration"
    folder.mkdir(parents=True)
    (folder / f"{mv.BOOK}.md").write_text(BOOK_MD, encoding="utf-8")
    pdf = pymupdf.open()
    for _ in MARKS:
        pdf.new_page()
    pdf.set_toc([[1, "POLICY BOOK", 1]] + [[2, f"{n}​ {t}", i + 1] for i, (n, t) in enumerate(MARKS)])
    pdf.save(folder / f"{mv.BOOK}.pdf")


@pytest.fixture
def repo(tmp_path, make_status_list):
    shutil.copy(pbook.ROOT / "categories.yml", tmp_path / "categories.yml")
    (tmp_path / "congresses.yml").write_text(
        "- id: 2020-Online\n  name: LYMEC Online Autumn Congress 2020\n  city: Online\n  date_start: null\n  date_end: 2020-11-14\n"
        "- id: 2004-Zagreb\n  name: LYMEC Congress 2004\n  city: Zagreb\n  date_start: 2004-04-27\n  date_end: 2004-04-28\n"
    )
    make_book(tmp_path)
    make_status_list(tmp_path, [("1.33", "A Better Response", "in force"), ("2.09", "", "in force"), ("2.10", "Telecommunication", "in force")])
    rows = (tmp_path / "resolution_status_list.csv").read_text().splitlines()
    rows[0] += ",Adopted at"
    rows[1] += ",2020-Online"
    (tmp_path / "resolution_status_list.csv").write_text("\n".join(rows[:2] + [r + "," for r in rows[2:]]) + "\n")
    return tmp_path


def load(root, number):
    (path,) = (root / "resolutions").glob(f"{number}_*.md")
    return pbook.load_resolution(path)


def test_migration_writes_one_file_per_resolution(repo):
    facts = mv.migrate(repo)
    assert facts["written"] == ["1.33", "2.09", "2.10"]
    assert sorted(p.name for p in (repo / "resolutions").iterdir()) == [
        "1.33_PB2026-1.33.md", "2.09_PB2026-2.09.md", "2.10_PB2026-2.10.md"
    ]  # fmt: skip
    first = load(repo, "1.33")
    assert first.meta["title"] == "A Better Response" and first.meta["category"] == "institutions"
    assert first.meta["congress"] == "2020-Online" and str(first.meta["adopted"]) == "2020-11-14"
    assert first.meta["source"] == "migration" and first.meta["id"] == "PB2026-1.33"
    assert load(repo, "2.09").meta["title"] == "A Uniform ID Card (Former 2.14)"  # number in the heading removed
    assert load(repo, "2.09").meta["congress"] is None  # not known: left empty
    assert facts["no_congress"] == ["2.09", "2.10"]
    assert facts["statements"] == [("2.09", "archives", "2.21", False)]
    assert validate.validate(repo).errors == []


def test_text_is_kept_word_for_word_with_cleaned_structure(repo):
    mv.migrate(repo)
    res = load(repo, "1.33")
    body = res.body
    assert res.meta["submitted_by"] == ["Jeunes MR", "Jong VLD"]  # moved into the header ...
    assert body.startswith("Adopted at LYMEC Online Autumn Congress")  # ... the adoption line stays in the text
    assert "**Noting that:**" in body and "##" not in body  # sub-headings become bold paragraphs
    assert "* One \\- with a dash\\!" in body
    assert "* A tab after the bullet" in body  # a tab after a bullet would make Markdown show code
    assert "●" not in body and "Chapter 2" not in body
    assert "* **A heading in a list item**" in body
    assert "\n* A list indented like code\n* Second item" in load(repo, "2.09").body


def test_subheading_stuck_to_the_title_moves_into_the_text(repo):
    mv.migrate(repo)
    res = load(repo, "2.10")
    assert res.meta["title"] == "Telecommunication"  # the dash between number and title is gone too
    assert res.body.startswith("**Considering that:**\n\n* Freedom of movement.")


def test_migration_runs_once_unless_forced(repo):
    mv.migrate(repo)
    with pytest.raises(mv.MigrationError, match="exist already"):
        mv.migrate(repo)
    assert mv.migrate(repo, force=True)["written"] == ["1.33", "2.09", "2.10"]


def test_report(repo):
    assert mv.main(["--root", str(repo)]) == 0
    report = (repo / "imports" / "migration" / "migration_report.md").read_text()
    assert "3 resolutions written" in report and "## Congress not known yet (2)" in report
    assert "- 2.09 archives 2.21" in report and "Numbers and titles agree." in report


def test_congress_entered_later_in_the_status_list_is_filled_in(repo):
    mv.migrate(repo)
    path = repo / "resolution_status_list.csv"
    path.write_text(path.read_text().replace("2.09,in force,,,,", "2.09,in force,,,,2004-Zagreb"))
    report = validate.validate(repo, fix=True)
    assert report.errors == []
    res = load(repo, "2.09")
    assert res.meta["congress"] == "2004-Zagreb" and str(res.meta["adopted"]) == "2004-04-28"


@pytest.mark.parametrize(
    "body, submitted, cosigned, text",
    [
        # two labels in one paragraph, the adoption line after the list stays
        ("Submitted by: Jonge Democraten Co-signatories: JNC, Centerstudenter. Adopted at the Autumn Congress 2022\n\nText.",
         ["Jonge Democraten"], ["JNC", "Centerstudenter"], "Adopted at the Autumn Congress 2022\n\nText."),
        # a list cut into two paragraphs by the export
        ("Movers: Working Group, Radikal Ungdom, Joventut\n\nNacionalista de Catalunya, JOVD\n\n**Noting that:**",
         ["Working Group", "Radikal Ungdom", "Joventut Nacionalista de Catalunya", "JOVD"], [], "**Noting that:**"),
        # names of individual members: the list stays in the text, the organisations' list is still moved
        ("Submitted by: Silvia Fernández (IMS), LHG Co-signed by: JOVD, Alliance Youth\n\nText.",
         [], ["JOVD", "Alliance Youth"], "Submitted by: Silvia Fernández (IMS), LHG\n\nText."),
        # "By ..." at the start of an ordinary sentence is not a label
        ("By legalising surrogacy, we can remove the legal limbo.", [], [], "By legalising surrogacy, we can remove the legal limbo."),
        # a note in brackets, or text, before the label
        ("(Former 9.70 Prior to Riga) Submitted by: European Youth of Ukraine\n\nUpdated in 2021 Submitted by: LHG",
         ["European Youth of Ukraine", "LHG"], [], "(Former 9.70 Prior to Riga)\n\nUpdated in 2021"),
        # "&" before a note, connecting "and"
        ("**Movers:** IMS Delegates & Outcome of the IMS Working Group\n\nSubmitters: LUF (Sweden) and JOVD, and Radikal Ungdom",
         ["IMS Delegates", "LUF (Sweden) and JOVD", "Radikal Ungdom"], [], "& Outcome of the IMS Working Group"),
    ],
)
def test_move_submitters(body, submitted, cosigned, text):
    sub, cos, new_body, removed, kept = mv.move_submitters(body)
    assert (sub, cos, new_body) == (submitted, cosigned, text)
