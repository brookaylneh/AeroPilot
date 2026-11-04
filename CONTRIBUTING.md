# Contributing

Read `AGENTS.md` first: it states the engineering rules (fail loudly, no fallbacks, minimal code,
measured numbers only) and the repository layout. Code lives in its domain folder; each folder's
`README.md` is its documentation. Tests sit beside the code they cover.

Before opening a pull request, run the checks from `README.md`:

```sh
npm test && npm run typecheck && npm run format:check
.venv-cosys/bin/ruff check && .venv-cosys/bin/ruff format --check
.venv-cosys/bin/python -m unittest
```

Tests that need vendored assets under `.local/vendor` report themselves as skipped elsewhere.
Changes to flight behaviour, the Pixel Streaming protocol or the SAM device image need the
acceptance procedures documented in the affected folder, run against an installed host.
