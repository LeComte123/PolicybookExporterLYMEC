import datetime as dt
import re
import shutil
import zipfile

import pymupdf
import pytest

import build
import import_openslides as imp
import pbook
import validate

pytestmark = pytest.mark.skipif(
    shutil.which("pandoc") is None or shutil.which("xelatex") is None, reason="pandoc and xelatex are needed to build"
)

CLEAN = ["3.32_2026-Vilnius-05", "4.26_2026-Vilnius-06", "1.40_2026-Vilnius-11", "4.27_2026-Vilnius-16", "4.28_2026-Vilnius-20"]
OLD_ID = "2019-Brussels-90"
OLD_FILE = f"4.05_{OLD_ID}"
OLD_TITLE = 'Bank Reform: 100% "Fair" & #Open_Markets for $5 {now}'  # characters LaTeX and Markdown treat specially
BUILD_DATE = dt.date(2026, 10, 29)


def make_repo(root):
    shutil.copy(pbook.ROOT / "categories.yml", root / "categories.yml")
    (root / "congresses.yml").write_text(
        "- id: 2019-Brussels\n  name: LYMEC Spring Congress 2019\n  city: Brussels\n  date_start: 2019-04-06\n  date_end: 2019-04-06\n"
        "- id: 2026-Vilnius\n  name: LYMEC Autumn Congress 2026\n  city: Vilnius\n  date_start: 2026-10-23\n  date_end: 2026-10-25\n"
    )
    for name in ("templates", "docs"):
        (root / name).symlink_to(pbook.ROOT / name)
    (root / "imports").mkdir()
    for suffix in (".csv", ".pdf"):
        shutil.copy(pbook.ROOT / "tests" / "fixtures" / f"2026-Vilnius{suffix}", root / "imports")
    imp.run([], root)
    meta = {
        "id": OLD_ID, "number": "4.05", "title": OLD_TITLE, "congress": "2019-Brussels", "adopted": dt.date(2019, 4, 6),
        "category": "economy", "status": "in_force", "replaces": [], "superseded_by": "2026-Vilnius-16", "amends": [],
        "submitted_by": ["JOVD_NL & Co"], "co_signers": [], "source": "migration",
    }  # fmt: skip
    pbook.write_resolution(pbook.resolution_path(root, meta), meta, "Old text with **bold**.\n\n- one\n- two")
    assert validate.validate(root, fix=True).errors == []  # marks it superseded by 2026-Vilnius-16


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("repo")
    make_repo(root)
    build.run(root, build_date=BUILD_DATE)
    return root


def text_of(path):
    return "\n".join(page.get_text() for page in pymupdf.open(path))


def flat(text):
    return " ".join(text.split())


def test_all_files_are_built(built):
    for stem in CLEAN + [OLD_FILE]:  # downloads are named like the resolution files
        for ext in ("pdf", "docx"):
            assert (built / "dist" / "resolutions" / f"{stem}.{ext}").stat().st_size > 1000
            assert (built / "site" / "resolutions" / f"{stem}.{ext}").exists()
    for name in ("LYMEC_Policy_Book.pdf", "LYMEC_Policy_Book_Archive.pdf"):
        assert (built / "dist" / name).exists() and (built / "site" / name).exists()
    assert (built / "site" / "index.html").exists() and (built / "site" / "lymec_logo.png").exists()


def resolution_entries(path):
    return [title for level, title, _ in pymupdf.open(path).get_toc() if level == 2]


def test_current_edition_has_only_resolutions_in_force(built):
    entries = resolution_entries(built / "dist" / "LYMEC_Policy_Book.pdf")
    assert entries == [
        "1.40 No Union without values: defending the Rule of Law in the EU",
        "3.32 Let Bulls Smell Flowers, Not Blood: A Call to Ban Bullfighting",
        "4.26 Together We Spend, United We Ball - More EU Defense Joint Venture Projects!",
        "4.27 Deregulate to Liberate: Making Life Affordable Again in Europe",
        "4.28 Making Europe a Global Leader in Pharmaceutical Innovation",
    ]
    toc = pymupdf.open(built / "dist" / "LYMEC_Policy_Book.pdf").get_toc()
    assert [t for level, t, _ in toc if level == 1][-1] == "Index by congress"
    text = flat(text_of(built / "dist" / "LYMEC_Policy_Book.pdf"))
    assert "Last updated: 29 October 2026" in text and "2026" in text
    assert "Bank Reform" not in text.split("Index by congress")[0].split("Contents")[0]  # not on the cover


