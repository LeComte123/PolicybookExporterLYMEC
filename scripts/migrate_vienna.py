"""One-time migration of the Vienna 2026 Policy Book into resolutions/ (phase 6).

Usage: uv run scripts/migrate_vienna.py [--force]

Reads, in imports/migration/:
  Updated Policy Book Spring 2026 Vienna.docx.md   the texts (Markdown export of the Policy Book document)
  Updated Policy Book Spring 2026 Vienna.docx.pdf  numbers and titles, from the PDF bookmarks (the Markdown
                                                   export lost the chapter digit of the automatic heading
                                                   numbers: "33." instead of "1.33")
and resolution_status_list.csv (congress of each resolution: column 'Adopted at') and congresses.yml.

Every resolution of the book becomes resolutions/<number>_PB2026-<number>.md, status in_force, source
migration. The text is kept word for word, including the lines about movers, adoption and former
numbers; only the structure is cleaned up (clean_body). A congress that is not known yet stays empty
(the Bureau fills it in via the status list). Statements like "This resolution archives 2.21" are
checked against the status list and listed in the report: imports/migration/migration_report.md.
Archived resolutions are created later from the status list, once the Bureau has filled it in.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pymupdf
from rapidfuzz import fuzz

import import_openslides
import pbook
import status_list

BOOK = "Updated Policy Book Spring 2026 Vienna.docx"
TITLE_MATCH = 85
# Congresses the texts point to but do not state, for the Bureau to check (shown in the report only)
SUGGESTIONS = {
    "9.37": "probably 2024-Warsaw: the text says it is the former 9.37 'prior to Warsaw, Autumn 2024'",
    "9.48": "probably 2024-Warsaw: the text says it replaces 9.48 'prior to Warsaw, Autumn 2024'",
}
# A sub-heading that ended up in the same heading as the title, e.g. "Telecommunication in Europe Considering that:"
GLUED_SUBHEADING_RE = re.compile(r"^(.*\S)\s+((?:Considering|Noting|Recalling|Believing|Recognising|Whereas)(?: that)?:)$")
CHAPTER_LINE_RE = re.compile(r"^\**\s*Chapter \d\s*(\\?-|–)")
HEADING_RE = re.compile(r"^(\s*)(\d+\.\s+)?#{1,6}\s+(.*)$")
LIST_ITEM_RE = re.compile(r"^\s*([*+-]|\d+[.)])\s")
LIST_HEADING_RE = re.compile(r"^(\s*(?:[*+-]|\d+[.)])\s+)#{1,6}\s+(.*)$")
LONE_BULLET = {"●", "▪", "•", "*", "-", "* ", "- "}


class MigrationError(Exception):
    pass


def bookmarks(pdf: Path) -> list[tuple[str, str]]:
    """(number, title) of every resolution, in book order."""
    marks = []
    for _, text, _ in pymupdf.open(pdf).get_toc():
        text = " ".join(text.replace("​", " ").split())
        if match := re.match(r"^(\d\.\d{2})\.?\s*(.*)$", text):
            marks.append((match.group(1), match.group(2)))
    return marks


def unescape(text: str) -> str:
    return re.sub(r"\\([\\`*_{}\[\]()#+\-.!<>|~])", r"\1", text)


def heading_title(line: str) -> str:
    """The plain title of a title heading such as '  33. ##  **A Better … Management**' or '## **7.03. …**'."""
    text = re.sub(r"^\s*(\d+\.\s+)?#{1,6}\s*", "", line)
    text = re.sub(r"(?<!\\)\*+", "", text)  # bold/italic markers
    text = unescape(text).replace("​", "")
    text = re.sub(r"^\d\.\d{2}\.?\s+", "", text.strip())  # number written in the heading
    text = re.sub(r"^[–-]\s+", "", text)  # dash between the (automatic) number and the title
    return " ".join(text.split())


def comparable(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", heading_title(text).casefold()).split())


def find_titles(lines: list[str], marks: list[tuple[str, str]]) -> list[tuple[str, int]]:
    """Line index of each bookmark's title heading in the Markdown, searching forward in book order."""
    headings = [(i, line) for i, line in enumerate(lines) if HEADING_RE.match(line)]
    found, pos = [], 0
    for number, title in marks:
        target = comparable(title)
        for k in range(pos, len(headings)):
            i, line = headings[k]
            text = comparable(line)
            # bookmark titles can be cut off, so a heading that starts with the bookmark also counts
            if fuzz.ratio(text, target) >= TITLE_MATCH or (len(target) > 25 and text.startswith(target[:25])):
                found.append((number, i))
                pos = k + 1
                break
        else:
            raise MigrationError(f"The title of {number} ('{title}') was not found in {BOOK}.md. Check the two files belong together.")
    return found


