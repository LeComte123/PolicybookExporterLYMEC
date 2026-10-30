# LYMEC Policy Book Pipeline

Accepted OpenSlides motions → one Markdown file per resolution (git is the database) → per-resolution
PDF/DOCX + `LYMEC_Policy_Book.pdf` (in force) + `LYMEC_Policy_Book_Archive.pdf` (everything).
Full spec: `build_instructions/SPEC.md`. Where this file deviates from SPEC.md, this file wins
(the deviations come from the real OpenSlides export, see "Decisions").

## Working mode
- Implement ONE phase at a time (phases 2–6 in SPEC/plan). After each phase: run tests, show what
  changed, list open questions, then stop until the user says "next".
- Python via uv only. Keep it simple: no frameworks beyond the stack below. English code comments.
  No secrets in the repo. Every user-facing error message says what to do next.
- README.md is the guide for non-technical Bureau/Secretariat users (plain language, no jargon,
  screenshot placeholders); it replaces SPEC's USER_GUIDE.md. Keep it up to date with every
  user-visible change. Developer notes live in this file.
- Same input → same output (sorted iteration, no timestamps in resolution files).
- Never change wording from the sources (motion texts, titles, organisation names, chapter titles),
  not even typos. Only structure is converted (HTML → Markdown, agenda prefix "5. " removed from titles,
  Submitted/Co-signed paragraphs moved into the header).

## Stack
- Python 3.12 (`.python-version`), uv (`pyproject.toml`, `uv.lock`). Libraries: pyyaml,
  python-frontmatter, pandas (OpenSlides CSV only), pymupdf, rapidfuzz; dev: pytest.
- pandoc (local: `brew install pandoc`; not a Python package) for HTML→Markdown at import and
  Markdown→PDF/DOCX at build. LaTeX engine: xelatex (local: MacTeX). Font: Arial everywhere (PDF, DOCX,
  web page); a system font, not in the repo (licence). macOS/Windows have it; Linux: ttf-mscorefonts-installer.
- CI: GitHub Actions, one workflow `.github/workflows/publish.yml`, one job in the Docker image
  `pandoc/extra:3.11.0.0-debian` (pandoc 3.11 + TeX Live with KOMA-Script; the job installs git, gh and Arial).
  The full test suite passes in that image. The job runs as root on a checkout owned by the runner's
  user, so it marks $GITHUB_WORKSPACE as git safe.directory. astral-sh/setup-uv has no major-version
  tag: pin a release (v10.3.0).
  The repo default GITHUB_TOKEN is read-only, so publish.yml declares exactly these top-level
  permissions and nothing else:
  ```yaml
  permissions:
    contents: write   # commit resolutions, move processed inputs to imported/, create releases
    issues: write     # open "Import failed" issues
    pages: write      # deploy GitHub Pages
    id-token: write   # required by actions/deploy-pages
  ```

## Layout
```
resolutions/<number>_<id>.md  one file per resolution (source of truth), e.g. 4.27_2026-Vilnius-16.md
congresses.yml            congress metadata {id, name, city, date_start, date_end}
resolution_status_list.csv  the Bureau's status list: every Policy Book number, in force / archived / not used
categories.yml            the 9 Policy Book chapters {id, chapter, title, order, last_number}
imports/                  upload inbox: <congress>.csv (+ <congress>.pdf), only files directly in it are imported
imports/migration/        material for the one-time migration (phase 6): the Vienna Policy Book (PDF; .docx follows)
imported/                every imported upload, kept for the record (never deleted; same name again -> _2, _3)
templates/                resolution.tex, policybook.tex (pandoc templates), lymec.latex (shared look),
                          reference.docx (Word styles, made by scripts/make_reference_docx.py)
assets/                   lymec_logo.png
scripts/                  pbook.py (shared helpers), validate.py, import_openslides.py,
                          openslides_pdf.py (PDF reader for the importer), build.py,
                          status_list.py (reads/extends resolution_status_list.csv),
                          open_issue.sh (workflow: open or comment a GitHub issue)
.github/workflows/        publish.yml
docs/images/              screenshots for README.md
tests/                    pytest (conftest.py: make_status_list fixture); tests/fixtures/2026-Vilnius.csv|pdf = real OpenSlides export (autumn 2026;
                          motions 08/12/18 have colliding amendments), colliding.csv = small conflict fixture
dist/                     build output (git-ignored)
site/                     generated download page + all files, ready for GitHub Pages (git-ignored)
```

