# Cosys-AirSim

Cosys-AirSim 3.5.0 (`5.8-v3.5.0`) provides the simulated aircraft and cameras inside the Unreal world.

| File                  | Purpose                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `settings.json`       | AirSim settings for the four drones. Positions are **metres in NED**, not Unreal centimetres.                      |
| `world.json`          | Search geometry and drone identities, without target locations.                                                    |
| `landmarks.py`        | Shared validation and directions for the public landmark catalogue.                                                |
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
`SNR_COSYS_PORT` matching that instance. It checks aircraft at rest at their pads and the
configured nadir camera without changing the gimbal or controlling the aircraft.

The 14 m square launch port is in front of Incident Command at north −738 m,
east −27 m, inside the main map bounds. Its 0.30 m slab spans 10.63–10.93 m UE up; the four homes are
11.13 m UE up on a 5 m grid. Exact bilinear terrain extrema under the slab are
10.671875–10.821601076182525 m, so its bottom enters the terrain throughout.
`world.json` owns these positions; settings generation validates the grid directly
without depending on an older `settings.json`. The parking's raised footprint
excludes this open launch area while the command-post and antenna footprints
continue protecting the equipment.
Flight planning uses each measured home and the exact read-back fence. Separate outward departure
lanes are a planning suggestion; the retained southern fence extension is not a mandatory detour.

The fetcher requires the shared `.env`, verifies release archive hashes, and applies
`airsim-ue58.patch`, `airsim-geodesy.patch` and `airsim-sensor.patch`. The first avoids resetting an unchanged
CustomDepth value during startup, which would invalidate Landscape PSO precaching in
UE 5.8. The geodesy patch uses the global NED transform and WGS84 conversion for each
spawn home. Coupled flight requires a game binary built with these patches.
Editor and package builds run `verify-plugin.sh` before compilation. It checks
every tracked AirSim patch in reverse without writing; a missing patch fails the
build before a release can use inconsistent GPS or camera frames.

## Sensor rendering

The native `DroneFoundation` keeps RGB captures at 3840×2160, registers each camera with
texture streaming, and attaches a PCG generation source to each mission camera. Independent
scene captures render without Lumen unless their post-process settings request it, so
`LumenGIEnable` and `LumenReflectionEnable` make Cosys
select Lumen global illumination and reflections, the world's methods. The runtime fails if a
capture lacks those overrides, ray tracing, the final tone-curve source, Lumen show flags,
manual exposure, fixed white balance, or zero bloom and motion blur. Native telemetry records
each camera's lighting, exposure compensation, white balance and resolution, plus the Lumen
scalability state; `coordinates.py` asserts them against `world/unreal/valley-lighting.json`.

### RGB readback

`airsim-ue58.patch` copies BGRA8 sensor targets into GPU staging textures and polls
completion at the render-frame boundary. It reads completed pixels without a synchronous
GPU wait on the render thread; a 15-second timeout is fatal. Float captures retain their
existing path. `SNR.Sensors.AsyncRgbReadback` compares RGB bytes against Unreal's
synchronous readback at 13×7, 257×9 and 3840×2160 to check channel order and padded rows
on the actual RHI. Response timestamps come from `airsim-sensor.patch` (below), not from
readback completion.

## Studio and mission transitions

The local `mission-request.json` contains exactly `mode` (`mission` or `studio`) and a
unique `id`. Native telemetry acknowledges both `mode` and `modeRequestId`; the bridge
waits for its own request, not an old matching mode. Launch disables the Studio viewport
and switches grass generation to the four drone sources. A mission viewer's acknowledged
`drone-view` command activates the selected drone's existing forward `fpv` camera and enables
the viewport for continuous Pixel Streaming video. Closing that viewer disables the mission
viewport again. The stock camera follows the aircraft's position and attitude during hover,
return, landing and disarm, independent of AUTO capture spacing. Offline calibration
captures retain their separate stabilized nadir `front_center` camera. Conversational
automatic observation aims `fpv` as described below; selecting a video feed itself changes
no camera pose or flight command.
Cancellation is acknowledged only
after native reset restores the Studio grass graph/source, viewport rendering, default
character, empty placement and blank brief. The mission owner must cancel its tasks and
land/disarm the aircraft before requesting this reset. Saving and loading remain separate
from this runtime transition.

