# Cosys-AirSim

Cosys-AirSim 3.5.0 (`5.8-v3.5.0`) provides the simulated aircraft and cameras inside the Unreal world.

| File | Purpose |
| --- | --- |
| `settings.json` | AirSim settings for the four drones. Positions are **metres in NED**, not Unreal centimetres. |
| `world.json` | Search geometry and drone identities, without target locations. |
| `materials.csv` | Empty segmentation material table that Cosys requires at startup. |
| `client.py` | Python bridge: explicit world/vehicle frames, telemetry, RGB capture with pose metadata. |
| `coordinates.py` | Checks live Cosys poses against native Unreal instrumentation. |
| `configure-drones.py` | Regenerates `settings.json` from `world.json`. |
| `fetch-cosys.py` | Downloads and verifies the pinned plugin into the Unreal project. |

```sh
.venv-cosys/bin/python -m world.cosys.fetch-cosys
.venv-cosys/bin/python -m world.cosys.coordinates
```

`coordinates.py` needs a running packaged world with `SNR_WORLD_CONFIG`, `SNR_DRONE_DATA_DIR` and
`SNR_COSYS_PORT` matching that instance.

The fetcher requires the shared `.env`, verifies release archive hashes, and applies
`airsim-ue58.patch`. The patch avoids resetting an unchanged CustomDepth value during
startup, which would invalidate Landscape PSO precaching in UE 5.8.
