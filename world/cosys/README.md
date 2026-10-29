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
`SNR_COSYS_PORT` matching that instance. It checks aircraft at rest at their pads and the
configured nadir camera without changing the gimbal or controlling the aircraft.

The fetcher requires the shared `.env`, verifies release archive hashes, and applies
`airsim-ue58.patch`, `airsim-geodesy.patch` and `airsim-sensor.patch`. The first avoids resetting an unchanged
CustomDepth value during startup, which would invalidate Landscape PSO precaching in
UE 5.8. The geodesy patch uses the global NED transform and WGS84 conversion for each
spawn home. Coupled flight requires a game binary built with these patches; the recovery
package does not contain the geodesy correction.
Editor and package builds run `verify-plugin.sh` before compilation. It checks
every tracked AirSim patch in reverse without writing; a missing patch fails the
build before a release can use inconsistent GPS or camera frames.

## Camera acquisition

`capture.FrameCapture` is the unpaused image writer shared by missions and calibration.
`sensor.MissionSensor` starts one worker per aircraft and triggers an image for every
12 metres accumulated in armed AUTO flight. Ground track follows successive MAVLink
positions, including turns; movement outside AUTO does not accumulate distance.
Only one request per aircraft may be in flight. A request still running at the next
trigger fails loudly. Network errors propagate without another request.

The generator sets a 3840×2160 scene capture with 70° horizontal FOV. The documented
camera `Gimbal` fixes world pitch at −90° and roll at 0° with stabilization 1. Yaw is
deliberately omitted so heading can follow the aircraft; the pinned parser uses NaN
for uncontrolled axes. Runtime `simSetCameraPose` changes these gimbal targets and
must not be used during a sensor flight. Equal exposure minimum/maximum values fix
the existing exposure profile at 13. The actual compensation key is
`AutoExposureCompensation`. Motion blur amount/max and bloom intensity are zero.
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

JPEG images and JSON records are written under the supplied mission directory before
the accepted-frame callback runs. That callback is the single search/perception
ingestion path. Rejected images remain with `validation.accepted=false` and explicit
reasons; they are excluded from ingestion and the feed. `sensor.jsonl` records every
attempt, requested spacing, measured camera displacement, latency and encoded sizes.
Acquired ground-track spacing integrates the MAVLink path up to each image time;
it retains turn distance and includes travel between the trigger and acquisition.
Accepted captures also produce a 960×540 JPEG preview at quality 75; only this preview
is sent to the browser at capture cadence. Full images remain in the mission directory.

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

## Installed cutover after merge

These are operator commands for the deployment window, not development commands.
Run them as brook_avionuas only after the accepted revision is merged into a clean
installed checkout. Land and disarm the installed fleet and release the authoring
player first. Keep the intended saved case's Mission Control page ready to reload.

```sh
cd /home/brook_avionuas/snr
set -euo pipefail
umask 077
test -z "$(git status --porcelain)"
export PATH="$PWD/.local/toolchains/bin:$PATH"
export SNR_CUTOVER_SOURCE=/home/brook_avionuas/snr-coupling
export SNR_CUTOVER_RELEASE="$PWD/.local/unreal/releases/coupling-sensor-20261028"
export SNR_CUTOVER_EVIDENCE="$PWD/.local/evidence/coupling/cutover-20261028"
mkdir "$SNR_CUTOVER_EVIDENCE"
cp .local/runtime.env "$SNR_CUTOVER_EVIDENCE/runtime-before.env"
readlink -f .local/unreal/Live > "$SNR_CUTOVER_EVIDENCE/previous-release.txt"
git rev-parse HEAD > "$SNR_CUTOVER_EVIDENCE/installed-commit.txt"
ss -lntup > "$SNR_CUTOVER_EVIDENCE/listeners-before.txt"
sudo systemctl stop snr-mission
sudo systemctl stop snr-unreal
sudo systemctl stop snr-web snr-signalling
```

That initial order applies to the currently installed built-in-physics fleet.
Subsequent coupled shutdowns use the ordered procedure below. Stage the accepted
release without modifying its source or cooked-content-recovery. Only the verified
cooked Paks files are hardlinked; executable, libraries, settings and other runtime
files receive independent copies. The accepted SITL executable is copied into the
new release too.