## Resolution file
```yaml
---
id: 2026-Vilnius-18        # <congress id>-<OpenSlides motion number, 2 digits>; stable, never changes
number: "6.21"             # Policy Book number <chapter>.<NN>; unique among in_force; chapter == category's chapter
title: "..."               # agenda prefix like "5. " stripped
congress: 2026-Vilnius     # key in congresses.yml (<year>-<City in English>[-<Part>])
adopted: 2026-10-25        # default: congress date_end; validate --fix keeps it within the congress dates
category: climate-energy   # key in categories.yml
status: in_force           # in_force | archived | superseded | expired
replaces: []               # ids (people may write numbers: validate --fix turns '6.03' into the id)
superseded_by: null        # id; derived from other resolutions' `replaces` (validate.py --fix)
amends: []                 # ids (numbers allowed as for replaces)
submitted_by: ["Julis"]    # organisations only, never person names
co_signers: ["JOVD"]       # organisations
openslides_number: "18"
source: openslides         # openslides | migration
---
Resolution text in Markdown (without the Submitted by / Co-signed by lines; they live in the header).
```

## Decisions (deviations from SPEC.md)
- The OpenSlides CSV contains the full motion text as HTML → **CSV is the primary text source**
  (HTML→Markdown via pandoc). The PDF is cross-checked against the CSV (numbers + titles); the
  pymupdf parser is only a fallback for PDF-only uploads.
- Motions whose CSV text contains `os-colliding-change` (unresolved colliding amendments) are **not
  imported**; they are reported as import errors with the motion number and the `data-identifier`s and
  the fix: "Several amendments change the same line. Open the motion in OpenSlides, create a modified
  final version that resolves them, and export again." The other motions in the file are imported.
- Congress ids use the city name, not SPEC's IATA code: `2026-Vilnius` (user's decision). The importer
  normalises file names (case, accents, spaces: "2027 kraków.csv" → 2027-Krakow) and matches existing
  congress ids case-insensitively.
- Ids `2026-Vilnius-18` (congress + OpenSlides motion number) are stable and used in all references;
  the Policy Book number is in `number` and in the file name `<number>_<id>.md` (user's decision:
  the Bureau finds files by number; renumbering only renames files, validate --fix does that when
  `number` is edited). Numbers are unique among in_force resolutions only: after a renumbering an
  archived resolution keeps its old number. New numbers = max(`last_number` in categories.yml, highest
  existing in that chapter) + 1, assigned in motion-number order.
- Archiving is confirmed by the Bureau's status list (user's decision), resolution_status_list.csv in
  the repo root (CSV, not xlsx: GitHub can show and edit it online). Columns found by header; the
  edited ones first: Number, Status, Archived at, Replaced by, Title, then migration data. Read with
  delimiter sniffing and cp1252 fallback; validate --fix writes the standard form (comma, UTF-8 with
  BOM for Excel, sorted by number, 6.1 → 6.10), which repairs Excel round trips.
  - Statement paragraphs in a motion text, anywhere in it ("This motion/resolution replaces|archives|
    repeals …", "Replaces: …", "Archives: …", "Archiving Resolution(s) …"; numbers in other sentences
    are ignored), only take effect if the list marks the number 'archived' (title must match if the row
    has one). Confirmed "replaces" → `replaces` (old one superseded); unconfirmed → "Please check the
    status list" issue (dist/status-check.md), nothing changes. Rows archived at the congress that no
    motion mentions → note in the import report.
  - validate --fix applies every 'archived' row: the in-force resolution with that number (and title)
    becomes archived, or superseded if 'Replaced by' names an in-force number. A row is satisfied when a
    matching resolution is already archived/superseded (renumbering). Rows for in-force resolutions
    missing from the list are added.
  - The migration (phase 6) uses the same rules: archived rows = archived resolutions; statements in
    the Vienna texts ("This resolution archives 2.21 …") are checked against the list.
  - The list was generated from the Vienna PDF (225 in force, 61 gap numbers, congress per resolution
    where the "Adopted at" line could be read). The congresses read from the book are in congresses.yml
    (comments mark uncertain data; Spring and Autumn 2025 are still missing; Autumn 2024 = Warsaw, from a
    "prior to Warsaw, Autumn 2024" note in the book).
