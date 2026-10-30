"""The status list resolution_status_list.csv: the Bureau's record of every Policy Book number.

One row per number. The columns the Bureau edits come first, so a change is made at the start of a
line in the GitHub web editor: Number, Status ("in force", "archived" or "not used"), Archived at
(congress id), Replaced by (number), then Title and the information for the migration. GitHub shows
the file as a table; it can also be edited in Excel and uploaded again.

The list confirms archiving: a motion text saying it replaces or archives a resolution only takes
effect when the list marks that resolution as archived (import_openslides.py), and every resolution
marked archived in the list is archived (validate.py --fix). validate.py --fix also adds rows for new
resolutions and rewrites the file in one standard form (comma separated, UTF-8, sorted by number),
which repairs what Excel does when saving (semicolons, 6.10 written as 6.1). The migration of the
old Policy Book uses the same rules.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path

from rapidfuzz import fuzz

import pbook

FILE = "resolution_status_list.csv"
HEADER = [
    "Number", "Status", "Archived at", "Replaced by", "Title", "Adopted at", "Adopted on", "Former numbers",
    "Submitted by", "Co-signers", "Full text", "Vienna 2026 book says", "Notes",
]  # fmt: skip
STATUSES = ("in force", "archived", "not used")
TITLE_MATCH = 85  # rapidfuzz ratio of two normalised titles that are the same resolution


class StatusListError(Exception):
    pass


@dataclass
class Row:
    line: int  # line in the file, for messages
    number: str
    status: str  # lower case: "in force", "archived", "not used" or "" (not filled in yet)
    archived_at: str
    replaced_by: str
    title: str


def path(root: Path) -> Path:
    return root / FILE


def clean_number(value: str) -> str:
    """'6.10' stays; '6.1' (Excel dropped the zero) becomes '6.10'."""
    value = value.strip()
    return value + "0" if re.fullmatch(r"\d\.\d", value) else value


def read(root: Path) -> tuple[list[str], list[dict]] | None:
    """(header, records) of the list as it is, or None if there is no list."""
    if not path(root).exists():
        return None
    raw = path(root).read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):  # Excel may save in the Windows encoding
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    first = text.split("\n", 1)[0]
    delimiter = max((";", ",", "\t"), key=first.count)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header = [h.strip() for h in next(reader, [])]
    if "Number" not in header or "Status" not in header:
        raise StatusListError(
            f"{FILE}: the first line must be the column names (Number, Status, …). Restore it from an earlier version of the file."
        )
    records = []
    for values in reader:
        if not any(v.strip() for v in values):
            continue
        records.append({name: (values[i].strip() if i < len(values) else "") for i, name in enumerate(header) if name})
    return header, records


def load(root: Path) -> list[Row] | None:
    """The rows of the status list, or None if there is no list."""
    table = read(root)
    return None if table is None else rows_of(table[1])


def rows_of(records: list[dict]) -> list[Row]:
    return [
        Row(
            line=line,
            number=clean_number(rec.get("Number", "")),
            status=rec.get("Status", "").lower(),
            archived_at=rec.get("Archived at", ""),
            replaced_by=clean_number(rec.get("Replaced by", "")),
            title=rec.get("Title", ""),
        )
        for line, rec in enumerate(records, start=2)
    ]


def number_key(number: str) -> tuple:
    match = pbook.NUMBER_RE.match(number)
    return (int(match.group(1)), int(match.group(2))) if match else (99, 0)


def standard_text(header: list[str], records: list[dict]) -> str:
    """The list in its standard form: known columns first, numbers cleaned, sorted by number."""
    columns = HEADER + [name for name in header if name and name not in HEADER]
    rows = []
    for rec in records:
        rec = {**rec, "Number": clean_number(rec.get("Number", "")), "Replaced by": clean_number(rec.get("Replaced by", ""))}
        rec["Status"] = rec.get("Status", "").lower()
        rows.append([rec.get(name, "") for name in columns])
    rows.sort(key=lambda row: number_key(row[0]))  # stable: rows with the same number keep their order
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue()


def write(root: Path, header: list[str], records: list[dict]) -> bool:
    """Write the list in its standard form (UTF-8 with BOM, so Excel shows accents). True if the file changed."""
    data = standard_text(header, records).encode("utf-8-sig")
    if path(root).exists() and path(root).read_bytes() == data:
        return False
    path(root).write_bytes(data)
    return True


def normalise(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", title.casefold()).split())


def titles_match(a: str, b: str) -> bool:
    return fuzz.ratio(normalise(a), normalise(b)) >= TITLE_MATCH


def archived_row(rows: list[Row] | None, number: str, title: str | None = None) -> Row | None:
    """The row marking this number as archived; if the row has a title, it must match `title`."""
    for row in rows or []:
        if row.number == number and row.status == "archived" and (not row.title or not title or titles_match(row.title, title)):
            return row
    return None


def new_record(res: pbook.Resolution) -> dict:
    """The row for a resolution that is not in the list yet."""
    return {"Number": res.number, "Status": "in force", "Title": str(res.meta.get("title", "")), "Adopted at": str(res.meta.get("congress", ""))}
