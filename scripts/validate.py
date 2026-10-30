"""Check the resolution database (resolutions/, categories.yml, congresses.yml) for errors.

Usage: uv run scripts/validate.py [--fix]

--fix repairs what can be derived, so people can edit files in the GitHub web editor:
- a Policy Book number written in replaces/amends/superseded_by (6.03) becomes the id;
- if A replaces B, then B gets status 'superseded' and superseded_by A (and the other way round:
  B.superseded_by = A adds B to A.replaces);
- a file whose number was changed is renamed to <number>_<id>.md;
- an adoption date outside the dates of its congress, or a missing one, is set to the last day of the congress;
- a resolution marked 'archived' in the status list (resolution_status_list.csv) is archived, or
  superseded if the list names the resolution that replaced it; resolutions in force that are
  missing from the list get a row, a missing congress is taken from the list's 'Adopted at', and
  the list is written in its standard form.
Exit code 1 if any problem is left.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pbook
import status_list


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


def check_congresses(root: Path, report: Report) -> dict[str, dict]:
    """Return {congress id: entry}."""
    congresses = {}
    for i, con in enumerate(pbook.load_congresses(root), start=1):
        cid = con.get("id")
        if not cid or not con.get("name"):
            report.error(f"congresses.yml: entry {i} needs an 'id' and a 'name'.")
            continue
        cid = str(cid)
        if not pbook.CONGRESS_ID_RE.match(cid):
            report.error(f"congresses.yml: id '{cid}' should look like 2026-Vilnius (year, dash, city in English).")
        if cid in congresses:
            report.error(f"congresses.yml: id '{cid}' appears twice. Remove one of the entries.")
        congresses[cid] = con
        start, end = con.get("date_start"), con.get("date_end")
        if start is None or end is None:
            report.warn(f"congresses.yml: '{cid}' has no date_start/date_end yet. Add the congress dates.")
        for key in ("date_start", "date_end"):
            if con.get(key) is not None and pbook.parse_date(con[key]) is None:
                report.error(f"congresses.yml: '{cid}' {key} '{con[key]}' is not a date. Write it as YYYY-MM-DD.")
        if pbook.parse_date(start) and pbook.parse_date(end) and pbook.parse_date(start) > pbook.parse_date(end):
            report.error(f"congresses.yml: '{cid}' ends before it starts. Check date_start and date_end.")
    return congresses


def check_resolution(res: pbook.Resolution, categories: dict, congresses: dict, report: Report) -> None:
    name = f"resolutions/{res.path.name}"
    meta = res.meta
    if not meta:
        report.error(f"{name}: the file has no header. It must start with a '---' block like the other files.")
        return

    migrated = meta.get("source") == "migration"
    for field in pbook.REQUIRED_FIELDS:
        if meta.get(field) in (None, "", []):
            if migrated and field in pbook.UNKNOWN_ALLOWED_FOR_MIGRATION:
                if field == "congress":
                    report.warn(
                        f"{name}: the congress is not known yet. Enter it in the status list (column 'Adopted at') "
                        "or in the file (congress: …)."
                    )
                continue
            report.error(f"{name}: required field '{field}' is missing or empty. Add it to the header.")
    for field in pbook.LIST_FIELDS:
        value = meta.get(field, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            report.error(f"{name}: '{field}' must be a list: one entry per line starting with '- ', or '{field}: []'.")
    for field in meta:
        if field not in pbook.FIELD_ORDER:
            report.warn(f"{name}: unknown field '{field}' is ignored. Check the spelling.")

    rid, congress = meta.get("id"), meta.get("congress")
    if rid and congress and not migrated and not str(rid).startswith(f"{congress}-"):
        report.error(f"{name}: id '{rid}' must start with its congress, e.g. {congress}-05 (congress, dash, motion number).")

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
    """Ids are unique; numbers are unique among the resolutions in force (renumbering may give the
    number of an archived resolution to another one)."""
    seen_ids: dict[str, str] = {}
    seen_numbers: dict[str, str] = {}
    for res in resolutions:
        rid, number = res.id, res.meta.get("number")
        if rid and rid in seen_ids:
            report.error(f"id '{rid}' is used by both {seen_ids[rid]} and {res.path.name}. Each id must be unique.")
        seen_ids.setdefault(rid, res.path.name)
        if isinstance(number, str) and res.meta.get("status") == "in_force":
            if number in seen_numbers:
                report.error(
                    f"number {number} is used by two resolutions in force: {seen_numbers[number]} and {res.path.name}. "
                    "Give one of them the next free number in its chapter."
                )
            seen_numbers.setdefault(number, res.path.name)


def as_number(value) -> str | None:
    """A Policy Book number written where an id is expected: '6.03', or 6.03 without quotes (YAML reads a float)."""
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, str) and pbook.NUMBER_RE.match(value.strip()):
        return value.strip()
    return None


def resolve_number_references(resolutions: list[pbook.Resolution], report: Report, fix: bool) -> set[str]:
    """Replace Policy Book numbers in replaces/amends/superseded_by by ids (in memory; saved with --fix).

    A number means the resolution in force with that number, else the only resolution with it.
    Returns the ids of the changed resolutions.
    """
    by_number: dict[str, list[pbook.Resolution]] = {}
    for res in resolutions:
        by_number.setdefault(res.number, []).append(res)

    def target(number: str) -> pbook.Resolution | None:
        found = by_number.get(number, [])
        in_force = [r for r in found if r.meta.get("status") == "in_force"]
        found = in_force or found
        return found[0] if len(found) == 1 else None

    changed = set()
    for res in resolutions:
        name = f"resolutions/{res.path.name}"
        for field in ("replaces", "amends", "superseded_by"):
            if field not in res.meta:
                continue
            value = res.meta[field]
            items = value if isinstance(value, list) else [value]
            new_items = []
            for item in items:
                number = as_number(item)
                found = target(number) if number else None
                if number and found is None:
                    report.error(f"{name}: '{field}' points to number {number}, but no single resolution has that number. Check the number.")
                    new_items.append(number)
                elif found:
                    new_items.append(found.id)
                    if fix:
                        report.fixed.append(f"{name}: {field} {number} written as its id {found.id}")
                        changed.add(res.id)
                    else:
                        report.error(f"{name}: write {found.id} instead of {number} in '{field}' (validate.py --fix does this).")
                else:
                    new_items.append(item)
            res.meta[field] = new_items if isinstance(value, list) else new_items[0]
    return changed


def check_file_names(resolutions: list[pbook.Resolution], report: Report, fix: bool) -> None:
    """Files are named <number>_<id>.md; --fix renames them (e.g. after the number was changed)."""
    for res in resolutions:
        number, rid = res.meta.get("number"), res.meta.get("id")
        if not (isinstance(number, str) and pbook.NUMBER_RE.match(number) and rid):
            continue  # reported by check_resolution
        expected = pbook.file_name(res.meta)
        if res.path.name == expected:
            continue
        target = res.path.with_name(expected)
        if fix and not target.exists():
            res.path.rename(target)
            report.fixed.append(f"resolutions/{res.path.name}: renamed to {expected} (number and id)")
            res.path = target
        else:
            report.error(f"resolutions/{res.path.name}: the file name must be {expected} (number, _, id). Rename the file.")


def check_adoption_dates(resolutions: list[pbook.Resolution], congresses: dict, report: Report, fix: bool) -> set[str]:
    """The adoption date lies within the dates of the congress; --fix sets it to the last day.

    This also corrects the placeholder date the importer uses for a congress that was not in
    congresses.yml yet, as soon as the real dates are entered there.
    """
    changed = set()
    for res in resolutions:
        con = congresses.get(str(res.meta.get("congress")), {})
        start, end = pbook.parse_date(con.get("date_start")), pbook.parse_date(con.get("date_end"))
        adopted = pbook.parse_date(res.meta.get("adopted"))
        if res.meta.get("adopted") is None and end:  # e.g. a migrated resolution whose congress is now known
            if fix:
                res.meta["adopted"] = end
                report.fixed.append(f"resolutions/{res.path.name}: adopted set to {end}, the last day of its congress")
                changed.add(res.id)
            else:
                report.warn(f"resolutions/{res.path.name}: no adoption date yet (validate.py --fix uses the last day of the congress).")
            continue
        if not (start and end and adopted) or start <= adopted <= end:
            continue
        msg = f"resolutions/{res.path.name}: adopted {adopted} is not within the dates of congress {res.meta['congress']} ({start} to {end})"
        if fix:
            res.meta["adopted"] = end
            report.fixed.append(msg + f"; set to {end}")
            changed.add(res.id)
        else:
            report.error(msg + ". Correct the date, or run validate.py --fix to use the last day of the congress.")
    return changed


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
                report.error(
                    f"{name}: '{field}' points to '{target}', which does not exist. Write the Policy Book number "
                    "(e.g. '6.03') or the id (the file name after the _, e.g. 2026-Vilnius-05)."
                )


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
                "Keep it in only one 'replaces' list and set superseded_by to that resolution."
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
                "Set superseded_by to the number or id of the newer resolution, or change the status."
            )
    for res in dirty.values():
        res.save()


def check_status_list(root: Path, resolutions: list[pbook.Resolution], congresses: dict, report: Report, fix: bool) -> set[str]:
    """Apply the Bureau's status list: rows marked 'archived' archive the resolution with that number.

    A row with a title only matches a resolution with that title (protects against renumbering
    mix-ups). A row is already satisfied when a resolution with its number is no longer in force.
    With --fix, rows for new resolutions are added and the file is written in its standard form.
    """
    name = status_list.FILE
    try:
        table = status_list.read(root)
    except status_list.StatusListError as exc:
        report.error(str(exc))
        return set()
    if table is None:
        return set()
    header, records = table
    rows = status_list.rows_of(records)
    for row in rows:
        if not pbook.NUMBER_RE.match(row.number):
            report.error(f"{name} line {row.line}: '{row.number}' is not a Policy Book number like 6.21. Correct it.")
        if row.status and row.status not in status_list.STATUSES:
            report.error(f"{name} line {row.line} ({row.number}): status '{row.status}' is not allowed. Write in force, archived or not used.")

    numbered = [r for r in resolutions if isinstance(r.meta.get("number"), str)]
    in_force = {r.number: r for r in numbered if r.meta.get("status") == "in_force"}
    replaced = {t for r in resolutions for t in r.meta.get("replaces") or [] if isinstance(t, str)}
    changed = set()
    without_text = []  # archived resolutions that had the number before a renumbering; text not in the database
    for row in rows:
        if row.status != "archived":
            continue
        where = f"{name} line {row.line} ({row.number})"
        if not row.title and any(r is not row and r.number == row.number and r.status == "in force" for r in rows):
            # The number has a line 'in force' as well: an old resolution that had this number before a
            # renumbering. Without its title it is unclear which one is meant, so nothing is archived.
            report.warn(f"{where}: marked archived, but another line says {row.number} is in force. Add the title of the archived resolution.")
            continue
        same = [r for r in numbered if r.number == row.number]
        matching = [r for r in same if not row.title or status_list.titles_match(row.title, str(r.meta.get("title", "")))]
        if any(r.meta.get("status") != "in_force" for r in matching):
            continue  # already archived or superseded
        target = next((r for r in matching if r.meta.get("status") == "in_force"), None)
        if target is None:
            if row.title:
                without_text.append(row)
            else:
                report.warn(f"{where}: marked archived, but there is no resolution with this number in the database. Check the number.")
            continue
        if target.id in replaced:
            continue  # superseded by the resolution that replaces it (derive_supersessions)
        replacer = None
        if row.replaced_by:
            replacer = in_force.get(row.replaced_by)
            if replacer is None or replacer is target:
                report.error(f"{where}: 'Replaced by' {row.replaced_by} is not a resolution in force. Correct the number.")
                continue
        result = f"superseded by {row.replaced_by}" if replacer else "archived"
        if not fix:
            report.error(f"resolutions/{target.path.name}: the status list marks it as archived. Run validate.py --fix to make it {result}.")
            continue
        if replacer:
            replacer.meta["replaces"] = sorted(set(replacer.meta.get("replaces") or []) | {target.id})
            changed.add(replacer.id)
        else:
            target.meta["status"] = "archived"
            changed.add(target.id)
        report.fixed.append(f"resolutions/{target.path.name}: {result}, as marked in the status list")
    if without_text:
        report.warn(
            f"{name}: {len(without_text)} archived resolution(s) are not in the database (older resolutions whose texts are "
            "missing); nothing changes for them. A line meant to archive a resolution in force needs that resolution's title."
        )

    for row in rows:  # 'Adopted at' completes resolutions whose congress is not known yet (migration)
        if row.status != "in force" or not row.adopted_at:
            continue
        target = in_force.get(row.number)
        if target is None or target.meta.get("congress") or (row.title and not status_list.titles_match(row.title, str(target.meta.get("title", "")))):
            continue
        if row.adopted_at not in congresses:
            report.error(f"{name} line {row.line} ({row.number}): 'Adopted at' {row.adopted_at} is not in congresses.yml. Add the congress there or correct it.")
        elif fix:
            target.meta["congress"] = row.adopted_at
            changed.add(target.id)
            report.fixed.append(f"resolutions/{target.path.name}: congress set to {row.adopted_at}, as in the status list")
        else:
            report.error(f"resolutions/{target.path.name}: the status list says it was adopted at {row.adopted_at}. Run validate.py --fix to fill it in.")

    for res in numbered:
        if res.meta.get("status") in ("archived", "superseded") and any(
            row.number == res.number and row.status == "in force" and (not row.title or status_list.titles_match(row.title, str(res.meta.get("title", ""))))
            for row in rows
        ):
            report.warn(f"resolutions/{res.path.name} is {res.meta['status']}, but the status list says 'in force'. Update the status list.")

    def listed(res):  # a line of an old resolution with the same number and another title does not count
        title = str(res.meta.get("title", ""))
        return any(
            row.number == res.number and (row.status != "archived" or not row.title or status_list.titles_match(row.title, title))
            for row in rows
        )

    missing = sorted((r for r in in_force.values() if not listed(r)), key=lambda r: status_list.number_key(r.number))
    if not fix:
        if missing:
            report.warn(f"{name}: no row yet for {', '.join(r.number for r in missing)} (validate.py --fix adds them).")
        return changed
    if status_list.write(root, header, records + [status_list.new_record(r) for r in missing]):
        added = f"rows added for {', '.join(r.number for r in missing)}; " if missing else ""
        report.fixed.append(f"{name}: {added}written in the standard form (sorted, comma separated)")
    return changed


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

    changed = resolve_number_references(resolutions, report, fix)
    for res in resolutions:
        check_resolution(res, categories, congresses, report)
    check_uniques(resolutions, report)
    check_file_names(resolutions, report, fix)
    changed |= check_status_list(root, resolutions, congresses, report, fix)
    changed |= check_adoption_dates(resolutions, congresses, report, fix)
    by_id = {res.id: res for res in resolutions if res.id}
    for rid in changed:
        by_id[rid].save()
    check_links(resolutions, by_id, report)
    derive_supersessions(resolutions, by_id, report, fix)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the resolution database for errors.")
    parser.add_argument("--fix", action="store_true", help="repair what can be derived (see the top of this file)")
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
