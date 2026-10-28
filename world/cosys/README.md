# Cosys-AirSim

Cosys-AirSim 3.5.0 (`5.8-v3.5.0`) provides the simulated aircraft and cameras inside the Unreal world.

| File | Purpose |
| --- | --- |
| `settings.json` | AirSim settings for the four drones. Positions are **metres in NED**, not Unreal centimetres. |
| `world.json` | Search geometry and drone identities, without target locations. |
| `materials.csv` | Empty segmentation material table that Cosys requires at startup. |
| `client.py` | Python bridge: explicit world/vehicle frames, telemetry, RGB capture with pose metadata. |
| `coordinates.py` | Checks live Cosys poses against native Unreal instrumentation. |
| `configure-drones.py` | Generates ArduCopter settings from `world.json` and mission geodesy with explicit RPC port and SITL instance base. |
| `fetch-cosys.py` | Downloads and verifies the pinned plugin into the Unreal project. |

```sh
.venv-cosys/bin/python -m world.cosys.fetch-cosys
.venv-cosys/bin/python -m world.cosys.coordinates
```

`coordinates.py` needs a running packaged world with `SNR_WORLD_CONFIG`, `SNR_DRONE_DATA_DIR` and
`SNR_COSYS_PORT` matching that instance.

The fetcher requires the shared `.env`, verifies release archive hashes, and applies
`airsim-ue58.patch` and `airsim-geodesy.patch`. The first avoids resetting an unchanged
CustomDepth value during startup, which would invalidate Landscape PSO precaching in
UE 5.8. The geodesy patch uses the global NED transform and WGS84 conversion for each
spawn home. It is prepared and dry-run checked; the recovery package does not contain it.

## Coupled settings

Generate the tracked deployment settings with explicit port and instance choices:

```sh
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port 41451 --instance-base 0 --output world/cosys/settings.json
```

For an isolated slot, load its ignored environment and write its own settings file:

```sh
set -a
. .local/slot.env
set +a
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port "$SNR_COSYS_PORT" --instance-base "$SNR_SITL_INSTANCE_BASE" \
  --output "$SNR_COSYS_SETTINGS"
```

The four `ArduCopter` vehicles bind their control inputs to `127.0.0.1`; sensor packets
also target loopback. For SITL instance `i`, `UdpPort` is `9003 + 10*i` and
`ControlPortLocal` is `9002 + 10*i`. The generator checks the NED spawn coordinates,
headings and altitude against `geodesy.homes()`. `OriginGeopoint` uses the mission
latitude and longitude; its altitude includes the PlayerStart height because AirSim's
global NED origin is PlayerStart, above the mission's Unreal zero plane. The generator
rejects a changed coordinate convention, PlayerStart or spawn/home disagreement.

`FastPhysicsEngine` integrates the ArduPilot motor output inside AirSim. An external
physics engine would instead require externally supplied vehicle poses.
`SteppableClock` is explicit: it is the pinned plugin's multirotor clock selection for
ArduCopter, and the UDP exchange waits for each controller's motor response. `ClockSpeed`
is `1`; actual wall-clock rate must be measured. Physics must never be paused while
the controllers are running. Start all controllers promptly after the world; stop
the world before the controllers to avoid leaving the world blocked on motor input.

IMU and GPS are enabled explicitly. `Gps.UpdateFrequency` retains the source's
`50` Hz setting. `Gps.UpdateLatency` is `0` because `ArduCopterApi` sends GPS position
and velocity alongside current IMU values; the stock GPS latency would deliberately
put those values behind the IMU. This changes simulated sensor latency, not arming,
EKF or failsafe checks. ArduPilot vehicle movement uses MAVLink; the plugin's AirSim
flight-control methods are unimplemented.

The stock plugin has a geodesy mismatch: its two spawn-home initialization sites use
the spherical `EarthUtils::nedToGeodetic`, while flight updates use the ellipsoidal
`GeodeticConverter`. Mission geodesy also uses WGS84 ellipsoidal radii. Changing the
settings origin cannot make these conventions identical. Preserve the startup home
check and compare runtime measurements before accepting a coupled release.

## Protocol and coupling gate

The pinned source comparison is recorded in
`.local/evidence/coupling/protocol/comparison.json`, with the fetched primary pages,
source hashes and the tag-verified `ArduCopterApi.hpp`.

