# Cosys-AirSim

Cosys-AirSim 3.5.0 (`5.8-v3.5.0`) provides the simulated aircraft and cameras inside the Unreal world.

| File                  | Purpose                                                                                       |
| --------------------- | --------------------------------------------------------------------------------------------- |
| `settings.json`       | AirSim settings for the four drones. Positions are **metres in NED**, not Unreal centimetres. |
| `world.json`          | Search geometry and drone identities, without target locations.                               |
| `materials.csv`       | Empty segmentation material table that Cosys requires at startup.                             |
| `client.py`           | Python bridge: explicit world/vehicle frames, telemetry, RGB capture with pose metadata.      |
| `coordinates.py`      | Checks live Cosys poses against native Unreal instrumentation.                                |
| `configure-drones.py` | Regenerates `settings.json` from `world.json`.                                                |
| `fetch-cosys.py`      | Downloads and verifies the pinned plugin into the Unreal project.                             |

```sh
.venv-cosys/bin/python -m world.cosys.fetch-cosys
.venv-cosys/bin/python -m world.cosys.coordinates
```

`coordinates.py` needs a running packaged world with `SNR_WORLD_CONFIG`, `SNR_DRONE_DATA_DIR` and
`SNR_COSYS_PORT` matching that instance.

The fetcher requires the shared `.env`, verifies release archive hashes, and applies
`airsim-ue58.patch`. The patch avoids resetting an unchanged CustomDepth value during
startup, which would invalidate Landscape PSO precaching in UE 5.8.

MessagePack connections are reused within their owning thread. Creating a new connection
for every image leaves an active read loop and its large decode buffer per capture.
A connection failure raises; the bridge does not replace failed connections automatically.

## Sensor rendering

The native `DroneFoundation` keeps RGB captures at 3840×2160 and registers each camera with
terrain and texture streaming. GI and reflections inherit the world lighting method; the native
component clears Cosys' explicit overrides to `None`. Shadow rendering shares the project settings.
Exposure is fixed at EV100 13, with zero compensation, bloom and motion blur. White balance is fixed
at 6500 K with zero tint. Native telemetry records each camera's resolution, exposure and white balance.

`python -m world.cosys.render_workload NEW_OUTPUT CAPTURE_HZ` is the local rendering-load worker.
The browser measurement tool owns its stdin protocol and cleanup. It requires four parked,
uncontrolled SimpleFlight vehicles and the exclusive controller lock. It records each 4K capture
with timing, pose and SHA-256, then lands the aircraft. It does not run SAM, Glimmer or fal runners.

### RGB readback

The pinned AirSim patch copies BGRA8 sensor targets into GPU staging textures and polls
completion at the render-frame boundary. It reads completed pixels without a synchronous
GPU wait on the render thread; a 15-second timeout is fatal. Float captures retain their
existing path. Capture timestamps are recorded when the copy is queued, before completion.
`SNR.Sensors.AsyncRgbReadback` compares RGB bytes against Unreal's synchronous readback at
13×7, 257×9 and 3840×2160 to check channel order and padded rows on the actual RHI.
The fetcher applies this change from `airsim-ue58.patch` to the pinned source archive.
