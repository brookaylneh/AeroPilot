# Engineering rules

- Fail loudly: no fallbacks, retries, or automatic repair branches.
- Network timeouts raise. An `except` only re-raises with added context.
- Required fields use direct indexing, never `d.get("key", default)`.
- Assert shapes, units, coordinate frames, ranges, schema fields, probability sums.
- Minimal code: one module per concept, no speculative abstractions, configuration
  layers, class hierarchies, unused parameters, or duplication.
- Comments explain only constraints that code cannot express.
- Report numbers from logged runs. Do not replace measurements with estimates.
- Read `simulation/phase2a/*.py` and match its style before editing Python.
- Verify APIs against primary documentation. A blocker stops only its affected
  item; continue independent work and report the evidence.
- Python loads `.env` through `mission.environment.load_env`; assert `FAL_KEY`, `HF_TOKEN`, and
  `MODEL_API_KEY` at startup. Never print, log, or commit secrets.
- No weight quantization. Glimmer uses BF16 and the official vLLM recipe.

# Layout

- `mission/models.py`: thin typed SAM, Glimmer, and Wonder clients.
- `mission/bench_models.py`: measured performance and capability benchmark.
- `mission/calibrate.py`: private calibration captures using existing simulator helpers.
- `mission/geometry.py`: recorded-camera projection and terrain intersection.
- `mission/infer_calibration.py`, `mission/analyze_calibration.py`: saved-frame inference and offline analysis.
- `mission/glimmer_calibration.py`: crop-presence labels and separate Glimmer cohort statistics.
- `mission/environment.py`: shared dotenv startup and credential assertions.
- `services/glimmer/`: fal GPU-H200 deployment of the official vLLM container.
- `services/sam/`: fal GPU-H100 deployment of the pinned SAM 3.1 checkpoint.
- Wonder uses Meta Model API. SAM uses fal; never route SAM to Meta API.
- `measurements/`: committed JSON samples and metadata; never captured images.
- `simulation/phase2a/`: existing simulator and evaluator helpers.
- `src/`, `server/`, `shared/`, `public/`: existing browser/server application.
- `unreal/`, `docs/`: engine integration and project documentation.
- `data/`, `evidence/`, `vendor/`, `staging/`: ignored local state and dependencies.
- Unreal uses centimetres, +X north, +Y east, +Z up; each drone has its own NED
  origin. Reuse existing transforms, flight controls, and capture helpers.
- Evaluator truth is private and used only for explicitly requested evaluation.

# Running

- Install `mission/requirements.txt` in `.venv-cosys`; deployment requirements
  belong in Python 3.12 `.venv-fal` using `services/glimmer/requirements.txt`.
- List calibration conditions: `.venv-cosys/bin/python -m mission.calibrate --list`.
- Capture one condition: `.venv-cosys/bin/python -m mission.calibrate --condition adult-kelvin
  --scenario-id <saved-flat-meadow-uuid>`; `--all` runs the plain condition list.
  The stream must be released. Existing condition directories fail; explicitly archive
  a failed condition before rerunning it by name. Do not overwrite accepted captures.
- Infer saved captures: `.venv-cosys/bin/python -m mission.infer_calibration --condition all
  --output evidence/calibration/<new-run-name>`. This owns the fal warming/cleanup window.
  Explicit condition names may replace `all`; existing inference outputs fail, with no resume.
- Correct old schema-1 crop labels without model requests:
  `.venv-cosys/bin/python -m mission.glimmer_calibration --condition <names>
  --output evidence/calibration/<new-label-audit>`. Original predictions are archived unchanged.
- Regenerate calibration deterministically, without simulator or network:
  `.venv-cosys/bin/python -m mission.analyze_calibration`.
  The input manifest at `data/calibration/manifest.json` records the complete plan,
  provenance and any explicit exclusions; missing, unexcluded inputs fail.
  See `docs/perception-calibration.md` for label definitions and limitations.
- Plot the resulting report: `.venv-cosys/bin/python -m mission.plot_calibration`.
- Run an explicit benchmark case with a new output path, for example:
  `.venv-cosys/bin/python -m mission.bench_models --case sam_frame_half_1
  --output evidence/models/sam-new.json`; `--help` lists supported cases.
- Set `SAM_URL` and `GLIMMER_URL` in `.env` to the deployed fal endpoints.
- Before fal CLI commands: `set -a; . ./.env; set +a`.
  Assert credentials: `: "${FAL_KEY:?}" "${HF_TOKEN:?}" "${MODEL_API_KEY:?}"`.
- Deploy from `services/sam` with `../../.venv-fal/bin/fal deploy snr-sam`, or
  from `services/glimmer` with `../../.venv-fal/bin/fal deploy snr-glimmer`.
- Keep compact samples, p50/p90, cold starts and failures in `measurements/models.json`;
  raw responses belong in ignored `evidence/`. Never silently retry failed requests.
- Preserve accepted measurements; new benchmarks use explicit new output files.
- Prove fast-mode prompt framing, one generated token, zero reasoning tokens,
  single-token options, normalized probabilities, and outside-option mass.
- SAM requests contain exactly `jpeg`, `prompts`, and required `threshold` in [0, 1].
- Run `.venv-cosys/bin/ruff check` and `.venv-cosys/bin/ruff format --check`.
- Record sources, exact dependency versions, image digest, and runner usage.
- Scale task-owned fal runners to zero after experiments, including failures.
