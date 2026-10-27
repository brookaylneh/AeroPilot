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
- Load `.env` through `mission.environment`; assert `FAL_KEY`, `HF_TOKEN`, and
  `MODEL_API_KEY` at startup. Never print, log, or commit secrets.
- No weight quantization. Glimmer uses BF16 and the official vLLM recipe.

# Layout

- `mission/models.py`: thin typed SAM, Glimmer, and Wonder clients.
- `mission/bench_models.py`: measured performance and capability benchmark.
- `mission/smoke.py`: private simulator evaluation captures using existing helpers.
- `mission/environment.py`: shared dotenv startup for clients and deployment.
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
- Phase 1 does not alter simulation/server code, UI, scenario assets, or planning.

# Running

- Install `mission/requirements.txt` in `.venv-cosys`; deployment requirements
  belong in Python 3.12 `.venv-fal` using `services/glimmer/requirements.txt`.
- From the repository root: `.venv-cosys/bin/python -m mission.smoke`, then
  `.venv-cosys/bin/python -m mission.bench_models --case resolution`.
- Run each remaining benchmark case with `--case`; use `--help` for the list.
- Set `SAM_URL` and `GLIMMER_URL` in `.env` to the deployed fal URL before Glimmer cases.
- For deployment, run `.venv-fal/bin/python -m mission.environment
  /srv/snr/.venv-fal/bin/fal --help`; the wrapper loads the same dotenv file.
- Keep raw samples, p50/p90, at least 20 warm samples, cold starts, and failures
  in `measurements/models.json`. Never silently retry failed requests.
- Prove fast-mode prompt framing, one generated token, zero reasoning tokens,
  single-token options, normalized probabilities, and outside-option mass.
- Record sources, exact dependency versions, image digest, and runner usage.
- Scale phase-owned fal runners to zero after benchmarks, including failures.