```sh
.venv-cosys/bin/python - <<'PY'
import hashlib
import json
import os
import shutil
from pathlib import Path

root = Path.cwd()
source = Path(os.environ["SNR_CUTOVER_SOURCE"])
destination = Path(os.environ["SNR_CUTOVER_RELEASE"])
evidence = Path(os.environ["SNR_CUTOVER_EVIDENCE"])
receipt_path = source / ".local/evidence/coupling/stage2/sensor-build-02/release.json"
receipt = json.loads(receipt_path.read_text())
release = Path(receipt["release"])
assert release == source / ".local/unreal/releases/coupling-sensor"

def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

files = receipt["files"]
assert len(files) == len({item["path"] for item in files})
assert {str(p.relative_to(release)) for p in release.rglob("*") if p.is_file()} == {
    item["path"] for item in files
}
destination.mkdir()
for item in files:
    relative = Path(item["path"])
    assert not relative.is_absolute() and ".." not in relative.parts
    original, copied = release / relative, destination / relative
    assert original.is_file() and not original.is_symlink()
    assert original.stat().st_size == item["bytes"] and digest(original) == item["sha256"]
    copied.parent.mkdir(parents=True, exist_ok=True)
    if (item["method"] == "hardlink_unchanged_content"
            and relative.parts[:3] == ("ScenarioStudio", "Content", "Paks")):
        os.link(original, copied)
    else:
        shutil.copy2(original, copied)
    assert digest(copied) == item["sha256"]
shutil.copy2(receipt_path, evidence / "game-source-receipt.json")
accepted = json.loads(
    (source / ".local/evidence/coupling/stage2/accepted-stage1.json").read_text()
)
sitl = accepted["builds"]["SITL"]
assert digest(Path(sitl["binary"])) == sitl["binary_after_sha256"]
shutil.copy2(sitl["binary"], destination / "arducopter")
assert digest(destination / "arducopter") == sitl["binary_after_sha256"]
(evidence / "sitl-source-receipt.json").write_text(json.dumps(sitl, indent=2) + "\n")

def environment(path):
    rows = [line for line in path.read_text().splitlines() if line and not line.startswith("#")]
    pairs = [line.split("=", 1) for line in rows]
    assert all(len(pair) == 2 for pair in pairs)
    assert len(pairs) == len({pair[0] for pair in pairs})
    return dict(pairs)

runtime = environment(root / ".local/runtime.env")
example = environment(root / "deploy/runtime.env.example")
for key in (
    "SNR_SITL_MODEL", "SNR_SITL_INSTANCE_BASE", "SNR_SITL_PORT_STEP",
    "SNR_COSYS_PORT", "SNR_MAVLINK_BASE_PORT", "SNR_SITL_RC_BASE_PORT",
    "SNR_PIXEL_STREAMING", "SNR_UNREAL_IMAGE", "SNR_SIGNALLING_LOG_DIR",
):
    runtime[key] = example[key]
runtime.update(
    SNR_SITL_BINARY=str(destination / "arducopter"),
    SNR_PACKAGE_ROOT=str(root / ".local/unreal/Live"),
    SNR_COSYS_SETTINGS=str(destination / "cosys-settings.json"),
    SNR_WORLD_CONFIG=str(destination / "world.json"),
)
assert not set(example).difference(runtime), "Required runtime fields are missing"
next_env = root / ".local/runtime.env.coupling"
with next_env.open("x") as stream:
    stream.write("".join(f"{key}={value}\n" for key, value in runtime.items()))
next_env.replace(root / ".local/runtime.env")
PY
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port 41451 --instance-base 0 \
  --output "$SNR_CUTOVER_RELEASE/cosys-settings.json"
sha256sum "$SNR_CUTOVER_RELEASE/cosys-settings.json" \
  > "$SNR_CUTOVER_EVIDENCE/deployment-settings.sha256"
test ! -e .local/unreal/Live.coupling
ln -s "$SNR_CUTOVER_RELEASE" .local/unreal/Live.coupling
mv -Tf .local/unreal/Live.coupling .local/unreal/Live
npm ci
npm run build
sudo bash deploy/install-services.sh
sudo test ! -e /etc/systemd/system/snr-unreal.service.d/coupled-shutdown.conf
sudo tee /etc/systemd/system/snr-unreal.service.d/coupled-shutdown.conf >/dev/null <<'UNIT'
[Service]
ExecStop=
SuccessExitStatus=143
UNIT
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/snr-unreal.service
set -a
. .local/runtime.env
set +a
```

