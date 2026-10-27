# Contributing

Keep changes small and tied to observed behavior. Prefer straightforward functions, explicit names and existing dependencies. Remove duplicated logic when a shared operation has clear meaning; avoid generic frameworks for hypothetical extensions.

## Error handling

- Validate browser messages, saved documents and configuration at their boundary.
- Use assertions for internal invariants. Do not run engineering controllers with Python optimization (`-O`), which removes assertions.
- An unavailable service is an explicit state. Invalid data, missing required assets and violated invariants must produce an error, not substituted content.
- Never replay a mutating drone or authoring command after an ambiguous timeout.
- Keep landing and resource cleanup in failure paths, and preserve the original failure in test reports.

## Checks

```sh
npm ci
npm test
npm run typecheck
npm run format:check
.venv-cosys/bin/python -m unittest discover -s tests/python -v
ruff check simulation/phase2a tests/python
ruff format --check simulation/phase2a tests/python
```

Install Ruff 0.14.2 for the Python checks. `npm run format` formats first-party browser/server code and test tools. Browser integration tests need Chromium (`npx playwright install chromium`); set `PLAYWRIGHT_CHROMIUM_PATH` only when using an explicitly installed alternative.

`npm run test:browser` runs the procedural preview against an isolated server. Scripts named `check-drone-*`, `check-native-workflow` and `checkpoint-live-scene` operate an actual Unreal instance and can move aircraft or change scenarios. Run them only against a released editing session or an isolated candidate.

Native C++ changes require a new Unreal build, package and runtime check. A successful TypeScript build is not evidence of a working Unreal scene. Record image dimensions, timing scope, hardware and concurrent workloads when publishing performance results.

Do not commit downloaded assets, private target coordinates, saved scenarios, authentication URLs, credentials or raw deployment evidence. Use the public export audit in [public-source.md](docs/public-source.md).
