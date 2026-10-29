# LYMEC Policy Book Pipeline – Specification

## Goal
Turn accepted motions exported from OpenSlides (PDF = text, CSV = metadata) into a version-controlled
resolution database (Markdown files in this repo) and generate:
- one PDF + one DOCX per resolution
- LYMEC_Policy_Book.pdf: all resolutions in force (cover, TOC, indexes)
- LYMEC_Policy_Book_Archive.pdf: all resolutions incl. repealed/superseded/expired
Non-technical users only upload the PDF (+ CSV) via the GitHub web UI. Everything else is
automatic: no pull requests, no review step, no local tools.

## Stack
- Python 3.12, managed with uv (pyproject.toml, uv.lock). Libraries: pyyaml,
  python-frontmatter, pandas (CSV only), pymupdf (PDF text + font info),
  rapidfuzz (title similarity), pytest.
- Pandoc for Markdown->PDF/DOCX. LaTeX engine: xelatex.
- GitHub Actions; in CI use the Docker image pandoc/extra (pandoc + TeX Live).
- No database server, no web framework. Git is the database.

## Repository layout
```
resolutions/<id>.md       one file per resolution (source of truth)
congresses.yml            congress metadata
categories.yml            allowed categories (id, title, order)
imports/                  OpenSlides exports dropped here: <congress>.pdf (+ <congress>.csv)
templates/resolution.tex  single-resolution LaTeX template
templates/policybook.tex  master LaTeX template
templates/reference.docx  Word styles
assets/                   logo, fonts
scripts/                  import_openslides.py, validate.py, build.py
tests/                    pytest; tests/fixtures/ holds a real sample PDF + CSV
dist/                     build output (git-ignored)
site/                     static download page (index.html, generated)
.github/workflows/        publish.yml
USER_GUIDE.md             step-by-step guide for the Secretariat (no jargon)
```

## Resolution file format
YAML front matter + Markdown body (the resolution text only).
```yaml
---
id: 2026-VIE-07            # <year>-<congress code>-<motion number, 2 digits>
title: "..."
congress: 2026-VIE          # key in congresses.yml
adopted: 2026-04-12
category: digital           # key in categories.yml
status: in_force            # in_force | repealed | superseded | expired
replaces: []                # list of ids this resolution replaces
superseded_by: null         # id, set automatically from 'replaces'
amends: []                  # ids partially amended
submitters: ["..."]
openslides_number: "M07"
source: openslides          # openslides | migration
---
```
congresses.yml entry: {id, name, city, date_start, date_end}.

## scripts/import_openslides.py
Input: imports/<congress>.pdf (required, motion texts) and imports/<congress>.csv
(optional, metadata table). Congress id = file stem (e.g. 2026-BXL).
- If the congress is missing from congresses.yml, add a stub entry (id, year
  from the stem, date = import date) and flag it in the import report, so
  uploaders never have to edit YAML.
- Expected PDF export settings (document them in USER_GUIDE.md): Final version;
  Title, Number, Text, Submitters, State, Category; line numbering None;
  Enforce page breaks ON; Table of contents, header, page numbers, date OFF.
- PDF parsing with pymupdf: every motion starts on a new page. Detect the
  motion start by the title block (largest font on the page) + the number;
  read metadata lines (Submitters, State, Category) from the block under the
  title; the rest until the next motion start is the text.
- Convert text to Markdown using font info: bold -> **, larger font -> ###,
  bullet/number glyphs -> Markdown lists; join hyphenated line breaks;
  drop any leftover headers/footers/page numbers.
- If the CSV is present: match motions by Number, take metadata from the CSV
  (it is more reliable), and fail with a clear message if the PDF and CSV
  disagree on the set of numbers or titles.
- Keep only motions whose State means accepted (configurable list); skip
  amendments (they are already merged into the final version).
- Tolerate encoding/separator differences in the CSV (utf-8, utf-8-sig,
  cp1252; , ; tab) and match column names case-insensitively.
- Write resolutions/<id>.md; never overwrite an existing file without --force.
- For each new resolution, find likely replaced resolutions (rapidfuzz on
  title, same category, top 3 above a threshold) and write them into a
  Markdown import report (used as the GitHub Release notes).
- Track processed files in imports/processed.yml (filename + sha256) so a
  re-upload of the same file is a no-op and a changed file re-imports with --force.

## scripts/validate.py
- Required fields present, ids unique and matching the filename, valid enums,
  dates parse, category exists, congress exists.
- Every id in replaces/amends/superseded_by exists.
- Derive: if A.replaces contains B then B.status = superseded and
  B.superseded_by = A (write back with --fix, otherwise report).
- Exit code != 0 on any error; human-readable output.

## scripts/build.py
- Single resolution: pandoc -> PDF (templates/resolution.tex) and DOCX
  (templates/reference.docx). Header: logo, id, title, congress, date, status.
- Master: build ONE combined Markdown/LaTeX document (do not merge PDFs) with
  cover page, build date, TOC, resolutions grouped by category (order from
  categories.yml) then by id, an index by congress, clickable cross-references
  for replaces/superseded_by, page numbers, PDF bookmarks.
- Superseded/repealed only in the archive edition, marked with a status banner.
- Generate site/index.html: list of downloads, search box (plain JS),
  last build date.
- Command: uv run scripts/build.py [--only <id>] [--edition current|archive|all]

## Workflow (single file: .github/workflows/publish.yml)
- Trigger: push to main on paths imports/**, resolutions/**, congresses.yml,
  categories.yml, templates/**, assets/**; plus workflow_dispatch.
  concurrency: group 'publish', cancel-in-progress: false.
- Steps, all in ONE job (a commit pushed with GITHUB_TOKEN does not trigger
  another workflow run, so import and build cannot be split into two workflows):
  1. Find PDFs in imports/ not yet in imports/processed.yml; run the importer.
  2. Run validate.py --fix.
  3. On error: commit nothing, publish nothing, open or update a GitHub issue
     "Import failed: <file>" with the plain-language error and how to fix it,
     then fail the run. The live Policy Book stays unchanged.
  4. On success: commit the new resolution files + processed.yml to main as
     github-actions[bot] ("Import <congress>").
  5. Build all outputs, publish a GitHub Release tagged policybook-YYYY-MM-DD
     (suffix -2, -3 on the same day) with the import report as release notes,
     deploy site/ + PDFs to GitHub Pages.
- Manual corrections (e.g. setting superseded_by) are made by editing a file
  in the GitHub web editor; that push triggers the same workflow (build only).
- No branch protection that requires pull requests.

## Migration (later phase, separate script scripts/migrate_docx.py)
Split the existing Policy Book (.docx) into resolution files via pandoc
(docx -> gfm), one file per resolution heading; source: migration;
fields that can't be derived are left empty and listed in a report.

## Quality bar
- pytest for importer and validator using the tests/fixtures/ sample PDF + CSV.
- Reproducible: the same input gives the same output.
- Every user-facing error message says what to do next.
- USER_GUIDE.md: screenshot placeholders, written for non-technical users.