The environment update takes coupling fields from the installed profile in
deploy/runtime.env.example; web, signalling, TURN, viewport and resource settings
retain their installed values. Regeneration deliberately changes the copied slot
settings to RPC 41451 and instances 0–3. The installer renders the actual systemd
units/drop-ins, reloads systemd and enables the services without starting them.
The additional drop-in accepts the measured container exit status 143 and clears
Docker stop: the container removes itself after exit, so an ensuing ExecStop must
not try to stop it again. Use the ordered coupled shutdown below instead of stopping
the Unreal unit directly.

```sh
ss -lntup > "$SNR_CUTOVER_EVIDENCE/listeners-before-start.txt"
.venv-cosys/bin/python - <<'PY'
from mission_planner.gcs.sitl import check_ports
check_ports([3001, 8080, 8888, 8765, 41451,
             9002, 9012, 9022, 9032, 9003, 9013, 9023, 9033,
             14600, 14601, 14602, 14603, 5501, 5511, 5521, 5531])
PY
sudo systemctl start snr-signalling snr-web
sudo systemctl start snr-unreal
timeout 120 bash -c 'until ss -H -ltn sport = :41451 | rg -q "127.0.0.1:41451"; do sleep 1; done'
sudo systemctl start snr-mission
```

Reload the intended saved case immediately: snr-mission starts idle and that
connection creates all four controllers. Verify the unchanged startup checks,
184 Hz readback, real camera frames and persisted search coverage. Keep the
mission's case selected across reconnects. With the editor and development worlds
stopped by their owners, repeat the recorded flight/capture, feed and pan checks;
record isolated process CPU, GPU and frame timing before accepting installed
performance. Development coexistence is not the installed performance result.

For subsequent coupled shutdowns, land and disarm first. Do not use the service's
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

`world.json` is the source of search bounds, heightfield path, per-axis sample counts,
spacing, row/column orientation, encoding and release hash. `terrain.py` verifies
those relationships and the raw file hash before supplying north/east heights.
Rows increase east (+Y); columns increase north (+X). The planner, perception
geometry, tool schemas, mission fence and browser maps derive their bounds from
this file. The launch corridor and authored homes remain separate infrastructure.

Generate a new terrain directory with these commands from the repository root:

```sh
.venv-cosys/bin/python -m world.unreal.scripts.generate-open-meadow --evidence-dir .local/evidence/world/<new-run>
.venv-cosys/bin/python -m world.unreal.scripts.generate-vegetation-mask --evidence-dir .local/evidence/world/<new-run>
.venv-cosys/bin/python -m world.unreal.scripts.generate-minimap --evidence-dir .local/evidence/world/<new-run>
```

The generator refuses an existing destination. It verifies every sample in the
original calibration square against the immutable vendor heightfield before
writing under `.local/world/`. The original release dimensions and hash are
explicit regression constants in the generator; current dimensions come only
from the world file. Mask generation retains its deterministic noise, trail and
coverage equations with per-axis texture dimensions, so physical patch scale is
preserved. The minimap transposes raw axes and reverses north to put north at the
top. Each command requires an explicit evidence directory and refuses an existing report.
Use the same new directory under `.local/evidence/world/` for these three commands.

`test_terrain.py` checks asymmetric corners/edge samples, original-square bit
identity and duplicated extent literals in tracked source. Historical measurement
records and font licence text are excluded from the literal check.

Array orientation and resampling follow the primary
[NumPy meshgrid documentation](https://numpy.org/doc/stable/reference/generated/numpy.meshgrid.html),
[SciPy zoom documentation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.ndimage.zoom.html)
and [Pillow image documentation](https://pillow.readthedocs.io/en/stable/reference/Image.html).