## Camera acquisition

`capture.FrameCapture` writes unpaused nadir images and pose evidence for offline
calibration. Live missions use `inspection.ForwardCapture` on the forward camera,
as described below. Camera acquisition never selects the operator's video view.

The generator sets a 3840×2160 scene capture with 70° horizontal FOV. The documented
camera `Gimbal` fixes world pitch at −90° and roll at 0° with stabilization 1. Yaw is
deliberately omitted so heading can follow the aircraft; the pinned parser uses NaN
for uncontrolled axes. Runtime `simSetCameraPose` must not change `front_center` during
calibration acquisition. Forward search aiming changes only `fpv`. Exposure is manual with no eye adaptation:
with the physical camera off, manual exposure meters at EV100 0, so
`AutoExposureCompensation` is minus the `exposureEv100` of
`world/unreal/valley-lighting.json` (13). Motion blur amount/max and bloom intensity are zero.
White balance is explicitly 6500 K with tint 0 for the flight. `airsim-sensor.patch`
adds these missing settings to the pinned plugin and applies Unreal's white-balance
overrides. It also samples each image timestamp alongside its camera pose in the
game-thread render callback; the stock timestamp instead follows pixel readback.

The image response owns the camera pose and simulator timestamp. Capture brackets
ground truth with IMU timestamps and records MAVLink positions concurrently; it never
calls `simPause`. The validator interpolates vehicle position and body orientation to
image time, rotates the 0.6 m forward / 0.05 m down camera mount, and compares the
result against the image camera. The explicit acceptance limits in `capture.LIMITS`
are 1° from nadir, 0.75 m camera/ground-truth error, and 2 m image-inferred-body/MAVLink
error. Simulator brackets must fit 2 s, individual truth samples 0.1 s, and MAVLink
receipt brackets 0.5 s. These are rejection criteria, not measured accuracy claims.
UTC is interpolated between the simulator/host clock brackets; the record retains
the complete bracket and its wall duration rather than assuming simulator time is UTC.

Records preserve the calibration schema, coordinate fields and image-response pose.
`vehicleStateBefore`/`After` now contain explicitly labelled simulator ground truth;
ArduCopter's AirSim estimated-state and landed-state APIs are unimplemented. MAVLink
samples supply flight state. Added fields include `imagePath`, `capturedAtUtc`,
`droneId`, `captureId`, `validation` and timing/size measurements. Calibration alone
may pass an actor observation callback; actor translation above 0.01 m rejects the
frame. Mission capture has no actor-placement access.

Calibration saves each JPEG and JSON record before returning it. Rejected images
retain `validation.accepted=false` and exact reasons; errors propagate without retry.
The live forward path saves its own timestamped images and metadata under each
mission's inspection directory; accepted evidence is registered for browser access.

Camera settings and image timing are checked against the pinned
[settings](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/docs/settings.md),
[image APIs](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/docs/image_apis.md),
`AirSimSettings.hpp`, `PIPCamera.cpp`, `UnrealImageCapture.cpp` and `RenderRequest.cpp`.
Archived primary sources and the field-by-field camera review are under
`.local/evidence/coupling/sensor-sources/`.

`camera_metrics.CameraMonitor(directory, fleet)` samples every aircraft using an
independent RPC client and worker. Its requested period is 0.05 s; `camera.jsonl`
retains actual host/simulator timestamps, optical-axis error, IMU attitude, angular
velocity and specific force, plus bracketing MAVLink speed/mode/mission context.
Call its async `start()`/`close()` around a flight and `check()` from the supervisor
to propagate failures. This measures turns and acceleration between image captures.

## Coupled settings

Flight-plan inspections use `inspection.ForwardCapture` and the stock `fpv`
camera. `CameraDefaults` configures request-driven 1920×1080 RGB with the same
fixed exposure, white balance and Lumen settings. No named `fpv` entry is created:
Cosys would replace the stock camera and its mount. The separately configured
3840×2160 nadir camera and calibration capture validation remain unchanged.

