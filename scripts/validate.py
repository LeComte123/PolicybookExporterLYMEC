"""Check the resolution database (resolutions/, categories.yml, congresses.yml) for errors.

Usage: uv run scripts/validate.py [--fix]

--fix writes derived fields back: if A replaces B, then B gets status 'superseded' and
superseded_by A (and the other way round: B.superseded_by = A adds B to A.replaces).
Exit code 1 if any problem is left.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pbook


class Report:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.fixed: list[str] = []
        self.checked = 0

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)


def check_categories(root: Path, report: Report) -> dict[str, int]:
    """Return {category id: chapter}."""
    categories = {}
    chapters = set()
    for i, cat in enumerate(pbook.load_categories(root), start=1):
        cid, chapter = cat.get("id"), cat.get("chapter")
        if not cid or not cat.get("title"):
            report.error(f"categories.yml: entry {i} needs an 'id' and a 'title'.")
            continue
        if not isinstance(chapter, int) or not 1 <= chapter <= 9:
            report.error(f"categories.yml: '{cid}' needs a chapter number from 1 to 9, e.g. 'chapter: 6'.")
            continue
        if cid in categories or chapter in chapters:
            report.error(f"categories.yml: id '{cid}' or chapter {chapter} appears twice. Each must be unique.")
        categories[cid] = chapter
        chapters.add(chapter)
    if not categories:
        report.error("categories.yml is missing or empty. Restore it from the git history.")
    return categories


def check_congresses(root: Path, report: Report) -> set[str]:
    congresses = set()
    for i, con in enumerate(pbook.load_congresses(root), start=1):
        cid = con.get("id")
        if not cid or not con.get("name"):
            report.error(f"congresses.yml: entry {i} needs an 'id' and a 'name'.")
            continue
        cid = str(cid)
        if not pbook.CONGRESS_ID_RE.match(cid):
            report.error(f"congresses.yml: id '{cid}' should look like 2026-VNO (year, dash, city code in capitals).")
        if cid in congresses:
            report.error(f"congresses.yml: id '{cid}' appears twice. Remove one of the entries.")
        congresses.add(cid)
        start, end = con.get("date_start"), con.get("date_end")
        if start is None or end is None:
            report.warn(f"congresses.yml: '{cid}' has no date_start/date_end yet. Add the congress dates.")
        for key in ("date_start", "date_end"):
            if con.get(key) is not None and pbook.parse_date(con[key]) is None:
                report.error(f"congresses.yml: '{cid}' {key} '{con[key]}' is not a date. Write it as YYYY-MM-DD.")
        if pbook.parse_date(start) and pbook.parse_date(end) and pbook.parse_date(start) > pbook.parse_date(end):
            report.error(f"congresses.yml: '{cid}' ends before it starts. Check date_start and date_end.")
    return congresses


def check_resolution(res: pbook.Resolution, categories: dict, congresses: set, report: Report) -> None:
    name = f"resolutions/{res.path.name}"
    meta = res.meta
    if not meta:
        report.error(f"{name}: the file has no header. It must start with a '---' block like the other files.")
        return

    for field in pbook.REQUIRED_FIELDS:
        if meta.get(field) in (None, "", []):
            report.error(f"{name}: required field '{field}' is missing or empty. Add it to the header.")
    for field in pbook.LIST_FIELDS:
        value = meta.get(field, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            report.error(f"{name}: '{field}' must be a list, e.g. '{field}: [2026-VNO-05]' or '{field}: []'.")
    for field in meta:
        if field not in pbook.FIELD_ORDER:
            report.warn(f"{name}: unknown field '{field}' is ignored. Check the spelling.")

    rid = meta.get("id")
    if rid and str(rid) != res.path.stem:
        report.error(f"{name}: id '{rid}' does not match the filename. Rename the file to {rid}.md or set id: {res.path.stem}.")

    number, category = meta.get("number"), meta.get("category")
    if number is not None:
        if not isinstance(number, str):
            report.error(f'{name}: write the number in quotes, e.g. number: "6.10" (without quotes 6.10 is read as 6.1).')
        elif not (match := pbook.NUMBER_RE.match(number)):
            report.error(f'{name}: number \'{number}\' is not a Policy Book number like "6.21".')
        elif category in categories and int(match.group(1)) != categories[category]:
            report.error(
                f"{name}: number {number} is in chapter {match.group(1)}, but category '{category}' is chapter "
                f"{categories[category]}. Change the number or the category."
            )

    if category is not None and category not in categories:
        report.error(f"{name}: category '{category}' does not exist. Use one of: {', '.join(categories)}.")
    congress = meta.get("congress")
    if congress is not None and str(congress) not in congresses:
        report.error(f"{name}: congress '{congress}' is not in congresses.yml. Add it there or fix the congress field.")
    if meta.get("status") is not None and meta["status"] not in pbook.STATUSES:
        report.error(f"{name}: status '{meta['status']}' is not allowed. Use one of: {', '.join(pbook.STATUSES)}.")
    if meta.get("source") is not None and meta["source"] not in pbook.SOURCES:
        report.error(f"{name}: source '{meta['source']}' is not allowed. Use one of: {', '.join(pbook.SOURCES)}.")
    if meta.get("adopted") is not None and pbook.parse_date(meta["adopted"]) is None:
        report.error(f"{name}: adopted '{meta['adopted']}' is not a date. Write it as YYYY-MM-DD.")
    if meta.get("title") is not None and not isinstance(meta["title"], str):
        report.error(f'{name}: write the title in quotes, e.g. title: "My title".')
    osn = meta.get("openslides_number")
    if osn is not None and not isinstance(osn, str):
        report.error(f'{name}: write openslides_number in quotes, e.g. openslides_number: "05".')
    if not res.body.strip():
        report.error(f"{name}: the resolution text is empty. Add the text below the header.")


def check_uniques(resolutions: list[pbook.Resolution], report: Report) -> None:
    seen_ids: dict[str, str] = {}
    seen_numbers: dict[str, str] = {}
    for res in resolutions:
        rid, number = res.id, res.meta.get("number")
        if rid and rid in seen_ids:
            report.error(f"id '{rid}' is used by both {seen_ids[rid]} and {res.path.name}. Each id must be unique.")
        seen_ids.setdefault(rid, res.path.name)
        if isinstance(number, str):
            if number in seen_numbers:
                report.error(
                    f"number {number} is used by both {seen_numbers[number]} and {res.path.name}. "
                    "Give one of them the next free number in its chapter."
                )
            seen_numbers.setdefault(number, res.path.name)


def check_links(resolutions: list[pbook.Resolution], by_id: dict, report: Report) -> None:
    for res in resolutions:
        name = f"resolutions/{res.path.name}"
        targets = [("replaces", t) for t in res.meta.get("replaces") or [] if isinstance(t, str)]
        targets += [("amends", t) for t in res.meta.get("amends") or [] if isinstance(t, str)]
        if res.meta.get("superseded_by"):
            targets.append(("superseded_by", str(res.meta["superseded_by"])))
        for field, target in targets:
            if target == res.id:
                report.error(f"{name}: '{field}' points to the resolution itself. Remove '{target}'.")
            elif target not in by_id:
                report.error(f"{name}: '{field}' points to '{target}', which does not exist. Check the id (it is the filename without .md).")


def derive_supersessions(resolutions: list[pbook.Resolution], by_id: dict, report: Report, fix: bool) -> None:
    """Keep 'A replaces B' and 'B superseded_by A' consistent; write back with --fix."""
    replacers: dict[str, set[str]] = {}
    for res in resolutions:
        for target in res.meta.get("replaces") or []:
            if isinstance(target, str) and target in by_id and target != res.id:
                replacers.setdefault(target, set()).add(res.id)
        sup = res.meta.get("superseded_by")
        if sup and str(sup) in by_id and str(sup) != res.id:
            replacers.setdefault(res.id, set()).add(str(sup))

    dirty: dict[str, pbook.Resolution] = {}
    for old_id, new_ids in sorted(replacers.items()):
        old = by_id[old_id]
        if len(new_ids) > 1:
            report.error(
                f"resolutions/{old.path.name} is replaced by more than one resolution ({', '.join(sorted(new_ids))}). "
                "Keep it in only one 'replaces' list and set superseded_by to that id."
            )
            continue
        new = by_id[next(iter(new_ids))]
        changes = []
        if old.meta.get("superseded_by") != new.id:
            changes.append((old, "superseded_by", new.id))
        if old.meta.get("status") != "superseded":
            changes.append((old, "status", "superseded"))
        new_replaces = new.meta.get("replaces") or []
        if isinstance(new_replaces, list) and old.id not in new_replaces:
            changes.append((new, "replaces", sorted(new_replaces + [old.id])))
        for res, field, value in changes:
            msg = f"resolutions/{res.path.name}: set {field} to {value} ({new.id} replaces {old.id})"
            if fix:
                res.meta[field] = value
                dirty[res.id] = res
                report.fixed.append(msg)
            else:
                report.error(msg + ". Run validate.py --fix to do this automatically, or edit the file.")

    for res in resolutions:
        if res.meta.get("status") == "superseded" and res.id not in replacers:
            report.error(
                f"resolutions/{res.path.name}: status is 'superseded' but no resolution replaces it. "
                "Set superseded_by to the id of the newer resolution, or change the status."
            )
    for res in dirty.values():
        res.save()


def validate(root: Path = pbook.ROOT, fix: bool = False) -> Report:
    report = Report()
    categories = check_categories(root, report)
    congresses = check_congresses(root, report)

    resolutions = []
    for path in pbook.resolution_paths(root):
        try:
            resolutions.append(pbook.load_resolution(path))
        except ValueError as exc:
            report.error(f"resolutions/{path.name}: {exc}. Compare the header with another resolution file.")
    report.checked = len(resolutions)

    for res in resolutions:
        check_resolution(res, categories, congresses, report)
    check_uniques(resolutions, report)
    by_id = {res.id: res for res in resolutions if res.id}
    check_links(resolutions, by_id, report)
    derive_supersessions(resolutions, by_id, report, fix)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the resolution database for errors.")
    parser.add_argument("--fix", action="store_true", help="write derived superseded_by/status/replaces back to the files")
    parser.add_argument("--root", type=Path, default=pbook.ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    report = validate(args.root, fix=args.fix)
    if report.fixed:
        print("Fixed automatically:")
        print("\n".join(f"  - {msg}" for msg in report.fixed))
    if report.warnings:
        print("Warnings (nothing blocked):")
        print("\n".join(f"  - {msg}" for msg in report.warnings))
    if report.errors:
        print(f"{len(report.errors)} problem(s) found in {report.checked} resolution(s):")
        print("\n".join(f"  - {msg}" for msg in report.errors))
        return 1
    print(f"OK: {report.checked} resolution(s) checked, no problems found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
