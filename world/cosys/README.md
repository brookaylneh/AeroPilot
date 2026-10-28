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
python3 world/cosys/fetch-cosys.py
.venv-cosys/bin/python -m world.cosys.coordinates
```

`coordinates.py` needs a running packaged world with `SNR_WORLD_CONFIG`, `SNR_DRONE_DATA_DIR` and
`SNR_COSYS_PORT` matching that instance.
