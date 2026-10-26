# Studio (ai.cutin.dev/studio)

Static source of the Studio page, copied as-is from `Qwen-image/site/studio/` (2026-10-26).
Plain HTML/CSS/JS with no build step.

- `shell.html` — page shell; `/*@DS_TOKENS@*/` is replaced with `ds/tokens.css` by the page generator (`scripts/gen_pages.py`, not in this repo).
- `core.js` — state, API client, composer; `feature-*.js` — per-mode modules (edit, compose, cutout, text, prod).
- `ds/` — vendored Cutin AI Design System (`VERSION` records the artifact version).

`core.js` holds the Clerk publishable key (`pk_live_…`), which is public by design.