def clean_body(lines: list[str]) -> str:
    """The resolution text, word for word; only structure is cleaned up:
    - chapter headings and stray bullet characters (left over from the PDF origin of the texts) are dropped;
    - headings inside a resolution ("Noting that:") become bold paragraphs, so they do not compete with
      the resolution titles in the Policy Book (inside a list item only the heading mark is removed);
    - zero-width spaces are removed and runs of empty lines collapsed;
    - text that Markdown would show as code, because of meaningless deep indentation, is moved left.
    """
    out = []
    for line in lines:
        line = line.replace("​", "").rstrip("\n")
        stripped = line.strip()
        if CHAPTER_LINE_RE.match(stripped) or stripped in LONE_BULLET:
            continue
        if match := LIST_HEADING_RE.match(line):  # "* ## text": a heading inside a list item
            line = match.group(1) + match.group(2).strip()
        elif match := HEADING_RE.match(line):
            indent, number, text = match.groups()
            text = text.strip()
            if not text:
                continue
            if "**" not in text.strip("*"):  # partly bold text keeps its own markers
                text = "**" + text.strip("*").strip() + "**"
            line = f"{indent}{number or ''}{text}"
        out.append(line)
    text = "\n".join(no_code_blocks(out))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def code_blocks(markdown: str) -> list[str]:
    """The texts of the blocks pandoc reads as code (in any list depth)."""
    result = subprocess.run(["pandoc", "-f", "gfm", "-t", "json"], input=markdown, capture_output=True, text=True, check=True)
    found = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("t") == "CodeBlock":
                found.append(node["c"][1])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(result.stdout)["blocks"])
    return found


def words(line: str) -> str:
    return " ".join(line.split())


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def no_code_blocks(lines: list[str]) -> list[str]:
    """Move text left that Markdown would show as code.

    The texts went through many conversions, so their indentation carries no meaning, but Markdown
    prints text indented by 4 more spaces than its context as code. A tab after a list bullet becomes a
    space; pandoc then finds the remaining code blocks, and each one is moved to the indentation of the
    line before it (its lines keep their positions relative to each other).
    """
    lines = [re.sub(r"^(\s*(?:[*+-]|\d+[.)]))[ \t]*\t[ \t]*", r"\1 ", line) for line in lines]
    lines = [line[: len(line) - len(line.lstrip(" \t"))].replace("\t", "    ") + line.lstrip(" \t") for line in lines]
    for _ in range(100):
        codes = code_blocks("\n".join(lines))
        if not codes:
            break
        wanted = [words(line) for line in codes[0].splitlines() if line.strip()]  # pandoc turns tabs into spaces in code
        start = next((i for i, line in enumerate(lines) if words(line) == wanted[0] and indentation(line) >= 4), None)
        if start is None:
            break  # not found again: leave the text as it is
        before = next((indentation(line) for line in reversed(lines[:start]) if line.strip()), 0)
        shift = indentation(lines[start]) - before
        i, k = start, 0
        while i < len(lines) and k < len(wanted):
            if words(lines[i]) == wanted[k]:
                lines[i] = lines[i][min(shift, indentation(lines[i])):]
                k += 1
            elif lines[i].strip():
                break
            i += 1
    return lines