| Packet field | Cosys emission / ArduPilot interpretation |
| --- | --- |
| `timestamp` | Integer microseconds, parsed as `uint64_t` |
| `imu.angular_velocity` | Body-axis vector, radians/s |
| `imu.linear_acceleration` | Body-axis specific force, m/s² |
| `gps.lat`, `gps.lon`, `gps.alt` | Degrees, degrees, altitude metres |
| `pose.roll`, `pose.pitch`, `pose.yaw` | NED Euler angles, radians |
| `velocity.world_linear_velocity` | NED vector, m/s |
| Optional `rng.distances` | Range array, metres; disabled |
| Optional `lidar.point_cloud` | Flat XYZ triples; disabled; empty arrays fail the ArduPilot parser |
| Optional `rc.channels` | Array; disabled. Cosys joystick order differs from ArduPilot's default RC map |
| Motor response | Eleven native-endian `uint16_t` PWM values, 22 bytes on this little-endian host; `(PWM−1000)/1000` motor normalization |

The required packet fields match. The ArduPilot sensor socket needs the tracked
loopback patch. The separately tracked SITL liveness patch replaces the upstream
unbounded receive/resend loop with one bounded receive and a nonzero exit on failure.
Its rebuilt binary has not flown. A dedicated missing-sensor test exited with status
1 in 1.004510 s and released its ports. No Cosys flight API is used for control.

The first concurrent four-controller diagnostic did **not** pass the gate. Measured
initialized GPS horizontal errors were 2.379611 m for Drone1/2 and 2.394143 m for
Drone3/4; GPS altitude was 2672.722900 m versus the intended 2632.730000 m. The global
transform's `toLocalNed` ignores PlayerStart's offset, causing the additional altitude
error. `airsim-geodesy.patch` corrects that call as well as both spherical conversions.
All four passed firmware and GPS/EKF readiness before the unchanged home assertion
failed. MAVLink home errors were 2.371963 m for Drone1/2 and 2.377226 m for Drone3/4;
their reported altitude was 2672.720000 m. Fence setup therefore did not run.

All four autopilots reported `FPS avg=333.33`, a simulator-timestamp diagnostic.
All four also reported `PreArm: Gyro 0 rate 335Hz < loop rate*1.8 540Hz` and
`PreArm: Main loop slow (222Hz < 300Hz)`. The stock plugin hardcodes its physics
step; there is no JSON period setting. The official AirSim defaults configure the
controller at 300 Hz. Changing the controller rate or rebuilding a faster physics
step requires a fresh gate with the same arming checks. Neither is validated here.

The run did not reach `ready.json`, fence readback or flight. The CLI ended with a
shutdown timeout; the old receive loop then logged sensor loss. Its child processes
were gone when cleanup checked their identities. The task-owned Unreal container
was explicitly killed after its motor receive stalled. Installed services were untouched.

The timing sample covered 19.981361 s with four controllers and both editor processes.
Coupling frame p95 was 29.613117 ms; mean CPU was 204.590664% (one core = 100%).
The live-world frame p95 rose from 16.667039 to 17.435148 ms and GPU-time p95 from
8.366600 to 17.760650 ms. Host GPU load averaged 96.947368% during the diagnostic.
The editor processes were absent from the earlier baseline and had no native frame
telemetry available, so these samples do not isolate coupling cost or prove unchanged
editor performance. The coupling instance has been stopped.

The machine-readable [coupling measurements](../../mission_planner/measurements/cosys-coupling.json)
link all raw evidence and mark unperformed flight checks explicitly. No ten-minute
flight, hover/cruise error distribution or Stage 2 acceptance is claimed.

Capture host/native timing without changing any running process:

```sh
.venv-cosys/bin/python -m world.cosys.runtime_metrics \
  --output .local/evidence/coupling/NEW.jsonl --seconds 30 \
  --native live=/home/brook_avionuas/snr/.local/data/four-drones/runtime.json
```

An Unreal plugin build is required before resuming the geodesy gate. It must occur
in the coupling checkout with its own plugin source/output and a coordinated CPU
budget; the shared recovery release and installed services remain unchanged.

Primary references:

- [Cosys-AirSim 3.5.0 settings](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/docs/settings.md): vehicle coordinates, physics, clock speed, RPC and ArduPilot documentation link.
- [ArduPilot SITL with AirSim](https://ardupilot.org/dev/docs/sitl-with-airsim.html): ArduCopter settings, instance port increments, lockstep startup/shutdown and flight API restrictions. The upstream page is archived; the pinned implementations govern compatibility.
- [ArduCopterApi.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/vehicles/multirotor/firmwares/arducopter/ArduCopterApi.hpp): sensor fields, loopback bind selection and blocking motor receive.
- [AirSimSettings.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/common/AirSimSettings.hpp): canonical `ControlPortLocal` field and `SteppableClock` selection.
- [GpsSimpleParams.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/sensors/gps/GpsSimpleParams.hpp): GPS frequency and latency settings.
- [EarthUtils.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/common/EarthUtils.hpp) and [GeodeticConverter.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/common/GeodeticConverter.hpp): spherical spawn conversion versus WGS84 flight conversion.
