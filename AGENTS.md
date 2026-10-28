# Engineering rules

- Fail loudly: no fallbacks, retries, or automatic repair branches.
- Network timeouts raise. An `except` only re-raises with added context.
- Required fields use direct indexing, never `d.get("key", default)`.
- Assert shapes, units, coordinate frames, ranges, schema fields, probability sums.
- Minimal code: one module per concept, no speculative abstractions, configuration
  layers, class hierarchies, unused parameters, or duplication.
- Comments explain only constraints that code cannot express.
- Report numbers from logged runs. Do not replace measurements with estimates.
- Match the style of the surrounding domain before editing; Python follows `world/simulator/*.py`.
- Verify APIs against primary documentation. A blocker stops only its affected
  item; continue independent work and report the evidence.
- Python loads `.env` through `mission_control.environment.load_env`; assert `FAL_KEY`,
  `HF_TOKEN`, and `MODEL_API_KEY` at startup. Never print, log, or commit secrets.
- No weight quantization. Glimmer uses BF16 and the official vLLM recipe.
- Keep the repository clean: no phase reports, historical notes, one-off probes or
  scratch scripts in tracked files. Results go to `measurements/`, raw runs to `evidence/`.

# Layout

- `app/`: web shell (`main.tsx` routes, `server.ts` host and proxies).
- `world/unreal/`: Unreal project and manifests. Centimetres, +X north, +Y east, +Z up;
  each drone has its own NED origin.
- `world/studio/`: browser scenario studio. `world/simulator/`: Cosys bridge.
  `world/scripts/`: build, package, run, stream. `world/deploy/`: container images.
- `mission_control/models.py`: thin typed SAM, Glimmer and Wonder clients.
  `mission_control/environment.py`: dotenv startup and credential assertions.
- `mission_control/perception/`: calibration capture, inference, analysis, geometry, benchmarks.
- `mission_control/agent/`: Wonder tool catalogue and tool-selection measurement.
- `mission_control/gcs/`: ArduPilot SITL fleet, MAVLink, mission service and `/mission` UI.
- `mission_control/services/`: fal GPU-H100 SAM 3.1 and GPU-H200 Glimmer deployments.
  Wonder uses Meta Model API; never route SAM to Meta API.
- Tests live beside their code (`test_*.py`, `*.test.ts`).
- `measurements/`: committed JSON summaries; never captured images.
- `data/`, `evidence/`, `vendor/`, `staging/`: ignored local state and dependencies.
- Evaluator truth is private and used only for explicitly requested evaluation.

# Running

- Install `world/simulator/requirements.txt` and `mission_control/requirements.txt` in
  `.venv-cosys`; fal deployment uses Python 3.12 `.venv-fal` with
  `mission_control/services/glimmer/requirements.txt`.
- Calibration (`P=.venv-cosys/bin/python -m mission_control.perception`):
  `$P.calibrate --list`; `$P.calibrate --condition <name> --scenario-id <uuid>` (stream
  released; existing condition directories fail; archive a failed condition explicitly);
  `$P.infer_calibration --condition all --output evidence/calibration/<new-run>` (owns fal
  warm-up and cleanup; existing outputs fail); `$P.analyze_calibration` (deterministic, from
  `data/calibration/manifest.json`); `$P.plot_calibration`.
- Benchmarks: `$P.bench_models --case <case> --output evidence/models/<new>.json`.
- Ground control: see `docs/mission-control/ground-control.md`. The mission service binds
  `127.0.0.1:8765`; the web server proxies `/api/mission`.
- Set `SAM_URL` and `GLIMMER_URL` in `.env` to the deployed fal endpoints.
- Before fal CLI commands: `set -a; . ./.env; set +a` and
  `: "${FAL_KEY:?}" "${HF_TOKEN:?}" "${MODEL_API_KEY:?}"`.
- Deploy from `mission_control/services/sam` with `../../../.venv-fal/bin/fal deploy snr-sam`,
  or from `mission_control/services/glimmer` with `../../../.venv-fal/bin/fal deploy snr-glimmer`.
- Keep compact samples, p50/p90, cold starts and failures in `measurements/`; raw responses
  in `evidence/`. Never silently retry failed requests. New benchmarks use new output files.
- Prove fast-mode prompt framing, one generated token, zero reasoning tokens,
  single-token options, normalized probabilities, and outside-option mass.
- SAM requests contain exactly `jpeg`, `prompts`, and required `threshold` in [0, 1].
- Record sources, exact dependency versions, image digest, and runner usage.
- Scale task-owned fal runners to zero after experiments, including failures.

# Checks

`npm test`, `npm run typecheck`, `npm run format:check`, `.venv-cosys/bin/ruff check`,
`.venv-cosys/bin/ruff format --check`, `.venv-cosys/bin/python -m unittest`.
