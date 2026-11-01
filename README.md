# Search and Rescue Mission Control

An AI flight planner and incident commander for a simulated backcountry world. Muse Wonder writes one
versioned flight plan for up to four quadcopters, each flown by an ArduPilot autopilot over MAVLink.
The operator reviews the checked plan and approves launch from a browser ground control station.
SAM 3.1 finds visual candidates and Muse Glimmer reviews their images; neither establishes identity.

General missions and Studio briefings share the same tools and planning instructions. Wonder chooses
one to four aircraft, the flight pattern and a visible target such as a person, animal or object.
Selected fly, follow, survey and circle steps can observe automatically inside a chosen polygon.
Hover and elevated views use explicit camera inspection. The complete trip is checked against each
aircraft's measured home, the exact flight fence, terrain, obstacles, separation and battery limits.
See [mission planning](mission_planner/gcs/README.md) and the
[release validation record](mission_planner/measurements/release.json) for behavior, checks and limits.

```
world/               the simulated world
  unreal/            Unreal Engine 5.8 project, build and streaming
  cosys/             Cosys-AirSim settings and Python bridge
scenario_creator/    browser studio: choose a person, situation and place; write the rescue briefing
mission_planner/     flight planning, control and visual observation
  gcs/               ground control station: ArduPilot fleet, mission service, UI
  agents/            model clients, Wonder tools, perception calibration
  measurements/      committed results
web/                 web server and page shell hosting both browser applications
```

Untracked state (data, raw evidence, vendored dependencies, builds, Unreal releases) lives in `.local/`.

## Start

Use the pinned Node 22 and Python toolchains in [deployment](deploy/README.md).
The packaged Unreal world, coupled mission service and remote model services must be configured before
flight. Python entry points load `.env` through `mission_planner.environment.load_env` and require
`HF_TOKEN`, `MODEL_API_KEY`, `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`; keep credentials out of logs.

```sh
cp .env.example .env
mkdir -p .local
cp deploy/runtime.env.example .local/runtime.env
npm ci
npm run fonts
python3 -m venv .venv-cosys
.venv-cosys/bin/pip install -r requirements.txt
```

`/home` offers the general planner and scenario creator. `/missionplanner` opens or rejoins a mission;
Studio launches saved rescue briefings into `/missionplanner?case=<id>`. `/mission` is an alias.
Opening a page starts no flight or model request. Follow the [installed-service instructions](deploy/README.md)
for startup and coupled shutdown. Development uses one `npm run dev` server on `127.0.0.1:5174`, reached
through an SSH tunnel; do not start a second server.

Wonder uses Meta Model API. The fleet shares one always-warm SAM 3.1 H100 Server and one Glimmer H200
Server on Modal. The mission host runs no models. Endpoint URLs and complete Authorization header values
come from `.env`; see [model services](mission_planner/agents/services/README.md).

## Checks

```sh
npm test && npm run typecheck && npm run format:check
.venv-cosys/bin/ruff check && .venv-cosys/bin/ruff format --check
.venv-cosys/bin/python -m unittest
```

Licensed Unreal content and Cosys binaries are not included; see
[asset dependencies and their licenses](world/unreal/assets.md). A project source license has not been
selected; dependency licenses do not grant a license for the whole repository.
