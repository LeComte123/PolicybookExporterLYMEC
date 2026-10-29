# LYMEC Policy Book Pipeline

Accepted OpenSlides motions → one Markdown file per resolution (git is the database) → per-resolution
PDF/DOCX + `LYMEC_Policy_Book.pdf` (in force) + `LYMEC_Policy_Book_Archive.pdf` (everything).
Full spec: `claude_instructions/SPEC.md`. Where this file deviates from SPEC.md, this file wins
(the deviations come from the real OpenSlides export, see "Decisions").

## Working mode
- Implement ONE phase at a time (phases 2–6 in SPEC/plan). After each phase: run tests, show what
  changed, list open questions, then stop until the user says "next".
- Python via uv only. Keep it simple: no frameworks beyond the stack below. English code comments.
  No secrets in the repo. Every user-facing error message says what to do next.
- Same input → same output (sorted iteration, no timestamps in resolution files).
- Never change wording from the sources (motion texts, titles, organisation names, chapter titles),
  not even typos. Only structure is converted (HTML → Markdown, agenda prefix "5. " removed from titles,
  Submitted/Co-signed paragraphs moved into the header).

## Stack
- Python 3.12 (`.python-version`), uv (`pyproject.toml`, `uv.lock`). Libraries: pyyaml,
  python-frontmatter, pandas (CSV only), pymupdf, rapidfuzz; dev: pytest.
- pandoc (local: `brew install pandoc`; not a Python package) for HTML→Markdown at import and
  Markdown→PDF/DOCX at build. LaTeX engine: xelatex (local: MacTeX). Font: Fira Sans in `assets/fonts/`.
- CI: GitHub Actions, one workflow `.github/workflows/publish.yml`, Docker image `pandoc/extra`.
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
resolutions/<id>.md       one file per resolution (source of truth)
congresses.yml            congress metadata {id, name, city, date_start, date_end}
categories.yml            the 9 Policy Book chapters {id, chapter, title, order, last_number}
imports/                  upload inbox: <congress>.csv (+ <congress>.pdf)
imported/                every imported upload, kept for the record (never deleted; same name again -> _2, _3)
templates/                resolution.tex, policybook.tex, reference.docx
assets/                   lymec_logo.png, fonts/
scripts/                  pbook.py (shared helpers), validate.py, import_openslides.py,
                          openslides_pdf.py (PDF reader for the importer), build.py
tests/                    pytest; tests/fixtures/2026-VNO.csv|pdf = real OpenSlides export (Vilnius, autumn 2026;
                          motions 08/12/18 have colliding amendments), colliding.csv = small conflict fixture
dist/                     build output (git-ignored)
site/                     generated download page
input/                    local scratch inputs (git-ignored)
```

## Resolution file
```yaml
---
id: 2026-VNO-18            # <congress id>-<OpenSlides motion number, 2 digits>; == filename stem
number: "6.21"             # Policy Book number <chapter>.<NN>; unique; chapter == category's chapter
title: "..."               # agenda prefix like "5. " stripped
congress: 2026-VNO         # key in congresses.yml (<year>-<IATA-style city code>)
adopted: 2026-10-25        # default: congress date_end
category: climate-energy   # key in categories.yml
status: in_force           # in_force | repealed | superseded | expired
replaces: []               # ids
superseded_by: null        # id; derived from other resolutions' `replaces` (validate.py --fix)
amends: []                 # ids
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
- Ids follow SPEC (`2026-VNO-18`), plus a `number` field (Policy Book numbering `6.21`, never reused).
  New numbers = max(`last_number` in categories.yml, highest existing in that chapter) + 1, assigned in
  motion-number order. Motion texts may say "This motion replaces: 6.03 …" → resolved via `number`.
- Categories = the 9 Policy Book chapters. CSV categories are mapped by chapter number (they contain
  typos like "Chatper 3").
- Uploads are never deleted: after an import they move from imports/ to imported/. A file whose
  content is already in imported/ is not imported again (replaces SPEC's imports/processed.yml).
- Submitters: organisations only, structured as `submitted_by` + `co_signers` (parsed from the
  "Submitted by:/Co-submitted by:/Co-signed by:" paragraphs; fallback: org in parentheses of the CSV
  Submitters column).

## Commands
```
uv sync                                          # install deps
uv run pytest                                    # tests
uv run scripts/validate.py [--fix]               # check resolutions/, congresses.yml, categories.yml
uv run scripts/import_openslides.py [files] [--force]
uv run scripts/build.py [--only <id>] [--edition current|archive|all]
```
