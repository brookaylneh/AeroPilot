# Engineering rules

- Fail loudly: no fallbacks, retries, or automatic repair branches.
- Network timeouts raise. An `except` only re-raises with added context.
- Required fields use direct indexing, never `d.get("key", default)`.
- Assert shapes, units, coordinate frames, ranges, schema fields, probability sums.
- Minimal code: one module per concept, no speculative abstractions, configuration
  layers, class hierarchies, unused parameters, or duplication.
- Comments explain only constraints that code cannot express.
- Report numbers from logged runs. Do not replace measurements with estimates.
- Match the style of the surrounding domain before editing; Python follows `world/cosys/*.py`.
- Verify APIs against primary documentation. A blocker stops only its affected
  item; continue independent work and report the evidence.
- Python loads `.env` through `mission_planner.environment.load_env`; assert `HF_TOKEN`,
  `MODEL_API_KEY`, `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` at startup. Never print, log, or commit secrets.
- Glimmer is the official `meta-models/Muse-Glimmer-30B-GGUF` build
  `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf` with its published mmproj, served by llama.cpp
  at the release the model card requires (b10353 or later). No other quantization or
  re-quantization of any model.
- No model is hosted on the mission host. The four drones share one SAM 3.1 H100 Server and one Glimmer H200 Server
  on Modal, a hackathon partner. Clients are provider-neutral: they read the
  endpoint from `.env` and send the same request contract to any provider.
- Keep the repository clean: no phase reports, historical notes, one-off probes or scratch
  scripts. Code lives in its domain folder; documentation is that folder's `README.md`.
  Committed results go to `mission_planner/measurements/`; everything untracked goes to `.local/`.

# Layout

- `world/unreal/`: Unreal project, manifests, scripts, container images. Centimetres,
  +X north, +Y east, +Z up; each drone has its own NED origin.
- `world/cosys/`: Cosys-AirSim settings (metres, NED) and the Python bridge.
- `scenario_creator/`: browser studio and rescue briefings.
- `mission_planner/gcs/`: ArduPilot SITL fleet, MAVLink mission service, `/mission` UI.
- `mission_planner/agents/`: model clients, Wonder tools, `perception/` calibration.
  Wonder uses Meta Model API; never route SAM to Meta API.
- `web/`: server, page shell and static files for both browser applications.
- `.local/`: `data/`, `evidence/`, `vendor/`, `staging/`, `dist/`, `unreal/` releases. Ignored.
- Tests live beside their code (`test_*.py`, `*.test.ts`).
- Evaluator truth is private and used only for explicitly requested evaluation.

# Serving

- Installed services and fixed ports are defined in `deploy/runtime.env.example` and
  `deploy/README.md`. Load `.local/runtime.env` for command-line tools.
- Use one development server: `npm run dev` binds `127.0.0.1:5174`, reached by SSH
  tunnel. Tests and acceptance use installed services or that documented dev port.
  Never choose a new port. Do not run a second dev server.

# Running

- `.venv-cosys` installs `requirements.txt`.
- Calibration (`P=.venv-cosys/bin/python -m mission_planner.agents.perception`):
  `$P.calibrate --list`; `$P.calibrate --condition <name> --scenario-id <uuid>` (stream
  released; existing condition directories fail; archive a failed condition explicitly);
  `$P.infer_calibration --condition all --output .local/evidence/calibration/<new-run>`
  (uses the always-warm Servers; existing outputs fail); `$P.analyze_calibration`
  (deterministic, from `.local/data/calibration/manifest.json`); `$P.plot_calibration`.
- Benchmarks: `$P.bench_models --case <case> --output .local/evidence/models/<new>.json`.
- Ground control: `mission_planner/gcs/README.md`. The mission service binds
  `127.0.0.1:8765`; the web server proxies `/api/mission`.
- Set `SAM_URL`, `GLIMMER_URL` and their full `*_AUTHORIZATION` header values in `.env`;
  changing provider changes only `.env`.
- Before Modal CLI commands: `set -a; . ./.env; set +a` and
  `: "${MODAL_TOKEN_ID:?}" "${MODAL_TOKEN_SECRET:?}" "${HF_TOKEN:?}"`.
- Never silently retry failed requests. New benchmarks use new output files; preserve
  accepted measurements.
- Glimmer analyst requests use the frozen template and fixed sampling settings; prove
  schema-valid reports and record reasoning-token counts. Belief uses only the measured
  escalation rates for that template, model build and provider.
- SAM requests contain exactly `jpeg`, `prompts`, and required `threshold` in [0, 1].
- Record sources, exact dependency versions, image digest, and runner usage.
- The two production Modal Servers stay warm at `min_containers=1`.
- Deploy: `.venv-cosys/bin/modal deploy -m mission_planner.agents.services.app`
  after loading `.env` as above.
- Any extra deployment a task creates for an experiment scales to zero afterwards,
  including after failures.

# Checks

`npm test`, `npm run typecheck`, `npm run format:check`, `.venv-cosys/bin/ruff check`,
`.venv-cosys/bin/ruff format --check`, `.venv-cosys/bin/python -m unittest`.
