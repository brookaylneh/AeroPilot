# Search and Rescue Mission Control

An AI incident commander for wilderness search and rescue. Four quadcopters, each flown by an ArduPilot
autopilot over MAVLink, search a photorealistic Unreal Engine world for a missing person. SAM 3.1 detects,
Muse Glimmer verifies and Muse Wonder commands through typed tools, while the operator supervises from a
browser ground control station.

```
world/               the simulated world
  unreal/            Unreal Engine 5.8 project, build and streaming
  cosys/             Cosys-AirSim settings and Python bridge
scenario_creator/    browser studio: choose a person, situation and place; write the rescue briefing
mission_planner/     the system that searches the world
  gcs/               ground control station: ArduPilot fleet, mission service, UI
  agents/            model clients, Wonder tools, perception calibration, fal services
  measurements/      committed results
web/                 web server and page shell hosting both browser applications
```

Untracked state (data, raw evidence, vendored dependencies, builds, Unreal releases) lives in `.local/`.

## Start

Node 22 and Python 3.10+.

```sh
npm ci && npm run fonts
python3 -m venv .venv-cosys && .venv-cosys/bin/pip install -r requirements.txt
cp .env.example .env
cp deploy/runtime.env.example .local/runtime.env
npm run build && npm start
```

`/` opens the scenario creator and `/mission?case=<id>` mission control. The creator needs a running packaged
world ([world/unreal](world/unreal/README.md)); mission control needs the
[mission service](mission_planner/gcs/README.md).

## Checks

```sh
npm test && npm run typecheck && npm run format:check
.venv-cosys/bin/ruff check && .venv-cosys/bin/ruff format --check
.venv-cosys/bin/python -m unittest
```

Licensed Unreal content (MetaHuman, Fab, Cosys binaries) is not included; see
[assets](world/unreal/assets.md).
