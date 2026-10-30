"""Import accepted motions from an OpenSlides export into resolutions/<id>.md.

Usage: uv run scripts/import_openslides.py [FILE ...] [--force] [--accepted-states "accepted,adopted"]

Without FILE, every imports/<congress>.csv / .pdf is imported (imports/ is the upload inbox) and
then moved to imported/ for the record. A file whose content is already in imported/ is not imported
again. The congress id is the file name: 2026-Vilnius.csv -> 2026-Vilnius (year, dash, city in
English); resolution files are named <Policy Book number>_<congress>-<motion number>.md.
The CSV ("Export as CSV" in OpenSlides) is the main source: its Text column holds the motion text
as HTML. The PDF is optional: next to a CSV it is only cross-checked (numbers and titles), on its
own its text is read with openslides_pdf.

Motions with unresolved colliding amendments are not imported but reported as import errors; the
other motions of the file are imported. Problems with a whole file (wrong name, unreadable CSV,
CSV and PDF disagree) stop the import before anything is written; exit code 1.
Reports: dist/import-report.md (release notes); dist/import-errors.md if motions were skipped and
dist/import-failed.md if the import stopped. The last two are the texts of the GitHub issues the
workflow opens: first line "# <issue title>", then the body. dist/new-congress.md is written when a
congress was not in congresses.yml and a placeholder entry was added, dist/status-check.md when a
motion says it replaces or archives a resolution that the status list does not mark as archived.
Other files in imports/ (e.g. the migration material in imports/migration/) are not touched.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import io
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

import pbook
import status_list
from openslides_pdf import parse_pdf

ACCEPTED_STATES = ("accepted", "adopted")
COLLISION_FIX = (
    "Several amendments change the same line. Open the motion in OpenSlides, create a modified "
    "final version that resolves them, and export again."
)
AMENDMENT_NUMBER_RE = re.compile(r"Ä|\bamd\b", re.I)  # "08-Ä09"
AMENDMENT_TITLE_RE = re.compile(r"^(\S+:\s*)?(amendment to|änderungsantrag zu|amendement à)\s+\S+\s*$", re.I)  # "Amendment to 08"
AGENDA_PREFIX_RE = re.compile(r"^\s*\d+\.\s+")
CHAPTER_RE = re.compile(r"\bch\w*\s*(\d)\b|^\s*(\d)\b", re.I)  # tolerates "Chatper 3"
SUBMITTER_LINE_RE = re.compile(
    r"^(?P<label>(co[-\s]?)?(submitted|signed|signers?|submitters?))(\s+by)?\s*:\s*(?P<orgs>.*)$", re.I | re.S
)
REPLACES_NUMBER_RE = re.compile(r"\b(\d\.\d{2,3})\b")
# A paragraph that states which resolutions this one replaces or archives (anywhere in the text):
# "This motion replaces: 6.03 …", "Replaces: …", "This resolution archives Resolution 2.21 …",
# "Archiving Resolutions 6.02, 6.03 …". Numbers in other sentences are ignored.
STATEMENT_RE = re.compile(
    r"^[*_\s]*(?:this\s+(?:motion|resolution)\s+(?P<verb>replaces|archives|repeals)\b"
    r"|(?P<label>replaces|archives)\s*:|(?P<archiving>archiving)\s+resolutions?\b)",
    re.I,
)
SUGGESTION_CUTOFF = 70
# Fields kept from an existing file when it is re-imported with --force (they may have been curated).
KEPT_ON_FORCE = ("number", "status", "superseded_by", "amends")


class ImportFailure(Exception):
    """A problem with a whole file; the message tells the uploader what to do."""


@dataclass
class Motion:
    number: str
    title: str
    state: str = ""
    category: str = ""
    submitters: str = ""
    html: str | None = None  # CSV text
    markdown: str | None = None  # PDF text (only used without a CSV text)
    repeated_line: str | None = None  # PDF only: a line printed twice (colliding amendments)


@dataclass
class Plan:
    """Everything one congress upload would change, computed before anything is written."""

    congress: str
    sources: list[Path]
    writes: list[tuple[Path, dict, str]] = field(default_factory=list)
    removals: list[Path] = field(default_factory=list)  # old files of re-imported resolutions that got a new number
    imported: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    replacements: list[str] = field(default_factory=list)
    unconfirmed: list[str] = field(default_factory=list)  # statements the status list does not confirm
    congress_stub: dict | None = None


# ---------------------------------------------------------------- reading the export


def read_csv(path: Path) -> list[Motion]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):  # latin-1 never fails
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    header = text.splitlines()[0] if text.strip() else ""
    sep = max(",;\t", key=header.count)
    if not header or header.count(sep) == 0:
        raise ImportFailure(f"{path.name} does not look like an OpenSlides CSV export. Export the motions as CSV again and upload that file.")
    table = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False)
    columns = {name.strip().casefold(): name for name in table.columns}
    missing = [name for name in ("number", "title", "state") if name not in columns]
    if missing:
        raise ImportFailure(
            f"{path.name} has no column {', '.join(repr(m.title()) for m in missing)}. In the OpenSlides CSV export, "
            "tick Number, Title, Text, Submitters, State and Category, then upload the new file."
        )

    def get(row, name):
        return str(row[columns[name]]).strip() if name in columns else ""

    return [
        Motion(
            number=get(row, "number"),
            title=get(row, "title"),
            state=get(row, "state"),
            category=get(row, "category"),
            submitters=get(row, "submitters"),
            html=str(row[columns["text"]]) if "text" in columns else None,
        )
        for _, row in table.iterrows()
    ]


def read_pdf(path: Path) -> list[Motion]:
    return [
        Motion(
            number=pm.number,
            title=pm.title,
            state=pm.meta.get("state", ""),
            category=pm.meta.get("category", ""),
            submitters=pm.meta.get("submitters", ""),
            markdown=pm.markdown,
            repeated_line=pm.repeated_line,
        )
        for pm in parse_pdf(path)
    ]


def normalise_title(title: str) -> str:
    text = clean_title(title).casefold()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", re.sub(r"-\s+", "-", text)).strip()


def cross_check(csv_motions: list[Motion], pdf_motions: list[Motion], csv_name: str, pdf_name: str) -> None:
    csv_by_number = {m.number: m for m in csv_motions}
    pdf_by_number = {m.number: m for m in pdf_motions}
    problems = [f"motion {n} is only in {csv_name}" for n in csv_by_number if n not in pdf_by_number]
    problems += [f"motion {n} is only in {pdf_name}" for n in pdf_by_number if n not in csv_by_number]
    for number, motion in csv_by_number.items():
        other = pdf_by_number.get(number)
        if other and fuzz.ratio(normalise_title(motion.title), normalise_title(other.title)) < 95:
            problems.append(f'motion {number} is called "{clean_title(motion.title)}" in the CSV but "{clean_title(other.title)}" in the PDF')
    if problems:
        raise ImportFailure(
            f"{csv_name} and {pdf_name} do not match: {'; '.join(problems)}. Export both files from the same "
            "motions in OpenSlides and upload them again (or upload only the CSV)."
        )


# ---------------------------------------------------------------- converting one motion


def motion_id(congress: str, number: str) -> str:
    suffix = f"{int(number):02d}" if number.isdigit() else re.sub(r"[^A-Za-z0-9]+", "", number).upper()
    return f"{congress}-{suffix}"


def number_key(number: str) -> tuple:
    return (0, int(number), "") if number.isdigit() else (1, 0, number)


def clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", html.unescape(title)).strip()
    return AGENDA_PREFIX_RE.sub("", title)


def chapter_of(category: str) -> int | None:
    match = CHAPTER_RE.search(category)
    return int(match.group(1) or match.group(2)) if match else None


def collisions(text: str) -> list[str]:
    """data-identifier of every colliding-amendment block, in order, without duplicates."""
    found = []
    for tag in re.findall(r"<div\b[^>]*os-colliding-change[^>]*>", text):
        ident = re.search(r'data-identifier="([^"]*)"', tag)
        name = html.unescape(ident.group(1)) if ident else "unknown amendment"
        if name not in found:
            found.append(name)
    return found


def clean_html(text: str) -> str:
    """Remove OpenSlides editor noise that has no meaning in the resolution text."""
    text = text.replace("&nbsp;", " ").replace("\xa0", " ")
    text = re.sub(r"</?(span|div)\b[^>]*>", "", text)
    text = re.sub(r'\s(style|class)="[^"]*"', "", text)
    text = re.sub(r"(<br\s*/?>\s*)+(?=</(li|p)>)", "", text)  # line breaks at the end of an item
    text = re.sub(r"<p>\s*</p>", "", text)  # empty paragraphs
    return re.sub(r"</ul>\s*<ul>", "", text)  # one <ul> per item -> one list


def html_to_markdown(text: str) -> str:
    try:
        result = subprocess.run(
            ["pandoc", "-f", "html", "-t", "gfm", "--wrap=none"], input=clean_html(text), capture_output=True, text=True, check=True
        )
    except FileNotFoundError:
        raise ImportFailure("pandoc is not installed. Install it (macOS: brew install pandoc) and run the import again.") from None
    return tidy_markdown(result.stdout)


def tidy_markdown(text: str) -> str:
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def unescape_markdown(text: str) -> str:
    text = re.sub(r"\\\n", " ", text)  # hard line break
    return re.sub(r"\s+", " ", re.sub(r"\\(.)", r"\1", text)).strip()


def split_orgs(text: str) -> list[str]:
    """Split "A, B (Poland); C" on commas/semicolons outside parentheses. Names stay verbatim."""
    orgs, depth, current = [], 0, ""
    for char in text:
        depth += char == "("
        depth -= char == ")"
        if char in ",;" and depth <= 0:
            orgs.append(current)
            current = ""
        else:
            current += char
    orgs.append(current)
    return [org.strip() for org in orgs if org.strip()]


def split_submitters(markdown: str) -> tuple[list[str], list[str], str]:
    """Take the leading "Submitted by:/Co-submitted by:/Co-signed by:" paragraphs out of the text."""
    blocks = markdown.strip().split("\n\n")
    submitted, co_signers = [], []
    while blocks:
        match = SUBMITTER_LINE_RE.match(unescape_markdown(blocks[0]))
        if not match:
            break
        target = co_signers if "sign" in match["label"].casefold() else submitted
        target.extend(org for org in split_orgs(match["orgs"]) if org not in target)
        blocks.pop(0)
    return submitted, co_signers, "\n\n".join(blocks).strip() + "\n"


def orgs_from_submitters(column: str) -> list[str]:
    """'Ben Jokinen (SU · No. Online)' -> ['SU']; person names are never kept."""
    orgs = []
    for inner in re.findall(r"\(([^()]*)\)", column):
        org = inner.split("·")[0].strip()
        if org and org not in orgs:
            orgs.append(org)
    return orgs


def statements(markdown: str) -> list[tuple[str, str]]:
    """(kind, number) for the resolutions a motion says it replaces or archives; kind is
    "replaces" or "archives"."""
    found = []
    for block in markdown.split("\n\n"):
        plain = unescape_markdown(block)
        if match := STATEMENT_RE.match(plain):
            verb = (match.group("verb") or match.group("label") or match.group("archiving")).lower()
            kind = "replaces" if verb.startswith("replac") else "archives"
            found += [(kind, n) for n in REPLACES_NUMBER_RE.findall(plain) if (kind, n) not in found]
    return found


# ---------------------------------------------------------------- planning one upload


def uploads(root: Path) -> list[Path]:
    """The OpenSlides exports waiting in imports/ (CSV and PDF files directly in it)."""
    return [p for p in sorted((root / "imports").glob("*")) if p.is_file() and p.suffix.lower() in (".csv", ".pdf")]


def find_sources(files: list[Path], root: Path) -> dict[str, list[Path]]:
    """Group the given files (or the new files in imports/) by congress id."""
    if files:
        paths = []
        for path in files:
            paths.append(path)
            sibling = path.with_suffix(".pdf" if path.suffix.lower() == ".csv" else ".csv")
            if sibling.exists() and sibling not in files:
                paths.append(sibling)
    else:
        paths = uploads(root)
    groups: dict[str, list[Path]] = {}
    for path in paths:
        if path.suffix.lower() not in (".csv", ".pdf"):
            raise ImportFailure(f"{path.name} is not a CSV or PDF file. Upload the OpenSlides export as .csv (and optionally .pdf).")
        congress = pbook.congress_id(path.stem)
        if not pbook.CONGRESS_ID_RE.match(congress):
            raise ImportFailure(
                f'The file name "{path.name}" must be the year and the city of the congress, e.g. 2026-Vilnius.csv '
                "(city in English, without accents). Rename the file and upload it again."
            )
        groups.setdefault(congress, []).append(path)
    return groups


def plan_upload(congress: str, sources: list[Path], root: Path, ctx: dict, force: bool) -> Plan:
    plan = Plan(congress, sources)
    ctx["claimed"] = set()  # numbers the motions of this upload say they replace or archive
    csv_path = next((p for p in sources if p.suffix.lower() == ".csv"), None)
    pdf_path = next((p for p in sources if p.suffix.lower() == ".pdf"), None)

    pdf_motions = read_pdf(pdf_path) if pdf_path else None
    if csv_path:
        motions = read_csv(csv_path)
        if pdf_motions is not None:
            cross_check(motions, pdf_motions, csv_path.name, pdf_path.name)
            if any(m.html is None for m in motions):  # CSV without Text column: take the text from the PDF
                by_number = {m.number: m for m in pdf_motions}
                for m in motions:
                    m.markdown, m.repeated_line = by_number[m.number].markdown, by_number[m.number].repeated_line
        elif any(m.html is None for m in motions):
            raise ImportFailure(f"{csv_path.name} has no Text column. Tick 'Text' in the OpenSlides CSV export, or upload the PDF as well.")
    else:
        motions = pdf_motions or []
    if not motions:
        raise ImportFailure(f"No motions found in {', '.join(p.name for p in sources)}. Check that you exported the right motions.")

    congress_entry = next((c for c in ctx["congresses"] if str(c.get("id")) == congress), None)
    if congress_entry is None:
        today = ctx["today"]
        city = congress[5:].replace("-", " ")
        congress_entry = {"id": congress, "name": f"LYMEC Congress {congress[:4]}", "city": city, "date_start": today, "date_end": today}
        plan.congress_stub = congress_entry
        ctx["congresses"].append(congress_entry)
        plan.notes.append(
            f"Congress {congress} was not in congresses.yml, so it was added with the name \"{congress_entry['name']}\" "
            "and today's date as placeholders. Correct the name and the dates in congresses.yml; the adoption dates "
            "of its resolutions are then corrected automatically."
        )
    adopted = pbook.parse_date(congress_entry.get("date_end"))
    if adopted is None:
        adopted = ctx["today"]
        plan.notes.append(f"Congress {congress} has no date_end in congresses.yml, so today's date was used as the adoption date.")

    accepted = {s.casefold() for s in ctx["accepted_states"]}
    chapters = {cat["chapter"]: cat["id"] for cat in ctx["categories"]}
    for motion in sorted(motions, key=lambda m: number_key(m.number)):
        label = f"Motion {motion.number or '(no number)'}"
        if AMENDMENT_NUMBER_RE.search(motion.number) or AMENDMENT_TITLE_RE.match(motion.title):
            plan.skipped.append(f"{label}: amendment (already part of the final motion text).")
            continue
        if motion.state.casefold() not in accepted:
            plan.skipped.append(f"{label}: state is '{motion.state}', not accepted.")
            continue
        if not motion.number:
            plan.errors.append(f'"{clean_title(motion.title)}" was not imported: it has no number. Give it a number in OpenSlides and export again.')
            continue
        if motion.html is not None and (colliding := collisions(motion.html)):
            plan.errors.append(f"{label} was not imported: colliding amendments {', '.join(colliding)}. {COLLISION_FIX}")
            continue
        if motion.html is None and motion.repeated_line:
            plan.errors.append(
                f'{label} was not imported: some lines appear twice in the PDF ("{motion.repeated_line[:60]}…"), which '
                f"usually means colliding amendments. {COLLISION_FIX} Uploading the CSV export as well shows which amendments collide."
            )
            continue
        chapter = chapter_of(motion.category)
        if chapter not in chapters:
            plan.errors.append(
                f"{label} was not imported: its category \"{motion.category}\" is not a Policy Book chapter. "
                "Set the category to the right chapter (e.g. \"Chapter 6 - …\") in OpenSlides and export again."
            )
            continue

        rid = motion_id(congress, motion.number)
        existing = ctx["by_id"].get(rid)
        if existing and not force:
            plan.skipped.append(f"{label}: already in the database as {rid}, not changed (an admin can re-import it with --force).")
            continue

        markdown = html_to_markdown(motion.html) if motion.html is not None else tidy_markdown(motion.markdown or "")
        submitted, co_signers, body = split_submitters(markdown)
        if not submitted:
            submitted = orgs_from_submitters(motion.submitters)
        if not submitted:
            plan.notes.append(f"{label}: no submitting organisation found. Add it to submitted_by in the file of {rid}.")
        if not body.strip():
            plan.errors.append(f"{label} was not imported: its text is empty. Check the motion text in OpenSlides and export again.")
            continue

        meta = {
            "id": rid,
            "number": None,
            "title": clean_title(motion.title),
            "congress": congress,
            "adopted": adopted,
            "category": chapters[chapter],
            "status": "in_force",
            "replaces": [],
            "superseded_by": None,
            "amends": [],
            "submitted_by": submitted,
            "co_signers": co_signers,
            "openslides_number": motion.number,
            "source": "openslides",
        }
        if existing:
            for key in KEPT_ON_FORCE:
                meta[key] = existing.meta.get(key, meta[key])
            meta["replaces"] = list(existing.meta.get("replaces") or [])
            if not str(meta["number"]).startswith(f"{chapter}."):
                meta["number"] = None  # moved to another chapter: needs a number there
        if not meta["number"]:
            meta["number"] = pbook.next_number(chapter, ctx["categories"], ctx["resolutions"])
        path = pbook.resolution_path(root, meta)
        if existing and existing.path != path:
            plan.removals.append(existing.path)
        new = pbook.Resolution(path, meta, body)
        ctx["resolutions"] = [r for r in ctx["resolutions"] if r.id != rid] + [new]
        ctx["by_id"][rid] = new

        add_replacements(new, body, ctx, plan)
        plan.writes.append((path, meta, body))
        plan.imported.append(f"{meta['number']} {meta['title']} ({rid}, chapter {chapter})")

    # The other direction of the check: archived at this congress according to the list, not named by a motion
    for row in ctx["status_list"] or []:
        if row.status == "archived" and row.archived_at.lower() == congress.lower() and row.number not in ctx["claimed"]:
            plan.notes.append(
                f"The status list marks {row.number} as archived at {congress}, but no motion of this upload mentions it. "
                "It is archived anyway, because the status list decides."
            )
    return plan


def add_replacements(new: pbook.Resolution, body: str, ctx: dict, plan: Plan) -> None:
    """Check "This motion replaces/archives 6.03" against the status list and suggest similar older titles.

    A statement only takes effect when the status list marks the resolution as archived (the Bureau
    confirms it); otherwise the Bureau is asked to check. "replaces" sets `replaces`, so the old
    resolution becomes superseded; "archives" needs nothing here: validate.py --fix archives every
    resolution the list marks as archived.
    """
    others = [r for r in ctx["resolutions"] if r.id != new.id]
    # numbers of archived resolutions can be used again after a renumbering: the one in force wins
    by_number = {r.number: r for r in sorted(others, key=lambda r: r.meta.get("status") == "in_force")}
    label = f"{new.meta['number']} ({new.id})"
    for kind, number in statements(body):
        old = by_number.get(number)
        verb = "replaces" if kind == "replaces" else "archives"
        ctx["claimed"].add(number)
        if old is None:
            plan.replacements.append(f"{label} says it {verb} {number}, but {number} is not in the database yet.")
            continue
        old_label = f"{number} {old.meta.get('title')}"
        if not status_list.archived_row(ctx["status_list"], number, str(old.meta.get("title", ""))):
            hint = f" and write {new.meta['number']} under 'Replaced by'" if kind == "replaces" else ""
            plan.unconfirmed.append(
                f"{label} {new.meta['title']} says it {verb} {old_label}, but the status list does not mark {number} "
                f"as archived. If the congress decided this, set {number} to 'archived' in {status_list.FILE}{hint}. "
                f"Otherwise nothing needs to be done: {number} stays in force."
            )
        elif kind == "replaces" and old.id not in new.meta["replaces"]:
            new.meta["replaces"].append(old.id)
            plan.replacements.append(f"{label} replaces {old_label} ({old.id}), as stated in its text and confirmed by the status list.")
        elif kind == "archives":
            plan.replacements.append(f"{label} archives {old_label} ({old.id}), as stated in its text and confirmed by the status list.")

    candidates = {
        r.id: str(r.meta.get("title", ""))
        for r in ctx["resolutions"]
        if r.id != new.id
        and r.meta.get("category") == new.meta["category"]
        and r.meta.get("status") == "in_force"
        and r.meta.get("congress") != new.meta["congress"]
        and r.id not in new.meta["replaces"]
    }
    for _, score, rid in process.extract(new.meta["title"], candidates, scorer=fuzz.WRatio, limit=3, score_cutoff=SUGGESTION_CUTOFF):
        old = ctx["by_id"][rid]
        plan.replacements.append(
            f"{label} may replace {old.meta.get('number')} {old.meta.get('title')} ({rid}), similar title ({score:.0f}%). "
            "If so, add it to 'replaces'."
        )


# ---------------------------------------------------------------- writing


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archived_copy(path: Path, root: Path) -> Path | None:
    """The file in imported/ with exactly the same content, if any."""
    digest = sha256(path)
    return next((p for p in sorted((root / "imported").glob("*")) if p.is_file() and sha256(p) == digest), None)


def archive_upload(path: Path, root: Path) -> None:
    """Move an uploaded file from imports/ to imported/, where it is kept for the record.

    New content under a name that is already taken is stored as <name>_2, _3, ...; an identical
    re-upload is already stored, so its copy in imports/ is just removed.
    """
    if path.parent.resolve() != (root / "imports").resolve():
        return  # files given on the command line from elsewhere stay where they are
    if archived_copy(path, root):
        path.unlink()
        return
    target_dir = root / "imported"
    target_dir.mkdir(exist_ok=True)
    target, n = target_dir / path.name, 2
    while target.exists():
        target, n = target_dir / f"{path.stem}_{n}{path.suffix}", n + 1
    path.rename(target)


def append_congress_stub(root: Path, entry: dict) -> None:
    """Append to congresses.yml as text, so its comments stay."""
    path = root / "congresses.yml"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text.strip() == "[]":
        text = ""
    stub = pbook.dump_yaml([entry])
    path.write_text(text.rstrip("\n") + ("\n" if text else "") + stub, encoding="utf-8")


def report_markdown(plans: list[Plan]) -> str:
    out = []
    for plan in plans:
        out.append(f"# Import {plan.congress}\n\nFiles: {', '.join(p.name for p in plan.sources)}\n")
        sections = [
            (f"Imported ({len(plan.imported)})", plan.imported),
            (f"Import errors ({len(plan.errors)})", plan.errors),
            ("To check in the status list", plan.unconfirmed),
            ("Replaced and archived resolutions", plan.replacements),
            ("Notes", plan.notes),
            ("Skipped", plan.skipped),
        ]
        for heading, items in sections:
            if items or heading.startswith("Imported"):
                out.append(f"## {heading}\n")
                out.append("\n".join(f"- {item}" for item in items) if items else "- nothing")
                out.append("")
    return "\n".join(out).strip() + "\n"


def write_reports(root: Path, plans: list[Plan]) -> None:
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "import-report.md").write_text(report_markdown(plans), encoding="utf-8")
    failed = [plan for plan in plans if plan.errors]
    error_file = dist / "import-errors.md"
    if failed:
        names = ", ".join(p.name for plan in failed for p in plan.sources)
        lines = [f"# Import incomplete: {names}", "", "Some motions were not imported. The other motions were imported and published.", ""]
        lines += [f"- {error}" for plan in failed for error in plan.errors]
        lines += [
            "",
            "What to do: fix these motions in OpenSlides as described, export again and upload the new file(s) "
            "to the imports folder with the same name. Motions that are already imported are skipped. "
            "Close this issue when everything is imported.",
        ]
        error_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        error_file.unlink(missing_ok=True)

    unconfirmed = [plan for plan in plans if plan.unconfirmed]
    check_file = dist / "status-check.md"
    if unconfirmed:
        lines = [f"# Please check the status list: {', '.join(plan.congress for plan in unconfirmed)}", ""]
        lines += [f"- {item}" for plan in unconfirmed for item in plan.unconfirmed]
        lines += [
            "",
            f"What to do: download {status_list.FILE}, make the changes described above if they are right, and "
            "upload it again (Add file → Upload files, same name). The resolutions are then archived automatically. "
            "Close this issue when done.",
        ]
        check_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        check_file.unlink(missing_ok=True)

    stubs = [plan.congress_stub for plan in plans if plan.congress_stub]
    congress_file = dist / "new-congress.md"
    if stubs:
        lines = [f"# New congress: {', '.join(stub['id'] for stub in stubs)}", ""]
        for stub in stubs:
            lines += [
                f"The upload for {stub['id']} belongs to a congress that was not in the list of congresses yet. "
                "Its resolutions were imported, and the congress was added to congresses.yml with placeholders:",
                "",
                "```yaml",
                pbook.dump_yaml([stub]).strip(),
                "```",
                "",
            ]
        lines += [
            "What to do: open congresses.yml, click the pencil icon, correct the name and the two dates "
            "(first and last day of the congress, written like 2026-10-25) and click Commit changes. "
            "The adoption dates of the new resolutions are then corrected automatically. Close this issue when done.",
        ]
        congress_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        congress_file.unlink(missing_ok=True)


def write_failure(root: Path, files: list[Path], message: str) -> None:
    """Text of the "Import failed" issue (see the module docstring)."""
    names = ", ".join(p.name for p in files or uploads(root)) or "upload"
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    text = (
        f"# Import failed: {names}\n\n{message}\n\n"
        "Nothing was imported or published; the Policy Book online is unchanged. "
        "When the corrected file is uploaded, the import runs again by itself.\n"
    )
    (dist / "import-failed.md").write_text(text, encoding="utf-8")


def run(files: list[Path], root: Path, force: bool = False, accepted_states=ACCEPTED_STATES, today: dt.date | None = None) -> list[Plan]:
    """Plan every upload first; write only if no file-level problem was found. Raises ImportFailure."""
    resolutions = pbook.load_resolutions(root)
    ctx = {
        "categories": pbook.load_categories(root),
        "congresses": pbook.load_congresses(root),
        "resolutions": resolutions,
        "by_id": {r.id: r for r in resolutions},
        "accepted_states": accepted_states,
        "today": today or dt.date.today(),
    }
    try:
        ctx["status_list"] = status_list.load(root)
    except status_list.StatusListError as exc:
        raise ImportFailure(str(exc)) from None
    groups = find_sources(files, root)
    plans, duplicates = [], []
    known = {str(c.get("id")).lower(): str(c.get("id")) for c in ctx["congresses"]}
    for congress, sources in sorted(groups.items()):
        congress = known.get(congress.lower(), congress)  # use the spelling of congresses.yml
        if not force and all(archived_copy(p, root) for p in sources):
            duplicates += sources  # exactly these files were imported before
            continue
        plans.append(plan_upload(congress, sources, root, ctx, force))

    for plan in plans:
        for path in plan.removals:
            path.unlink(missing_ok=True)
        for path, meta, body in plan.writes:
            pbook.write_resolution(path, meta, body)
        if plan.congress_stub:
            append_congress_stub(root, plan.congress_stub)
    for path in [p for plan in plans for p in plan.sources] + duplicates:
        archive_upload(path, root)
    if plans:
        write_reports(root, plans)
    return plans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import accepted motions from an OpenSlides export.")
    parser.add_argument("files", nargs="*", type=Path, help="CSV/PDF files (default: new files in imports/)")
    parser.add_argument("--force", action="store_true", help="re-import files and overwrite existing resolutions")
    parser.add_argument("--accepted-states", default=",".join(ACCEPTED_STATES), help="comma-separated states that count as accepted")
    parser.add_argument("--root", type=Path, default=pbook.ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    states = [s.strip() for s in args.accepted_states.split(",") if s.strip()]
    (args.root / "dist" / "import-failed.md").unlink(missing_ok=True)
    try:
        plans = run([f.resolve() for f in args.files], args.root.resolve(), force=args.force, accepted_states=states)
    except ImportFailure as exc:
        write_failure(args.root.resolve(), args.files, str(exc))
        print(f"Import failed: {exc}")
        return 1
    if not plans:
        print("Nothing new to import.")
        return 0
    print(report_markdown(plans))
    return 0


if __name__ == "__main__":
    sys.exit(main())