def migrate(root: Path, force: bool = False) -> dict:
    """Write the resolution files and return the facts for the report. Raises MigrationError."""
    folder = root / "imports" / "migration"
    md_path, pdf_path = folder / f"{BOOK}.md", folder / f"{BOOK}.pdf"
    for path in (md_path, pdf_path):
        if not path.exists():
            raise MigrationError(f"{path.relative_to(root)} is missing. Put the Markdown export and the PDF of the book there.")
    existing = [r for r in pbook.load_resolutions(root) if r.meta.get("source") == "migration"]
    if existing and not force:
        raise MigrationError(f"{len(existing)} migrated resolutions exist already. Run with --force to replace them.")
    for res in existing:
        res.path.unlink()

    marks = bookmarks(pdf_path)
    lines = md_path.read_text(encoding="utf-8").splitlines()
    titles = find_titles(lines, marks)
    rows = status_list.load(root) or []
    in_force_rows = {row.number: row for row in rows if row.status == "in force"}
    congresses = {str(c["id"]): c for c in pbook.load_congresses(root)}
    chapters = {cat["chapter"]: cat["id"] for cat in pbook.load_categories(root)}
    numbers = [number for number, _ in titles]
    if len(set(numbers)) != len(numbers):
        raise MigrationError(f"Numbers appear twice in the book: {sorted({n for n in numbers if numbers.count(n) > 1})}.")

    facts = {"written": [], "no_congress": [], "title_differs": [], "not_in_list": [], "statements": []}
    for k, (number, start) in enumerate(titles):
        end = titles[k + 1][1] if k + 1 < len(titles) else len(lines)
        title = heading_title(lines[start])
        body_lines = lines[start + 1:end]
        if match := GLUED_SUBHEADING_RE.match(title):
            title = match.group(1)
            body_lines = [f"**{match.group(2)}**", ""] + body_lines
        body = clean_body(body_lines)

        row = in_force_rows.get(number)
        congress = row.adopted_at if row and row.adopted_at else None
        if row is None:
            facts["not_in_list"].append(f"{number} {title}")
        elif row.title and not status_list.titles_match(row.title, title):
            facts["title_differs"].append(f"{number}: book \"{title}\", status list \"{row.title}\"")
        if congress and congress not in congresses:
            raise MigrationError(f"{status_list.FILE}: 'Adopted at' {congress} of {number} is not in congresses.yml. Add it there first.")
        if not congress:
            facts["no_congress"].append(number)

        meta = {
            "id": f"{pbook.MIGRATION_ID_PREFIX}{number}",
            "number": number,
            "title": title,
            "congress": congress,
            "adopted": congresses[congress].get("date_end") if congress else None,
            "category": chapters[int(number[0])],
            "status": "in_force",
            "replaces": [],
            "superseded_by": None,
            "amends": [],
            "submitted_by": [],
            "co_signers": [],
            "source": "migration",
        }
        pbook.write_resolution(pbook.resolution_path(root, meta), meta, body)
        facts["written"].append(number)
        for kind, target in import_openslides.statements(body):
            confirmed = status_list.archived_row(rows, target) is not None
            facts["statements"].append((number, kind, target, confirmed))

    others = {r.number for r in pbook.load_resolutions(root) if r.meta.get("source") != "migration"}  # e.g. imported since
    facts["missing_from_book"] = sorted(set(in_force_rows) - set(numbers) - others, key=status_list.number_key)
    facts["archived_rows"] = [row for row in rows if row.status == "archived"]
    return facts


def report_markdown(facts: dict, root: Path) -> str:
    says = {}
    table = status_list.read(root)
    if table:
        says = {rec.get("Number"): rec.get("Vienna 2026 book says", "") for rec in table[1]}
    out = [
        "# Migration of the Vienna 2026 Policy Book",
        "",
        f"{len(facts['written'])} resolutions written to resolutions/ (status in force, source migration).",
        "The texts are word for word; submitters, adoption lines and former numbers stay in the text.",
        "",
        f"## Congress not known yet ({len(facts['no_congress'])})",
        "",
        "Enter the congress in resolution_status_list.csv (column 'Adopted at'); the next run fills it in.",
        "",
    ]
    out += [
        f"- {n}" + (f": the book says \"{says[n]}\"" if says.get(n) else "") + (f" ({SUGGESTIONS[n]})" if n in SUGGESTIONS else "")
        for n in facts["no_congress"]
    ] or ["- none"]
    out += ["", "## Statements about older resolutions", "",
            "These texts say they replace or archive another resolution. The numbers are those of that time: the",
            "resolutions they mean were archived before 2026, and numbers were changed since, so today the same",
            "number may belong to another resolution. Nothing was changed. To record such an archived resolution,",
            "add a line to the status list with its old number, 'archived' and its title (the title is needed so",
            "that the resolution which has the number today is not archived by mistake).", ""]
    out += [f"- {n} {'replaces' if kind == 'replaces' else 'archives'} {target}" + (" (marked archived in the status list)" if ok else "")
            for n, kind, target, ok in facts["statements"]] or ["- none"]
    out += ["", "## Checks against the status list", ""]
    out += [f"- In the book, not 'in force' in the status list: {n}" for n in facts["not_in_list"]]
    out += [f"- 'In force' in the status list, not in the book: {n}" for n in facts["missing_from_book"]]
    out += [f"- Title differs: {t}" for t in facts["title_differs"]]
    if not (facts["not_in_list"] or facts["missing_from_book"] or facts["title_differs"]):
        out.append("- Numbers and titles agree.")
    out += ["", "## Archived resolutions", "",
            f"{len(facts['archived_rows'])} rows of the status list are marked 'archived'. Archived resolutions are",
            "created once the Bureau has filled in the status list (title, congress and where the full text is)."]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the Vienna 2026 Policy Book into resolutions/ (once).")
    parser.add_argument("--force", action="store_true", help="replace resolutions migrated before")
    parser.add_argument("--root", type=Path, default=pbook.ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        facts = migrate(args.root, force=args.force)
    except MigrationError as exc:
        print(f"Migration failed: {exc}")
        return 1
    report = report_markdown(facts, args.root)
    (args.root / "imports" / "migration" / "migration_report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