Forward capture records the image-owned pose and timestamp, measured horizontal
field of view, vehicle origin and simulator/UTC brackets. Wrong camera identity,
dimensions, an out-of-bracket timestamp or invalid image/pose rejects the frame.
Shadow-floor luminance is recorded as an image-quality measurement: a dark percentile
alone does not establish missing lighting. An entirely black forward frame is saved
losslessly as `<timestamp>-rejected.png` with pose/quality JSON in the inspection directory
before raising. The calibrated nadir capture retains its strict shadow-fill check.
Forward capture neither pauses physics nor selects the operator's video camera. Each inspection
owns one RPC worker; cancellation drains its current acquisition before closing
the client. These are observation poses, not inferred object coordinates or SAR
calibration claims. Changed camera settings require regenerated release settings and a coupled
world restart.

Automatic `observe: true` steps also use that forward camera. `ForwardCapture.aim` computes
the body-relative camera rotation from an observation-derived world point and the
measured vehicle attitude, preserving the stock mount. The capture owner remembers
that world target and reapplies its aim before each image, compensating for changes
in aircraft position and attitude during braking. This is capture-time compensation,
not a native per-render-frame gimbal. Reset clears the target and restores the mount.
`route_look_ahead` projects the
aircraft onto its active leg, then follows the remaining route across short terrain
waypoints. It receives the previous navigation point followed by current/future targets
and clamps look-ahead at the route endpoint after arrival or overshoot. Fly/follow/survey
observation aims at terrain along this route; circle observation aims at its ground-level center.
Hover and elevated views use the explicit `set_camera_view` and `start_inspection` workflow,
scheduled with `wait_for`, rather than an automatic hover checkpoint. Capture metadata records
the observation `step_index`; the mission's `rescue.completed_steps` retains per-step frame counts.
The camera never reads a scenario character's position. Tracking uses the image-owned
pose for every ray, so aircraft and camera rotations cannot be confused with target motion.

`ForwardCapture.view(target, zoom)` centres a world point and sets 1–4× focal zoom
on the same serialized worker as capture and aiming. Zoom uses the original measured
field of view: doubling focal length halves `tan(FOV/2)`, not the angle. The command
reads the setting back, then returns its target, zoom, measured horizontal FOV and
view revision. Each image records `camera_view_revision` and brackets capture with
matching FOV reads. Callers retain one capture owner per drone and serialize view
changes with capture. A selected temporal session survives a camera adjustment;
old boxes are invalidated and only a fresh corresponding observation may guide flight.
The original relative pose and FOV are
restored when the owner resets or closes.

