# Mission planner

The system that searches the world.

- [`search/`](search/README.md): quoted subject profiles, sourced priors, calibrated detection,
  terrain-following fleet planning and camera-derived belief.

- [`gcs/`](gcs/README.md): ground control station. Four ArduPilot SITL autopilots over MAVLink, the mission
  service and the `/mission` browser UI.
- [`agents/`](agents/README.md): the AI. Model clients (`models.py`), Wonder's typed tool catalogue,
  [perception calibration](agents/perception/README.md) for SAM 3.1 and Muse Glimmer, and the fal deployments
  in `agents/services/`.
- `measurements/`: committed results (model benchmark, autopilot fleet, tool selection, calibration).
- `environment.py`: loads `.env` and asserts `FAL_KEY`, `HF_TOKEN` and `MODEL_API_KEY`.

Wonder runs on the Meta Model API. SAM 3.1 (fal H100) and Glimmer (fal H200, BF16 vLLM) run on fal.
