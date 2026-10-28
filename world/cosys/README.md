# Cosys-AirSim

Cosys-AirSim 3.5.0 (`5.8-v3.5.0`) provides the simulated aircraft and cameras inside the Unreal world.

| File                  | Purpose                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `settings.json`       | AirSim settings for the four drones. Positions are **metres in NED**, not Unreal centimetres.                      |
| `world.json`          | Search geometry and drone identities, without target locations.                                                    |
| `materials.csv`       | Empty segmentation material table that Cosys requires at startup.                                                  |
| `client.py`           | Python bridge: explicit world/vehicle frames, telemetry, RGB capture with pose metadata.                           |
| `coordinates.py`      | Checks live Cosys poses against native Unreal instrumentation.                                                     |
| `configure-drones.py` | Generates ArduCopter settings from `world.json` and mission geodesy with explicit RPC port and SITL instance base. |
| `fetch-cosys.py`      | Downloads and verifies the pinned plugin into the Unreal project.                                                  |

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
spawn home. Coupled flight requires a game binary built with both patches; the recovery
package does not contain the geodesy correction.

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
the world physics before the controllers to avoid leaving the world blocked on motor input.
Unreal can continue flushing caches after its world has ended play.

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

| Packet field                          | Cosys emission / ArduPilot interpretation                                                                              |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `timestamp`                           | Integer microseconds, parsed as `uint64_t`                                                                             |
| `imu.angular_velocity`                | Body-axis vector, radians/s                                                                                            |
| `imu.linear_acceleration`             | Body-axis specific force, m/s²                                                                                         |
| `gps.lat`, `gps.lon`, `gps.alt`       | Degrees, degrees, altitude metres                                                                                      |
| `pose.roll`, `pose.pitch`, `pose.yaw` | NED Euler angles, radians                                                                                              |
| `velocity.world_linear_velocity`      | NED vector, m/s                                                                                                        |
| Optional `rng.distances`              | Range array, metres; disabled                                                                                          |
| Optional `lidar.point_cloud`          | Flat XYZ triples; disabled; empty arrays fail the ArduPilot parser                                                     |
| Optional `rc.channels`                | Array; disabled. Cosys joystick order differs from ArduPilot's default RC map                                          |
| Motor response                        | Eleven native-endian `uint16_t` PWM values, 22 bytes on this little-endian host; `(PWM−1000)/1000` motor normalization |

The required packet fields match. The ArduPilot sensor socket needs the tracked
loopback patch. The separately tracked SITL liveness patch replaces the upstream
unbounded receive/resend loop with one bounded receive and a nonzero exit on failure.
Requested SIGTERM exits successfully; missing sensor input exits with an error.
The measurement file records both isolated termination tests and complete flight shutdowns. No Cosys flight API is used for control.

The coupled controller runs at `SCHED_LOOP_RATE=100`, with readback at startup.
The pinned ArduPilot prearm checks require gyro input at least `1.8 ×` the configured
loop rate and a filtered main-loop rate at least `0.90 ×` it. The stock Cosys physics
period remains 3 ms. `FPS avg` in the SITL log describes sensor timestamps; DataFlash
`PM.LR` reports the filtered controller rate. Neither alone measures wall-clock speed.

`sitl-gps-cadence.patch` preserves the GPS emulator's scheduled phase on discrete
physics ticks. Resetting the publication epoch to each actual update produced
201 ms periods; the receiver rounds those to 200 ms, causing false timestamp lag.
The patch retains the same GPS model, frequency and delay. Invalid frequencies or
missed whole periods exit with an error; it sends no catch-up samples.

`mission_planner.gcs.coupling_gate` records all four aircraft through GUIDED takeoff,
twelve AUTO waypoints, BRAKE/resume, sustained flight, RTL and a separate LAND sortie.
It retains MAVLink receipt ages, RPC brackets, native NED origins, IMU simulator times,
GPS/EKF health, mission sequences and hover/cruise position distributions. Native
origins identify vehicle frames; no target placement enters flight control.

The gate's required `--seconds` duration applies to both wall and simulator clocks.
Its shutdown marker transfers control to the supervising experiment: terminate the
owned Unreal instance, acknowledge completion of world `EndPlay`, then let the gate
terminate its autopilots. `UWorld::CleanupWorld` follows `World->EndPlay()` in
`UGameEngine::PreExit`; the AirSim physics thread has stopped at that point.
Do not wait for all renderer cache flushing before stopping the controllers.

The machine-readable [coupling measurements](../../mission_planner/measurements/cosys-coupling.json)
contain the accepted runs, failures, source/build hashes and raw evidence paths.

Capture host/native timing without controlling any aircraft:

```sh
.venv-cosys/bin/python -m world.cosys.runtime_metrics \
  --output .local/evidence/coupling/NEW.jsonl --seconds 30 \
  --native live=/home/brook_avionuas/snr/.local/data/four-drones/runtime.json
```

The sampler selects SITL by `SNR_SITL_BINARY`, records process identities and CPU
counters, and reads only the timing fields from native telemetry. GPU utilization
is host-wide; native GPU timing is per world. Editor CPU is recorded when present;
editor frame time requires its own instrumentation. Development uses a capped
320×180 viewport without Pixel Streaming; isolated performance belongs to cutover.

Primary references:

- [Cosys-AirSim 3.5.0 settings](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/docs/settings.md): vehicle coordinates, physics, clock speed, RPC and ArduPilot documentation link.
- [ArduPilot SITL with AirSim](https://ardupilot.org/dev/docs/sitl-with-airsim.html): ArduCopter settings, instance port increments, lockstep startup/shutdown and flight API restrictions. The upstream page is archived; the pinned implementations govern compatibility.
- [ArduCopterApi.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/vehicles/multirotor/firmwares/arducopter/ArduCopterApi.hpp): sensor fields, loopback bind selection and blocking motor receive.
- [AirSimSettings.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/common/AirSimSettings.hpp): canonical `ControlPortLocal` field and `SteppableClock` selection.
- [GpsSimpleParams.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/sensors/gps/GpsSimpleParams.hpp): GPS frequency and latency settings.
- [EarthUtils.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/common/EarthUtils.hpp) and [GeodeticConverter.hpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/AirLib/include/common/GeodeticConverter.hpp): spherical spawn conversion versus WGS84 flight conversion.