Camera changes affect both SAM images and the existing Pixel Streaming view, without
creating a new video connection or camera. The pinned Cosys
[image API](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/docs/image_apis.md#camera-apis)
defines `simSetCameraPose` and `simSetCameraFov`;
[PIPCamera.cpp](https://github.com/Cosys-Lab/Cosys-AirSim/blob/5.8-v3.5.0/Unreal/Plugins/AirSim/Source/PIPCamera.cpp)
applies FOV to both the main camera and scene captures. Zoom narrows search coverage;
the flight plan must choose overlapping passes for its height, angle and capture rate.

Generate the tracked deployment settings with explicit port and instance choices:

```sh
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port 41451 --instance-base 0 --output world/cosys/settings.json
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

The coupled controller runs at `SCHED_LOOP_RATE=184`, with readback at startup.
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

## Install and restart

Use the [installed-service workflow](../../deploy/README.md#installed-services) to stage a verified
cooked release, its matching Cosys settings and the pinned SITL binary. Record the source revision,
artifact digests, selected `Live` release and runtime configuration in a fresh evidence directory.
Native plugin changes require a rebuilt game package; editing source or settings does not change
a running world. Keep the fixed ports and instance assignments from `.local/runtime.env`.

Land and disarm the fleet, release the viewer and use the world-first shutdown below before
replacing binaries or settings. Start the installed signalling and web services and Unreal;
after its loopback RPC listener is ready, start `snr-mission`. It starts all four controllers
immediately because the world needs their motor replies. Do not start a second fleet or listener.

Verify startup homes, fence and 184 Hz parameter readback, real camera frames and persisted
observation evidence. Live flight, camera cadence, tracking and isolated CPU/GPU/frame timing
require their own recorded acceptance. See the [release validation record](../../mission_planner/measurements/release.json)
for completed checks and remaining limits. A source-level check does not establish installed performance.

For coupled shutdowns, land and disarm first. Do not use the service's
60-second Docker stop timeout while Unreal is still flushing caches. This sequence
signals Unreal, observes only new log output, then stops Mission Control as soon as
world EndPlay has stopped AirSim physics. The log follower is the only helper it
terminates.

```sh
cd /home/brook_avionuas/snr
set -euo pipefail
set -a
. .local/runtime.env
set +a
sudo -v
.venv-cosys/bin/python - <<'PY'
import os
import select
import subprocess
import time
from datetime import datetime, timezone

container = os.environ["SNR_CONTAINER_NAME"]
assert container == "snr-unreal-live"
since = datetime.now(timezone.utc).isoformat()
logs = subprocess.Popen(
    ["docker", "logs", "--follow", "--since", since, container],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
try:
    subprocess.run(["docker", "kill", "--signal=TERM", container], check=True)
    deadline, buffer = time.monotonic() + 60, b""
    while b"UWorld::CleanupWorld for OpenMeadow," not in buffer:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "Unreal EndPlay marker timed out"
        assert select.select([logs.stdout], [], [], remaining)[0], "Unreal EndPlay marker timed out"
        chunk = os.read(logs.stdout.fileno(), 65536)
        assert chunk, "Unreal log ended before EndPlay"
        buffer = (buffer + chunk)[-131072:]
    subprocess.run(["sudo", "-n", "systemctl", "stop", "snr-mission"], check=True)
finally:
    logs.terminate()
    logs.wait(timeout=5)
PY
timeout 180 bash -c 'while systemctl is-active --quiet snr-unreal; do sleep 1; done'
test "$(systemctl show snr-unreal -p ActiveState --value)" = inactive
test "$(systemctl show snr-unreal -p Result --value)" = success
systemctl show snr-unreal -p ExecMainStatus -p Result
test -z "$(docker ps -aq --filter name=^/snr-unreal-live$)"
.venv-cosys/bin/python - <<'PY'
from mission_planner.gcs.sitl import check_ports
check_ports([8765, 41451, 9002, 9012, 9022, 9032, 9003, 9013, 9023, 9033,
             14600, 14601, 14602, 14603, 5501, 5511, 5521, 5531])
PY
```

## Terrain dimensions and sampling

`terrain_height` evaluates the bilinear heightfield and the launch slab top from
`world.json`. The existing southern fence corridor retains the authored apron
triangles from `world/unreal/horizon.py`. `launch_breaks` exposes slab and apron
edges, including legs entirely inside the heightfield, so continuous clearance
checks include the relocated slab. Unknown ground outside these domains raises an error.
That error identifies the coordinates as outside **mapped terrain**. These ground-data
limits are independent of a mission's search perimeter; transit and return can leave
that perimeter while remaining inside the mapped world and its flight fence.

`world.json` is the source of search bounds, heightfield path, per-axis sample counts,
spacing, row/column orientation, encoding, release hash and the fixed sun.
`terrain.py` verifies those relationships and the raw file hash before supplying
north/east heights.
Rows increase east (+Y); columns increase north (+X). The planner, perception
geometry, tool schemas, mission fence and browser maps derive their bounds from
this file. The retained southern extension and relocated homes are explicit infrastructure;
the homes lie inside the main heightfield bounds.

Generate the current numeric valley terrain from the repository root:

```sh
.venv-cosys/bin/python -m world.unreal.valley --evidence-dir .local/evidence/world/valley/<new-run>
```

The generator reconstructs and verifies the analytic meadow before shaping, carving
and grading from `world/unreal/landmarks.json`. It refuses existing output and evidence
destinations, and writes the numeric field and its landscape layer masks under
`.local/world/valley/terrain/`. The browser map `web/public/maps/valley-topographic.png`
is drawn from that field and its masks with 5 m contours:

```sh
.venv-cosys/bin/python -m world.unreal.scripts.generate-minimap --evidence-dir <new-directory>
```

It transposes raw axes and reverses north to put north at the top, and fails unless the
heightfield agrees with the north-up image and disagrees with its mirror and flip. Existing
outputs fail. Current dimensions come only from the world file.

`test_terrain.py` checks asymmetric corners/edge samples, the uncarved analytic
meadow's original-square bit identity, and duplicated extent literals in tracked
source. Historical measurements, font licence text and validated absolute landmark
coordinates are excluded from the literal check. Landmark metadata is still scanned.
Valley authoring tests additionally check drainage, the descending lake-to-boundary
creek with dry banks, basin water inside its footprints, the 37° grid-triangle slope
cap, walkable trails, the fire-road grade, dome height, pad clearance, layer masks,
southern preservation, feature classes, height envelopes and disjoint same-kind
footprints.

Array orientation and resampling follow the primary
[NumPy meshgrid documentation](https://numpy.org/doc/stable/reference/generated/numpy.meshgrid.html),
[SciPy zoom documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.zoom.html)
and [Pillow image documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html).

## Public landmarks

`landmarks.py` owns the schema and loader for the valley catalogue
`world/unreal/landmarks.json`. The document has exactly `schemaVersion` (integer 1),
`worldId` (matching `world.json`), `coordinateSystem`
(`UE_LH_Z_UP_CENTIMETERS`) and a nonempty `landmarks` array.
Each item has exactly these fields:

- `id`: unique stable lowercase letters/digits/underscore/hyphen, starting with a letter or digit.
- `name`: display name; `aliases`: array of alternate names. Names are nonempty,
  trimmed strings. An item's names must be distinct ignoring case; names shared
  across different items are allowed and produce an ambiguous lookup.
- `kind`: `trail`, `road`, `creek`, `pond`, `bridge`, `structure`, `obstacle`, `forest`,
  `ridge`, `outcrop`, `campsite`, `parking` or `command_post`.
  `structure` means roofed and enterable. `obstacle` supplies height clearance and
  directions only; it has no behavior-prior weight, aerial occlusion or structure-check task.
- `heightM`: required finite nonnegative height in metres above terrain. Trails,
  roads, creeks and ponds have exactly zero height. Any positive height requires
  a polygon footprint; the search agent's route contract reserves ten metres above
  terrain plus `heightM` over the full footprint. Subject and clue placements never
  enter this catalogue.
- `geometry`: exactly `type` and `points`. `type` is `point`, `polyline` or
  `polygon`; `points` is an array of objects with exactly finite numeric `x`, `y`,
  `z` in Unreal centimetres (+X north, +Y east, +Z up). Every coordinate is inside
  the corresponding `world.json.searchBoundsCm` range. A point has one vertex;
  a polyline has at least two; a polygon has at least three distinct vertices,
  implicit closure, nonzero horizontal area and no self-intersections.
  Adjacent vertices cannot have the same horizontal position. Trails, roads,
  creeks and ridges require polyline geometry. All undeclared fields fail validation.

`load()` reads those two authoritative files; missing or invalid data raises.
`find(landmarks, name)` matches display names and aliases exactly ignoring case,
returning all matching records ordered by ID. A missing name returns the known
display names. It does not trim the query, approximate spelling or select an
ambiguous result.

`directions(landmarks, north_m, east_m)` returns `text`, `nearest` and `linear`.
`nearest` contains `id`, `name`, `kind`, `distance_m`, `compass` and the nearest
`point` in mission north/east metres. `linear` is null when there are no lines;
otherwise it has the same fields plus `side`. Distances are horizontal, using
exact segment projections and zero distance inside polygons. Distance ties use
the stable ID; equal segment distances use vertex order. Compass directions use
eight equal bearing sectors. Side is the compass side of the nearest segment,
`on`, or `beyond endpoint`; creek sides are called banks without inferring flow
direction. These are geometric location descriptions, not walkable routes.
