"""Build the downloads: one PDF + DOCX per resolution, the two Policy Book editions and the download page.

Usage: uv run scripts/build.py [--only RESOLUTION] [--edition current|archive|all] [--date YYYY-MM-DD]

Without options everything is built:
  dist/resolutions/<number>_<id>.pdf and .docx (named like the resolution file)
  dist/LYMEC_Policy_Book.pdf          resolutions in force
  dist/LYMEC_Policy_Book_Archive.pdf  all resolutions, a banner on those no longer in force
  site/                               download page with all files, ready for GitHub Pages
--only builds just that resolution's PDF and DOCX (give its number, id or file name);
--edition builds just that Policy Book.
Nothing is built if validate.py reports a problem.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

import pbook
import validate

EDITIONS = {  # file name, subtitle on the cover, short name in the footer
    "current": ("LYMEC_Policy_Book.pdf", "Resolutions in force", "Resolutions in force"),
    "archive": ("LYMEC_Policy_Book_Archive.pdf", "Archive edition: all resolutions, including those no longer in force", "Archive edition"),
}
STATUS_LABELS = {"in_force": "In force", "archived": "Archived", "superseded": "Superseded", "expired": "Expired"}
MD_SPECIAL_RE = re.compile(r"([\\`*_{}\[\]<>#!|$@~^])")


class BuildError(Exception):
    pass


class Context:
    """Everything loaded once: categories, congresses, resolutions and derived links."""

    def __init__(self, root: Path, build_date: dt.date):
        self.root = root
        self.build_date = build_date
        self.categories = {c["id"]: c for c in pbook.load_categories(root)}
        self.congresses = {str(c["id"]): c for c in pbook.load_congresses(root)}
        self.resolutions = sorted(pbook.load_resolutions(root), key=number_key)
        self.by_id = {r.id: r for r in self.resolutions}
        self.amended_by: dict[str, list[str]] = {}
        for res in self.resolutions:
            for target in res.meta.get("amends") or []:
                self.amended_by.setdefault(target, []).append(res.id)
        self.templates = root / "templates"
        self.logo = root / "docs" / "images" / "lymec_logo.png"
        self.dist = root / "dist"


def number_key(res: pbook.Resolution) -> tuple:
    match = pbook.NUMBER_RE.match(str(res.meta.get("number", "")))
    return (int(match.group(1)), int(match.group(2))) if match else (99, 0)


# ---------------------------------------------------------------- texts shown in the documents


def md_escape(text) -> str:
    """Escape text for pandoc Markdown (titles, organisation names from the YAML header)."""
    text = MD_SPECIAL_RE.sub(r"\\\1", str(text))
    return re.sub(r"^([-+])", r"\\\1", text)


def format_date(value) -> str:
    date = pbook.parse_date(value)
    return f"{date.day} {date:%B %Y}" if date else ""


UNKNOWN_CONGRESS = "Congress not known"


def congress_name(ctx: Context, cid: str | None) -> str:
    if not cid:
        return UNKNOWN_CONGRESS  # some resolutions of the old Policy Book (until the Bureau fills it in)
    con = ctx.congresses.get(str(cid), {})
    name, city = con.get("name") or str(cid), con.get("city")
    return f"{name}, {city}" if city and city not in name else name


def label(ctx: Context, rid: str) -> str:
    res = ctx.by_id[rid]
    return f"{res.meta['number']} {res.meta['title']}"


def ref(ctx: Context, rid: str, linked: set[str]) -> str:
    """Markdown reference to another resolution; a link if it is in the same document."""
    text = md_escape(label(ctx, rid))
    return f"[{text}](#{rid})" if rid in linked else text


def refs(ctx: Context, ids, linked: set[str]) -> str:
    return "; ".join(ref(ctx, rid, linked) for rid in ids if rid in ctx.by_id)


def banner(ctx: Context, res: pbook.Resolution, linked: set[str]) -> str:
    status = res.meta["status"]
    if status == "superseded" and res.meta.get("superseded_by") in ctx.by_id:
        return f"Superseded by {ref(ctx, res.meta['superseded_by'], linked)}. This resolution is no longer in force."
    if status != "in_force":
        return f"{STATUS_LABELS[status]}. This resolution is no longer in force."
    return ""


def facts(ctx: Context, res: pbook.Resolution, linked: set[str]) -> list[tuple[str, str]]:
    """(label, Markdown value) rows describing a resolution, shared by all output formats."""
    meta = res.meta
    category = ctx.categories[meta["category"]]
    rows = [
        ("Adopted", md_escape(", ".join(x for x in (congress_name(ctx, meta.get("congress")), format_date(meta.get("adopted"))) if x))),
        ("Chapter", md_escape(f"Chapter {category['chapter']} – {category['title']}")),
        ("Status", STATUS_LABELS[meta["status"]]),
        ("Submitted by", ", ".join(md_escape(o) for o in meta.get("submitted_by") or [])),
        ("Co-signers", ", ".join(md_escape(o) for o in meta.get("co_signers") or [])),
        ("Replaces", refs(ctx, meta.get("replaces") or [], linked)),
        ("Amends", refs(ctx, meta.get("amends") or [], linked)),
        ("Amended by", refs(ctx, ctx.amended_by.get(res.id, []), linked)),
    ]
    return [(name, value) for name, value in rows if value]


# ---------------------------------------------------------------- pandoc


def run_pandoc(args: list, ctx: Context, input_text: str, what: str) -> str:
    epoch = int(dt.datetime.combine(ctx.build_date, dt.time(), dt.timezone.utc).timestamp())
    # PDF creation date = build date. (Byte-identical PDFs are not possible: xdvipdfmx picks random font subset tags.)
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(epoch), "FORCE_SOURCE_DATE": "1"}
    try:
        result = subprocess.run(["pandoc", *map(str, args)], input=input_text, capture_output=True, text=True, env=env, cwd=ctx.root)
    except FileNotFoundError:
        raise BuildError("pandoc is not installed. Install it (macOS: brew install pandoc) and build again.") from None
    if result.returncode != 0:
        if 'The font "Arial" cannot be found' in result.stderr:
            raise BuildError(
                "The font Arial is not installed on this computer. Install the Microsoft core fonts "
                "(Debian/Ubuntu: apt install ttf-mscorefonts-installer) and build again."
            )
        tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        raise BuildError(f"Building {what} failed:\n{tail}")
    return result.stdout


def to_markdown(ctx: Context, res: pbook.Resolution) -> str:
    """Resolution text (GitHub Markdown) -> pandoc Markdown, so it can be combined with the generated parts."""
    return run_pandoc(["-f", "gfm", "-t", "markdown-smart", "--wrap=none"], ctx, res.body, res.id)


def shift_headings(markdown: str, by: int) -> str:
    return re.sub(r"^(#{1,6}) ", lambda m: "#" * min(len(m.group(1)) + by, 6) + " ", markdown, flags=re.M)


def template_args(ctx: Context, template: str, meta: dict, tmp: Path) -> list:
    meta_file = tmp / "meta.yml"
    meta_file.write_text(yaml.safe_dump(meta, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return [
        "-f", "markdown-smart",
        "--lua-filter", ctx.templates / "no_code_blocks.lua",
        "--metadata-file", meta_file,
        "--template", ctx.templates / template,
        "--pdf-engine=xelatex",
        "-V", f"logo={ctx.logo}",
        "-V", "lang=en-GB",
    ]  # fmt: skip


# ---------------------------------------------------------------- one resolution


def build_single(ctx: Context, res: pbook.Resolution, body: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = dict(facts(ctx, res, set()))
    meta = {
        "title": md_escape(label(ctx, res.id)),
        "number": res.meta["number"],
        "restitle": md_escape(res.meta["title"]),
        "id": res.id,
        "adopted": rows["Adopted"],
        "chapter": rows["Chapter"],
        "status": rows["Status"],
        "submitted_by": [md_escape(o) for o in res.meta.get("submitted_by") or []],
        "co_signers": [md_escape(o) for o in res.meta.get("co_signers") or []],
        "replaces": rows.get("Replaces", ""),
        "amends": rows.get("Amends", ""),
        "amendedby": rows.get("Amended by", ""),
        "banner": banner(ctx, res, set()),
    }
    with tempfile.TemporaryDirectory() as tmp:
        args = template_args(ctx, "resolution.tex", meta, Path(tmp))
        run_pandoc([*args, "-o", out_dir / f"{res.path.stem}.pdf"], ctx, body, f"the PDF of {res.path.stem}")

    table = ["| | |", "|---|---|"] + [f"| {name} | {value} |" for name, value in facts(ctx, res, set())]
    table.append(f"| Reference | {res.id} |")
    parts = [f"![]({ctx.logo}){{width=4cm}}", f"# {md_escape(label(ctx, res.id))}", "\n".join(table)]
    if text := banner(ctx, res, set()):
        parts.append(f"**{text}**")
    parts.append(shift_headings(body, 1))
    args = ["-f", "markdown-smart", "--lua-filter", ctx.templates / "no_code_blocks.lua", "--reference-doc", ctx.templates / "reference.docx"]
    args += ["-o", out_dir / f"{res.path.stem}.docx"]
    run_pandoc(args, ctx, "\n\n".join(parts), f"the DOCX of {res.path.stem}")


# ---------------------------------------------------------------- the Policy Book


def raw(latex: str) -> str:
    return f"```{{=latex}}\n{latex}\n```"


def in_edition(res: pbook.Resolution, edition: str) -> bool:
    return edition == "archive" or res.meta["status"] == "in_force"


def edition_markdown(ctx: Context, edition: str, bodies: dict[str, str]) -> str:
    included = [r for r in ctx.resolutions if in_edition(r, edition)]
    linked = {r.id for r in included}
    parts = []
    for category in sorted(ctx.categories.values(), key=lambda c: c.get("order", c["chapter"])):
        members = [r for r in included if r.meta["category"] == category["id"]]
        if not members:
            continue
        parts.append(f"# Chapter {category['chapter']} – {md_escape(category['title'])} {{#chapter-{category['chapter']}}}")
        for i, res in enumerate(members):
            if i:
                parts.append(raw("\\clearpage"))
            parts.append(f"## {md_escape(label(ctx, res.id))} {{#{res.id}}}")
            lines = [f"**{name}:** {value}" for name, value in facts(ctx, res, linked)]
            parts += [raw("\\begin{resmeta}"), "\\\n".join(lines), raw("\\end{resmeta}")]
            if text := banner(ctx, res, linked):
                parts += [raw("\\begin{statusbanner}"), text, raw("\\end{statusbanner}")]
            parts += [raw("\\lymecrule"), shift_headings(bodies[res.id], 2)]

    parts.append("# Index by congress {#index-by-congress}")
    by_congress: dict[str, list[pbook.Resolution]] = {}
    for res in included:
        by_congress.setdefault(str(res.meta.get("congress") or ""), []).append(res)

    def congress_key(cid: str) -> tuple:
        if not cid:
            return (dt.date.max, "")  # congress not known: last
        con = ctx.congresses.get(cid, {})
        date = pbook.parse_date(con.get("date_end")) or pbook.parse_date(con.get("date_start")) or dt.date(int(cid[:4]), 1, 1)
        return (date, cid)

    for cid in sorted(by_congress, key=congress_key):
        con = ctx.congresses.get(cid, {})
        dates = format_date(con.get("date_end")) if con.get("date_end") else cid[:4]
        heading = f"{congress_name(ctx, cid)} ({dates})" if cid else UNKNOWN_CONGRESS
        parts.append(f"## {md_escape(heading)} {{.unnumbered .unlisted}}")
        entries = []
        for res in by_congress[cid]:
            status = "" if res.meta["status"] == "in_force" else f" ({STATUS_LABELS[res.meta['status']].lower()})"
            entries.append(f"[{md_escape(label(ctx, res.id))}](#{res.id}){status}`\\dotfill\\pageref{{{res.id}}}`{{=latex}}")
        parts += [raw("\\begin{congressindex}"), "\n\n".join(entries), raw("\\end{congressindex}")]
    return "\n\n".join(parts) + "\n"


def build_edition(ctx: Context, edition: str, bodies: dict[str, str]) -> Path:
    filename, subtitle, footer = EDITIONS[edition]
    included = [r for r in ctx.resolutions if in_edition(r, edition)]
    if not included:
        raise BuildError(f"There are no resolutions for the {edition} edition yet. Import or add resolutions first.")
    # year of adoption, or of the congress; resolutions of the old Policy Book may know neither yet
    years = sorted(
        date.year if (date := pbook.parse_date(r.meta.get("adopted"))) else int(str(r.meta["congress"])[:4])
        for r in included
        if r.meta.get("adopted") or r.meta.get("congress")
    ) or [ctx.build_date.year]
    meta = {
        "title": f"LYMEC Policy Book – {subtitle}",
        "edition": subtitle,
        "footer": footer,
        "years": f"{years[0]}–{years[-1]}" if years[0] != years[-1] else str(years[0]),
        "count": f"{len(included)} resolution{'s' if len(included) != 1 else ''}",
        "builddate": format_date(ctx.build_date),
    }
    ctx.dist.mkdir(exist_ok=True)
    target = ctx.dist / filename
    markdown = edition_markdown(ctx, edition, bodies)
    with tempfile.TemporaryDirectory() as tmp:
        args = template_args(ctx, "policybook.tex", meta, Path(tmp))
        args += ["--top-level-division=chapter", "-o", target]
        run_pandoc(args, ctx, markdown, f"the {edition} Policy Book")
    return target


# ---------------------------------------------------------------- download page


def build_site(ctx: Context) -> None:
    site = ctx.root / "site"
    if site.exists():
        shutil.rmtree(site)
    shutil.copytree(ctx.dist / "resolutions", site / "resolutions")
    for filename, _, _ in EDITIONS.values():
        shutil.copy(ctx.dist / filename, site / filename)
    shutil.copy(ctx.logo, site / ctx.logo.name)

    rows = []
    for res in ctx.resolutions:
        meta, category = res.meta, ctx.categories[res.meta["category"]]
        css = ' class="archived"' if meta["status"] != "in_force" else ""
        rows.append(
            f'<tr{css}><td>{html.escape(meta["number"])}</td>'
            f"<td>{html.escape(meta['title'])}</td><td>{category['chapter']}</td>"
            f"<td>{html.escape(congress_name(ctx, meta.get('congress')))}</td><td>{STATUS_LABELS[meta['status']]}</td>"
            f'<td><a href="resolutions/{res.path.stem}.pdf">PDF</a> · <a href="resolutions/{res.path.stem}.docx">Word</a></td></tr>'
        )
    page = SITE_TEMPLATE.format(
        build_date=format_date(ctx.build_date),
        logo=ctx.logo.name,
        current=EDITIONS["current"][0],
        archive=EDITIONS["archive"][0],
        count=sum(r.meta["status"] == "in_force" for r in ctx.resolutions),
        total=len(ctx.resolutions),
        rows="\n".join(rows),
    )
    (site / "index.html").write_text(page, encoding="utf-8")


SITE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LYMEC Policy Book</title>
<style>
  :root {{ --violet: #5a5fb5; --blue: #20a8e0; --pink: #e80088; --grey: #5f6368; }}
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 0 auto; max-width: 1100px; padding: 24px 16px; color: #222; background: #fff; }}
  header {{ display: flex; align-items: center; gap: 24px; flex-wrap: wrap; }}
  header img {{ height: 72px; }}
  h1 {{ color: var(--violet); margin: 0; }}
  .updated {{ color: var(--grey); }}
  .books {{ display: flex; gap: 16px; flex-wrap: wrap; margin: 24px 0; }}
  .books a {{ border: 2px solid var(--blue); border-radius: 8px; padding: 12px 18px; text-decoration: none; color: #222; }}
  .books a strong {{ display: block; color: var(--violet); }}
  input[type=search] {{ width: 100%; box-sizing: border-box; padding: 10px; font-size: 16px; border: 1px solid #bbb; border-radius: 6px; }}
  label {{ display: inline-block; margin: 10px 0; color: var(--grey); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #e3e3e3; vertical-align: top; }}
  th {{ color: var(--violet); }}
  tr.archived {{ color: var(--grey); }}
  tr.archived td:nth-child(5) {{ color: var(--pink); }}
  .table-wrap {{ overflow-x: auto; }}
  a {{ color: #1f8bc0; }}
</style>
</head>
<body>
<header>
  <img src="{logo}" alt="LYMEC logo">
  <div><h1>LYMEC Policy Book</h1><div class="updated">Last updated: {build_date}</div></div>
</header>
<div class="books">
  <a href="{current}"><strong>Policy Book (PDF)</strong>{count} resolutions in force</a>
  <a href="{archive}"><strong>Archive edition (PDF)</strong>all {total} resolutions, including archived and superseded ones</a>
</div>
<input type="search" id="search" placeholder="Search by number, title, congress …" aria-label="Search resolutions">
<label><input type="checkbox" id="show-archived"> Show resolutions that are no longer in force</label>
<div class="table-wrap">
<table>
<thead><tr><th>No.</th><th>Title</th><th>Chapter</th><th>Adopted at</th><th>Status</th><th>Download</th></tr></thead>
<tbody id="rows">
{rows}
</tbody>
</table>
</div>
<script>
  const search = document.getElementById("search");
  const showArchived = document.getElementById("show-archived");
  function filter() {{
    const words = search.value.toLowerCase().split(/\\s+/).filter(Boolean);
    for (const row of document.querySelectorAll("#rows tr")) {{
      const text = row.textContent.toLowerCase();
      const visible = (showArchived.checked || !row.classList.contains("archived")) && words.every(w => text.includes(w));
      row.hidden = !visible;
    }}
  }}
  search.addEventListener("input", filter);
  showArchived.addEventListener("change", filter);
  filter();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- main


def find(ctx: Context, text: str) -> list[pbook.Resolution]:
    """The resolution meant by a number (the one in force), an id or a file name."""
    text = text.removesuffix(".md")
    matches = [r for r in ctx.resolutions if text in (r.id, r.path.stem)]
    matches = matches or [r for r in ctx.resolutions if r.number == text and r.meta["status"] == "in_force"]
    matches = matches or [r for r in ctx.resolutions if r.number == text]
    if len(matches) != 1:
        raise BuildError(
            f"There is no single resolution '{text}'. Give its number (4.27), its id (2026-Vilnius-16) "
            "or its file name (4.27_2026-Vilnius-16)."
        )
    return matches


def run(root: Path, only: str | None = None, edition: str | None = None, build_date: dt.date | None = None) -> list[Path]:
    """Build and return the files produced. Raises BuildError."""
    report = validate.validate(root)
    if report.errors:
        raise BuildError("Fix these problems first (run validate.py for details):\n" + "\n".join(f"  - {e}" for e in report.errors))
    ctx = Context(root, build_date or dt.date.today())
    if not ctx.resolutions:
        raise BuildError("There are no resolutions yet. Import an OpenSlides export first.")
    selected = find(ctx, only) if only else ctx.resolutions

    if not only and edition is None and (ctx.dist / "resolutions").exists():
        shutil.rmtree(ctx.dist / "resolutions")  # no files of deleted resolutions left behind
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        bodies = dict(zip((r.id for r in selected), pool.map(lambda r: to_markdown(ctx, r), selected)))
        editions = [] if only else (["current", "archive"] if edition in (None, "all") else [edition])
        jobs = [pool.submit(build_edition, ctx, e, bodies) for e in editions]  # the long jobs first
        if edition is None:
            jobs += [pool.submit(build_single, ctx, r, bodies[r.id], ctx.dist / "resolutions") for r in selected]
        errors = []
        for job in jobs:
            try:
                job.result()
            except BuildError as exc:
                errors.append(str(exc))
    if errors:
        raise BuildError("\n\n".join(errors))

    produced = [ctx.dist / EDITIONS[e][0] for e in editions]
    if edition is None:
        produced += [ctx.dist / "resolutions" / f"{r.path.stem}.{ext}" for r in selected for ext in ("pdf", "docx")]
    if not only and edition is None:
        build_site(ctx)
        produced.append(root / "site" / "index.html")
    return produced


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Policy Book PDFs, the per-resolution files and the download page.")
    parser.add_argument("--only", metavar="RESOLUTION", help="build only this resolution's PDF and DOCX (number, id or file name)")
    parser.add_argument("--edition", choices=["current", "archive", "all"], help="build only this Policy Book edition")
    parser.add_argument("--date", type=dt.date.fromisoformat, help="build date shown in the documents (default: today)")
    parser.add_argument("--root", type=Path, default=pbook.ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        produced = run(args.root.resolve(), only=args.only, edition=args.edition, build_date=args.date)
    except BuildError as exc:
        print(f"Build failed: {exc}")
        return 1
    print(f"Built {len(produced)} file(s) in {args.root.name}/dist" + (" and site/" if any(p.name == "index.html" for p in produced) else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