def test_archive_edition_has_everything_with_a_banner(built):
    path = built / "dist" / "LYMEC_Policy_Book_Archive.pdf"
    entries = resolution_entries(path)
    assert len(entries) == 6
    assert f"4.05 {OLD_TITLE}" in entries  # special characters survive Markdown and LaTeX
    text = flat(text_of(path))
    assert "Superseded by 4.27 Deregulate to Liberate: Making Life Affordable Again in Europe." in text
    assert f"Replaces: 4.05 {OLD_TITLE}" in text
    assert "(superseded)" in text  # marked in the index by congress
    # text is printed exactly as written: no TeX quote/dash substitutions
    assert "Chapter 4 – Business" in text and "Europe’s defence" in text and "industry's public subsidy" in text


def test_index_by_congress_has_correct_pages(built):
    doc = pymupdf.open(built / "dist" / "LYMEC_Policy_Book_Archive.pdf")
    toc_pages = {title.split()[0]: page for level, title, page in doc.get_toc() if level == 2}
    index_page = [page for level, title, page in doc.get_toc() if title == "Index by congress"][0]
    lines = "\n".join(doc[p].get_text() for p in range(index_page - 1, doc.page_count)).splitlines()
    assert lines.index("LYMEC Spring Congress 2019, Brussels (6 April 2019)") < lines.index(
        "LYMEC Autumn Congress 2026, Vilnius (25 October 2026)"
    )
    index_pages, number = {}, None
    for line in lines:  # an entry may wrap; its page number ends the last line, after the dots
        if match := re.match(r"^(\d\.\d{2,3}) ", line):
            number = match.group(1)
        if number and (match := re.search(r"\.\s*(\d+)$", line)):
            index_pages[number], number = int(match.group(1)), None
    assert index_pages == toc_pages


def test_single_resolution_pdf(built):
    text = flat(text_of(built / "dist" / "resolutions" / "4.27_2026-Vilnius-16.pdf"))
    assert "4.27 Deregulate to Liberate: Making Life Affordable Again in Europe" in text
    assert "Adopted LYMEC Autumn Congress 2026, Vilnius, 25 October 2026" in text
    assert "Submitted by Julis" in text and "Co-signers Svensk Ungdom (SU)" in text
    assert "Reference 2026-Vilnius-16" in text
    old = flat(text_of(built / "dist" / "resolutions" / f"{OLD_FILE}.pdf"))
    assert "Superseded by 4.27" in old and "Status Superseded" in old and "JOVD_NL & Co" in old


def test_single_resolution_docx(built):
    with zipfile.ZipFile(built / "dist" / "resolutions" / "4.27_2026-Vilnius-16.docx") as docx:
        xml = docx.read("word/document.xml").decode()
        assert any(name.startswith("word/media/") for name in docx.namelist())  # the logo
    assert "Deregulate to Liberate" in xml and 'w:val="Heading1"' in xml
    assert "Svensk Ungdom (SU)" in xml and "<w:tbl>" in xml


def test_download_page(built):
    page = (built / "site" / "index.html").read_text()
    assert "Last updated: 29 October 2026" in page
    assert page.count("<tr") == 7  # header + 6 resolutions
    assert '<tr class="archived"><td>4.05</td>' in page
    assert "Bank Reform: 100% &quot;Fair&quot; &amp; #Open_Markets" in page
    assert 'href="resolutions/3.32_2026-Vilnius-05.pdf"' in page and 'href="LYMEC_Policy_Book_Archive.pdf"' in page


@pytest.mark.parametrize("only", ["2026-Vilnius-05", "3.32", "3.32_2026-Vilnius-05.md"])
def test_only_builds_one_resolution(built, only):
    produced = build.run(built, only=only, build_date=BUILD_DATE)
    assert sorted(p.name for p in produced) == ["3.32_2026-Vilnius-05.docx", "3.32_2026-Vilnius-05.pdf"]


def test_unknown_id_and_invalid_data_stop_the_build(tmp_path):
    shutil.copy(pbook.ROOT / "categories.yml", tmp_path / "categories.yml")
    (tmp_path / "congresses.yml").write_text((pbook.ROOT / "congresses.yml").read_text())
    with pytest.raises(build.BuildError, match="no resolutions yet"):
        build.run(tmp_path)
    meta = {"id": "2026-Vilnius-01", "number": "6.21", "title": "T", "congress": "2026-Vilnius", "adopted": "2026-10-25",
            "category": "digital", "status": "in_force", "source": "openslides"}  # fmt: skip
    pbook.write_resolution(tmp_path / "resolutions" / "2026-Vilnius-01.md", meta, "Text.")
    with pytest.raises(build.BuildError, match="category 'digital' does not exist"):
        build.run(tmp_path)
