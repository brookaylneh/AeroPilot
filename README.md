# Search and Rescue Mission Control

An AI incident commander for wilderness search and rescue. Four quadcopters, each flown by an ArduPilot
autopilot over MAVLink, search a photorealistic Unreal Engine world for a missing person. SAM 3.1 detects,
Muse Glimmer verifies and Muse Wonder commands through typed tools, while the operator supervises from a
browser ground control station.

## Structure

```
app/                 web shell: routes and server for both browser applications
world/               the simulated world
  unreal/            Unreal Engine 5.8 project, asset manifests, Cosys-AirSim settings
  studio/            browser scenario studio: person, situation, placement, rescue briefing
  simulator/         Python Cosys-AirSim bridge
  scripts/           build, package, run and stream the world
  deploy/            container images
mission_control/     the system that searches the world
  models.py          SAM 3.1, Muse Glimmer and Muse Wonder clients
  perception/        detection calibration at 30 m
  agent/             Wonder tool catalogue
  gcs/               ground control station: ArduPilot fleet, mission service, UI
  services/          fal model deployments
measurements/        committed measurement summaries
docs/                setup, architecture and per-domain documentation
```

## Start

```sh
npm ci && npm run assets && npm run build && npm start
```

Open `http://127.0.0.1:3001` for the scenario studio and `/mission?case=<id>` for mission control.
The studio needs a running packaged Unreal world; see [setup](docs/setup.md).

## Documentation

- [Setup](docs/setup.md) and [architecture](docs/architecture.md)
- World: [scenario studio](docs/world/studio.md), [navigation](docs/world/navigation.md),
  [streaming](docs/world/streaming.md), [assets](docs/world/assets.md)
- Mission control: [ground control](docs/mission-control/ground-control.md), [agent tools](docs/mission-control/tools.md),
  [perception calibration](docs/mission-control/perception-calibration.md)

Licensed Unreal content (MetaHuman, Fab, Cosys plugin binaries) is not included; see [assets](docs/world/assets.md).
