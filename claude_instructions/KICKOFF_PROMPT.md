Read SPEC.md in the repo root. It is the full specification for this project.

1. First, create CLAUDE.md with the key conventions from SPEC.md (stack, folder layout, data schema, commands), so future sessions keep them.
2. Then propose a short implementation plan per phase (2–5 from SPEC.md) and wait for my OK.
3. Implement ONE phase at a time. After each phase: run the tests, show me what changed, list open questions, and stop until I say "next".

Rules: Python via uv only, keep it simple (no frameworks beyond what SPEC.md lists), English code comments, no secrets in the repo. If the sample PDF/CSV in tests/fixtures/ contradict SPEC.md, trust the files and tell me.
