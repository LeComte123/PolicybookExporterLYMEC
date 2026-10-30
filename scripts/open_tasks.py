"""Write OPEN_TASKS.md: everything that still has to be filled in or checked, computed from the data.

Usage: uv run scripts/open_tasks.py

The workflow runs it after every check, so the list on GitHub is always current and gets shorter as
the Bureau fills things in. Same data -> same file (no dates in it).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import import_openslides
import pbook
import status_list

FILE = "OPEN_TASKS.md"
# A sentence in a resolution text saying that it archives, replaces or merges older resolutions
STATEMENT_RE = re.compile(
    r"(?:This\s+(?:motion|resolution)\b(?:[^.\n]|(?<=\d)\.(?=\d)){0,40}?\b(?:archives|archived|replaces|merges|repeals)\b"
    r"|Archiving\s+Resolutions?\b"
    r"|Merger\s+(?:of\s+)?[Ff]ormer\b|merges\s+former\b)[^\n]*"
)
NUMBER_RE = re.compile(r"\b\d\.\d{2}\b")
# The start of a list of submitters or co-signers that stayed in the text (it names individual members)
SUBMITTER_LINE_RE = re.compile(r"^[*_\s]*(?:Movers?|Submitted\s+by|Submitters?|Co-?\s?[Ss]ubmitters)\b", re.M)
COSIGNER_LINE_RE = re.compile(
    r"^[*_\s]*(?:Co-?\s?[Ss]igned\s+by|Co-?[Ss]igners|Co-?[Ss]ignatories|Cosignatory\s+MOs|MO[’']?s\s+supporting|Co-?sponsored\s+by)", re.M
)


def flat(text: str) -> str:
    return " ".join(str(text).split())


def number_key(res: pbook.Resolution) -> tuple:
    return status_list.number_key(res.number)


def statement(text: str) -> str:
    """The statement, shortened to its sentence (numbers like 2.09 do not end it)."""
    text = flat(text.replace("\\", "").replace("*", ""))
    end = re.search(r"(?<!\d)\.(?!\d)\s", text)
    return (text[: end.start() + 1] if end else text)[:220]


def not_imported(root: Path, resolutions: list[pbook.Resolution]) -> list[str]:
    """Accepted motions of the last upload of each congress that have no resolution file."""
    ids = {res.id for res in resolutions}
    latest: dict[str, Path] = {}
    for csv in sorted((root / "imported").glob("*_*/*.csv")):
        latest[csv.parent.name.split("_", 1)[1]] = csv  # folder names sort by date and time
    lines = []
    for congress, csv in sorted(latest.items()):
        try:
            motions = import_openslides.read_csv(csv)
        except import_openslides.ImportFailure:
            continue
        for motion in motions:
            if (
                motion.state.casefold() not in import_openslides.ACCEPTED_STATES
                or not motion.number
                or import_openslides.AMENDMENT_NUMBER_RE.search(motion.number)
                or import_openslides.AMENDMENT_TITLE_RE.match(motion.title)
                or import_openslides.motion_id(congress, motion.number) in ids
            ):
                continue
            why = ""
            if motion.html is not None and (colliding := import_openslides.collisions(motion.html)):
                why = f": colliding amendments {', '.join(colliding)}. Resolve them in OpenSlides and upload a new export."
            lines.append(f"- {congress}, motion {motion.number} \"{import_openslides.clean_title(motion.title)}\"{why}")
    return lines


def tasks_markdown(root: Path) -> str:
    resolutions = sorted(pbook.load_resolutions(root), key=number_key)
    congresses = pbook.load_congresses(root)
    table = status_list.read(root)
    records = table[1] if table else []
    rows = status_list.rows_of(records)
    row_notes = {id(row): rec.get("Notes", "") for row, rec in zip(rows, records)}
    out = [
        "# Open tasks",
        "",
        "This list is made automatically from the resolutions, the status list (resolution_status_list.csv) and",
        "congresses.yml after every change. It gets shorter as things are filled in. See README.md for how.",
        "",
    ]

    def section(title: str, intro: list[str], items: list[str], count: int | None = None) -> None:
        count = len(items) if count is None else count
        out.extend([f"## {title} ({count})", ""] + intro + ([""] if intro else []) + (items or ["- nothing"]) + [""])

    # 1. numbers without a status
    blank = [row for row in rows if not row.status]
    by_chapter: dict[str, list[str]] = {}
    for row in blank:
        by_chapter.setdefault(row.number[0], []).append(row.number)
    section(
        "Policy Book numbers without a status",
        ["These numbers are not in the Vienna 2026 Policy Book. In the status list, write `archived` (with the",
         "title, if known) or `not used` in the column Status."],
        [f"- Chapter {chapter}: {', '.join(numbers)}" for chapter, numbers in sorted(by_chapter.items())],
        count=len(blank),
    )

    # 2. congress not known
    says = {rec.get("Number"): (rec.get("Vienna 2026 book says", ""), rec.get("Notes", "")) for rec in records}
    unknown = [res for res in resolutions if res.meta.get("status") == "in_force" and not res.meta.get("congress")]
    items = []
    for res in unknown:
        book, notes = says.get(res.number, ("", ""))
        hint = "; ".join(x for x in (f"the book says \"{book}\"" if book else "", notes) if x)
        items.append(f"- {res.number} {res.meta['title']}" + (f" ({hint})" if hint else ""))
    section(
        "Resolutions whose congress is not known",
        ["Write the congress (e.g. `2015-Rotterdam`) in the status list, column Adopted at. Their PDFs say",
         "\"Congress not known\" until then."],
        items,
    )

    # 3. congresses with missing data
    items = []
    for con in congresses:
        missing = [label for key, label in (("city", "city"), ("date_start", "first day"), ("date_end", "last day")) if not con.get(key)]
        if missing:
            items.append(f"- {con['id']} {con.get('name', '')}: {', '.join(missing)} missing")
    section("Congresses with missing information", ["Fill them in in congresses.yml."], items)

    # 4. submitters
    in_force = [res for res in resolutions if res.meta.get("status") == "in_force"]
    def top(res):  # the block before the text; lists further down belong to merged parts of the text
        return "\n\n".join(res.body.split("\n\n")[:8])

    named = [res for res in in_force if not res.meta.get("submitted_by") and SUBMITTER_LINE_RE.search(top(res))]
    items = []
    for res in in_force:
        lists = [kind for kind, regex in (("submitters", SUBMITTER_LINE_RE), ("co-signers", COSIGNER_LINE_RE)) if regex.search(top(res))]
        if lists:
            items.append(f"- {res.number} {res.meta['title']} ({' and '.join(lists)})")
    section(
        "Lists of submitters or co-signers that stayed in the text",
        ["These lists name individual members, so they were not moved into the header (names of people are",
         "never put there). Add the organisations to submitted_by / co_signers in the file, if wanted."],
        items,
    )
    none = [res for res in in_force if not res.meta.get("submitted_by") and res not in named]
    section(
        "Resolutions without any submitting organisation",
        ["The texts do not say who submitted them. Add them to submitted_by in the file, if known."],
        [f"- {', '.join(res.number for res in none)}"] if none else [],
        count=len(none),
    )

    # 5. archiving mentioned in texts
    items = []
    for res in resolutions:
        for match in STATEMENT_RE.finditer(res.body):
            text = statement(match.group(0))
            numbers = NUMBER_RE.findall(text)  # its own number too: "merges former 8.01" means an older 8.01
            open_numbers = [n for n in dict.fromkeys(numbers) if not any(r.number == n and r.status == "archived" for r in rows)]
            if open_numbers:
                items.append(f"- {res.number}: \"{text}\" → {', '.join(open_numbers)}")
    section(
        "Archived resolutions mentioned in texts",
        ["These texts say they archived, replaced or merged older resolutions (numbers of that time; today the",
         "number may belong to another resolution). For each, add a line to the status list: the old number,",
         "`archived`, its title, and Archived at. Then it can go into the archive edition."],
        items,
    )

    # 6. archived lines that need a title: their number is used by a resolution in force today
    in_force_numbers = {row.number for row in rows if row.status == "in force"}
    untitled = [row for row in rows if row.status == "archived" and not row.title and row.number in in_force_numbers]
    section(
        "Archived resolutions that need their title",
        ["Their number is used by another resolution today, so the title is needed to tell them apart. Write it",
         "in the status list, column Title."],
        [f"- {row.number}" + (f" ({row_notes[id(row)]})" if row_notes.get(id(row)) else "") for row in untitled],
    )

    # 7. archived lines whose resolution is not in the database
    def in_database(row):
        return any(
            res.number == row.number and res.meta.get("status") != "in_force"
            and (not row.title or status_list.titles_match(row.title, str(res.meta.get("title", ""))))
            for res in resolutions
        )

    waiting = [row for row in rows if row.status == "archived" and not in_database(row)]
    section(
        "Archived resolutions without their text",
        ["Marked archived in the status list, but their text is not in the database, so they are not in the",
         "archive edition yet. Say where the text is (column Full text), e.g. an older edition of the Policy Book."],
        [f"- {row.number} {row.title or '(title not known)'}" for row in waiting],
    )

    # 8. motions not imported
    section("Accepted motions that were not imported", [], not_imported(root, resolutions))
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else pbook.ROOT
    (root / FILE).write_text(tasks_markdown(root), encoding="utf-8")
    print(f"Wrote {FILE}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