- Status `archived` replaces SPEC's `repealed` (LYMEC's word, shown as "Archived").
- A congress missing from congresses.yml gets a placeholder entry (city from the id, upload day as
  dates) and a "New congress" issue; when the real dates are entered, validate --fix moves the
  adoption dates of its resolutions into the congress dates (last day).
- Categories = the 9 Policy Book chapters. CSV categories are mapped by chapter number (they contain
  typos like "Chatper 3").
- Uploads are never deleted: after an import they move from imports/ to imported/. A file whose
  content is already in imported/ is not imported again (replaces SPEC's imports/processed.yml).
- Submitters: organisations only, structured as `submitted_by` + `co_signers` (parsed from the
  "Submitted by:/Co-submitted by:/Co-signed by:" paragraphs; fallback: org in parentheses of the CSV
  Submitters column).
- publish.yml order: import → validate --fix → build → commit → release → Pages. Building before the
  commit means a failed import, check or build commits and publishes nothing.
- Issues: the importer writes their texts, first line `# <title>`: dist/import-failed.md ("Import failed:
  <files>", run fails), dist/import-errors.md ("Import incomplete: <files>", skipped motions; the rest is
  published), dist/status-check.md ("Please check the status list: <congress>") and dist/new-congress.md
  ("New congress: <id>"). Other failures → "Publishing failed": for the steps
  check/build it shows the problems from their log; any other failed step (found via toJSON(steps),
  every step has an id) is reported as technical, to forward to the maintainer. Without any resolution file the workflow stops after the check (nothing to publish). scripts/open_issue.sh comments on an
  open issue with the same title instead of opening a second one.
- A release (tag policybook-YYYY-MM-DD[-n]; assets: both books + a zip of the per-resolution files) is only
  created when resolutions/, congresses.yml, categories.yml, templates/ or assets/ changed since the last
  release tag. Notes = import report + list of changed resolution files. Pages are deployed on every run.

## Commands
```
uv sync                                          # install deps
uv run pytest                                    # tests
uv run scripts/validate.py [--fix]               # check resolutions/, congresses.yml, categories.yml
uv run scripts/import_openslides.py [files] [--force]
uv run scripts/build.py [--only <number|id|file>] [--edition current|archive|all] [--date YYYY-MM-DD]
                        # no options: everything; --only: one resolution; --edition: only that book
uv run scripts/make_reference_docx.py            # regenerate templates/reference.docx
```

## Build notes
- Resolution bodies are GitHub Markdown; build.py converts them to pandoc Markdown (-smart) and
  combines them with generated parts (raw LaTeX blocks for labels, banners, index).
- PDFs: xelatex, KOMA-Script (scrartcl/scrreprt), Arial with Ligatures=TeXOff so text prints
  exactly as written. Templates reuse pandoc's partials ($common.latex()$ etc.), so CI must use a
  pandoc version with the same partials (local: 3.13, CI: 3.11; both work).
- DOCX: Arial, LYMEC violet headings.
- PDF creation date = build date (SOURCE_DATE_EPOCH); PDFs are not byte-identical between builds
  (xdvipdfmx uses random font subset tags), DOCX files are.

## Phase 6 plan (migration of the Vienna Policy Book)
- Inputs: imports/migration/<Vienna book>.docx (pandoc docx → gfm, split at the "X.YY Title" headings)
  + resolution_status_list.csv (statuses, archived resolutions) + congresses.yml + the Vienna PDF for
  cross-checks. Run once, locally; the result is reviewed, then committed (the push publishes it).
- Ids of migrated resolutions: <congress>-<number at migration>, e.g. 2018-Berlin-6.08 (no OpenSlides
  number); `source: migration`.
- Afterwards (agreed): move imports/migration/* to imported/migration/ and delete the one-time
  migration script (it stays in the git history).
