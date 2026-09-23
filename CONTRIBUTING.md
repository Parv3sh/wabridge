# Contributing to WaBridge

Thanks for helping make WhatsApp migration free for everyone.

## The single most valuable contribution right now

**Run a real migration and report what happened.** The engine is tested against synthetic
databases only. DESIGN.md §7 lists the on-device checks. Open an issue with:

* WhatsApp version on Android and iOS, Android version, iOS version
* the console output of every `wabridge` step (`-v` for tracebacks)
* `wabridge-work/convert/report.json`
* what WhatsApp on the iPhone showed afterwards (chats visible? media? names?)

Never post `msgstore.db`, `ChatStorage.sqlite`, your 64-digit key or any backup folder.

## Code

```bash
git clone https://github.com/parvesh-rm/wabridge && cd wabridge
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[all]"
pytest -q && ruff check src tests
```

Design rules that keep the project maintainable:

1. **Never hard-code schema assumptions that can be introspected.** Both databases drift between
   WhatsApp releases. Use `PRAGMA table_info` and `Z_PRIMARYKEY`, as the existing code does.
2. **Parsers and writers only meet in `model.py`.** Android code must not import iOS code or vice versa.
3. **Every stage must be re-runnable.** Users will retry after failures; stages read `state.json`
   and must not corrupt earlier outputs.
4. **Fixtures over real data.** Add columns/rows to `tests/fixtures.py` when you need a new case.
5. Mark anything you inferred rather than verified with `# unverified` and add it to DESIGN.md §7.

## Desktop app

`bash build-app.sh dev` runs the Tauri app against the repo venv with hot reload (needs Rust and
Node 20). Frontend type-check: `cd gui && npm run typecheck`. The engine protocol is the contract
between `src/wabridge/serve.py` and `gui/src/types.ts` — change both together and extend
`tests/test_serve.py`.

## Sharing schema knowledge safely

If you discover a new column meaning or enum value, contribute it to DESIGN.md §4/§5 with the
WhatsApp version you observed it in. Schema dumps (`.schema` output) contain no personal data
and are very welcome as `docs/schemas/<platform>-<version>.sql`.

## Licence

By contributing you agree your work is released under GPL-3.0-or-later.
