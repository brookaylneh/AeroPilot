# Ground control: four ArduCopter aircraft

## Start mission control

From the repository root, start the mission owner and web server in separate terminals:

```sh
SNR_SCENARIO_DATA_DIR=/srv/snr/.local/data/unreal-scenarios .venv-cosys/bin/python -m mission_planner.gcs.service --case 01A17FAF-5D8E-7F58-ADBD-7F7B59BA7956
npm run build
PORT=3104 SNR_SCENARIO_DATA_DIR=/srv/snr/.local/data/unreal-scenarios npm start
```

Open `http://127.0.0.1:3104/mission?case=01A17FAF-5D8E-7F58-ADBD-7F7B59BA7956`.
Use the UUID of an existing rescue briefing for another case. Only the `briefings/`
subdirectory is read; private scenario truth is excluded. The existing briefing launch
button saves with its established authoring commands and opens the saved case URL.

The service binds `127.0.0.1:8765`; `/api/mission` forwards WebSockets only after the
same-origin check. `SNR_MISSION_PORT` selects the web proxy's explicit local destination.
Mission events are appended to `.local/data/missions/<mission-id>/events.jsonl`, with
original Wonder responses alongside. The [agent catalogue](../agents/README.md) describes
eleven registered tools and the future subsystem catalogue. Supervised is the default;
operator controls and Wonder use the same handlers and approval decisions.

Video has no source in this stage. The local topographic image is the existing simulated
Open Meadow terrain, not a survey of the chosen geographic origin. Position, fence,
mission, mode and health displays come from MAVLink. A link older than two seconds is
shown as lost. No automatic reconnection or failed-command retry is performed.

## SITL build and lifecycle

The mission service launches four independent `arducopter` processes directly, using the built-in `quad` physics model. It owns one MAVLink 2 UDP connection per process on loopback ports 14600–14603. Instances are 0–3 and `MAV_SYSID` is 1–4. No MAVProxy or router is used. Console serial0 and unused serial2 are explicitly `none`; MAVLink uses serial1. The pinned firmware sends three SiK reset words (`0x30 0x20`) before MAVLink on serial1 (upstream `GCS_Common.cpp`, lines 200–215); the driver asserts and consumes exactly that startup preamble, then rejects every malformed MAVLink frame. Each fresh mission has separate EEPROM, flight logs and process output beneath its mission directory. Existing run directories are refused. SIGINT/SIGTERM closes the service and terminates its four children.

The latest stable release verified against the GitHub releases API is **Copter-4.7.2**, commit **fb773567896014da007bca58358704d4a0d8516f**. The official release announcement is dated 11 October 2026; the GitHub release was published on 14 October. The retained API result is `.local/evidence/gcs/github-copter-releases.json`. [Official release](https://github.com/ArduPilot/ardupilot/releases/tag/Copter-4.7.2).

Each startup requests `AUTOPILOT_VERSION` over MAVLink and asserts the official 4.7.2 version and `fb773567` firmware commit prefix before configuring telemetry. The smoke report retains all four firmware responses.

```sh
git clone --depth 1 --branch Copter-4.7.2 --recurse-submodules --shallow-submodules -j4 https://github.com/ArduPilot/ardupilot.git .local/vendor/ardupilot
.venv-cosys/bin/python -m pip install -r requirements.txt
cd .local/vendor/ardupilot
../../../.venv-cosys/bin/python ./waf configure --board sitl
../../../.venv-cosys/bin/python ./waf copter -j4
```

The successful 4.7.2 continuation took **489.164448704 s** with four jobs, recorded in `.local/evidence/gcs/ardupilot-4.7.2-build-continuation-01.json`. Its binary SHA-256 is `ca634fc62f8d4058cf94d00c374831d1c1039cc239491ab7c817669c78e5c835`. This timing covers the explicit continuation only.

The build uses Ubuntu 22.04.5, Python 3.10.12, GCC/G++ 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.3), Git 2.34.1 and GNU Make 4.3. Existing system packages used: `libc6-dev 2.35-0ubuntu3.15`, `zlib1g-dev 1:1.2.11.dfsg-2ubuntu9.2`, `rsync 3.2.7-0ubuntu0.22.04.7`. No system packages were installed. Python runtime pins are pymavlink 2.4.50 and websockets 15.0.1. Build-only packages installed into the existing venv: empy 3.3.4 and pexpect 4.9.0 (ptyprocess 0.7.0); pymavlink dependencies installed were lxml 6.1.3 and fastcrc 0.5.0. The upstream Waf and all submodules are fixed by the release checkout; full revisions and build logs remain in `.local/evidence/gcs/`.

## Origin and coordinate convention

`mission_planner/gcs/geodesy.py:ORIGIN` is the single geographic registration: **Tuolumne Meadows, Yosemite National Park; 37.876432° N, −119.393421° E, 2621.28 m MSL**. The coordinate is published by the [National Park Service for its Tuolumne Meadows geology walk](https://www.nps.gov/planyourvisit/event-details.htm?id=E1999AF6-95B4-C0C1-D78F903E7482512D). The ground elevation is the NPS regional meadow elevation of [8,600 feet](https://www.nps.gov/yose/planyourvisit/tm.htm), converted exactly with 0.3048 m/ft; it is an approximate regional elevation, not a surveyed height at that coordinate. NPS staffs a [Tuolumne Meadows search-and-rescue district](https://www.nps.gov/yose/getinvolved/sar_jobs.htm). This phase is a simulation registration; the existing topographic image is not a survey of Yosemite terrain.

The local tangent-plane conversion linearizes WGS84 at that latitude. With semi-major axis `a=6378137 m`, eccentricity squared `e²=0.0066943799901413165`, `W=sqrt(1−e² sin²φ₀)`, meridional radius `M=a(1−e²)/W³` and prime-vertical radius `N=a/W`, latitude is `φ₀+north/M` and longitude is `λ₀+east/(N cosφ₀)` in radians. The inverse uses the same fixed radii, so local/geodetic round trips are asserted below 1 μm; MAVLink integer-degree encoding adds its documented 1e−7° resolution. This is a local linearization, not a long-distance geodesic projection.

`world/cosys/settings.json` is an AirSim settings file: **its X/Y/Z are metres in NED**, despite the Unreal native coordinate convention being centimetres/+Z up. Therefore X and Y are already north/east metres. `world/README.md` gives the PlayerStart origin 40 m up, so world spawn height is `40−Z=11.45 m`. SITL home MSL is origin ground MSL plus this height. Drone1/Drone2 spawn at north −814.172 m and east −2.5/+2.5 m; Drone3/Drone4 are at north −819.172 m, east −2.5/+2.5 m. All horizontal round trips are asserted, and reported `HOME_POSITION` must match within 1 m. No simulator settings are edited.

## Fence and command safety

The operator explicitly authorized a small launch corridor because all four fixed homes lie south of the requested square. A single concave inclusion polygon preserves the ±804.672 m search square and adds a corridor between east −25/+25 m, extending south to north −844.672 m. Its eight ordered north/east vertices are:

```text
(-804.672, -804.672), (-804.672, -25), (-844.672, -25),
(-844.672, 25), (-804.672, 25), (-804.672, 804.672),
(804.672, 804.672), (804.672, -804.672)
```

The driver uploads these as eight `MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION` items with `param1=8`, using `MAV_MISSION_TYPE_FENCE`. It downloads every item, asserts coordinates and command, then enables the fence. Browser geometry and flight containment checks use that readback. The driver's command guard checks closed polygon containment of the whole route, including segments cutting across the concave corridor shoulders. It checks a 10–100 m above-home flight band, live link/GPS/EKF/arming state, and a 3.5 m minimum distance between 3D route segments. Routes reserve space without relying on arrival timing; current aircraft positions and launch/landing columns also reserve space. This is conservative separation validation, not traffic avoidance or terrain planning. The default autopilot maximum-altitude fence remains 100 m.

Flight modes are GUIDED for takeoff/direct positioning, AUTO for uploaded routes and resume, BRAKE for hold, RTL for return and LAND for landing. Every mode command requires a MAVLink acknowledgement and matching subsequent heartbeat. Speed changes are sent after selecting GUIDED or AUTO: the firmware applies the command to the current flight mode, and entering AUTO initializes its waypoint controller. Resume reapplies the uploaded route's acknowledged speed; QGroundControl export uses it as `hoverSpeed`. Acknowledgements report accepted commands; callers must inspect live telemetry to establish arrival/landing. An upload is downloaded and checked before starting AUTO. No rejected request is resent. The pinned firmware's `MissionItemProtocol::queued_request_send` emits `MISSION_REQUEST`; the upload answers with `MISSION_ITEM_INT` as supported by the documented protocol.

RTL validation reserves its nominal initial climb, direct return leg and final descent to home. Its return altitude follows the verified upstream 15 m minimum, 3:1 cone near home and zero additional climb; an aircraft above 15 m stays at its current altitude. Startup reads and asserts `ARMING_SKIPCHK=0`, `FS_THR_ENABLE=1`, `RTL_ALT_M=15`, `RTL_ALT_FINAL_M=0`, `RTL_CLIMB_MIN_M=0`, `RTL_CONE_SLOPE=3`, `RTL_ALT_TYPE=0`, `FENCE_ALT_MAX=100`, and `FENCE_MARGIN=2`. ArduCopter 4.7 replaced `ARMING_CHECK` with `ARMING_SKIPCHK`; zero skips no checks. These are readbacks, not parameter changes. Planned segments do not model transient braking or controller tracking error. [RTL behavior](https://ardupilot.org/copter/docs/rtl-mode.html) and the pinned `ArduCopter/mode_rtl.cpp` define the flight path.

Explicit requested telemetry rates: HEARTBEAT 2 Hz; GLOBAL_POSITION_INT 10 Hz; SYS_STATUS, GPS_RAW_INT, MISSION_CURRENT, EKF_STATUS_REPORT and EXTENDED_SYS_STATE 5 Hz; HOME_POSITION 1 Hz. Measured position rates are asserted at startup to be ≥5 Hz. Ages over 2 seconds mark a lost link, and a stale position reports 0 Hz. Unknown MAVLink battery/heading values remain null. Positions and sensor values are never synthesized.

## Parameter record

The unmodified upstream `Tools/autotest/default_params/copter.parm` supplies the official built-in Copter simulation calibration baseline. Its complete values are listed below, so the baseline does not hide changes from compiled firmware defaults. No arming or failsafe check is disabled. Additional changes are `MAV_SYSID=1/2/3/4` for aircraft identity, `FENCE_TYPE=5` to use maximum altitude plus the requested polygon (the baseline's home circle has radius 150 m), and `FENCE_ENABLE=1` to enforce it. `FENCE_ACTION`, `FENCE_MARGIN`, `FENCE_ALT_MAX`, `ARMING_SKIPCHK`, radio failsafe and EKF parameters retain upstream defaults.

| Parameter          | Baseline value | Reason                                                  |
| ------------------ | -------------- | ------------------------------------------------------- |
| `FRAME_TYPE`       | `0`            | Upstream built-in quad airframe and controller baseline |
| `FS_THR_ENABLE`    | `1`            | Preserve enabled radio failsafe                         |
| `ARSPD_PIN`        | `1`            | Official SITL Copter baseline                           |
| `ARSPD_BUS`        | `2`            | Official SITL Copter baseline                           |
| `ATC_RAT_YAW_P`    | `0.3`          | Upstream built-in quad airframe and controller baseline |
| `ATC_RAT_YAW_I`    | `0.02`         | Upstream built-in quad airframe and controller baseline |
| `BATT_MONITOR`     | `4`            | Official SITL Copter baseline                           |
| `COMPASS_OFS_X`    | `5`            | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS_Y`    | `13`           | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS_Z`    | `-18`          | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS2_X`   | `5`            | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS2_Y`   | `13`           | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS2_Z`   | `-18`          | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS3_X`   | `5`            | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS3_Y`   | `13`           | Upstream simulated magnetometer calibration             |
| `COMPASS_OFS3_Z`   | `-18`          | Upstream simulated magnetometer calibration             |
| `FENCE_RADIUS`     | `150`          | Official SITL Copter baseline                           |
| `FRAME_CLASS`      | `1`            | Upstream built-in quad airframe and controller baseline |
| `RC1_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC1_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC1_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `RC2_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC2_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC2_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `RC3_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC3_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC3_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `RC4_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC4_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC4_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `RC5_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC5_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC5_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `RC6_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC6_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC6_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `RC7_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC7_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC7_OPTION`       | `7`            | Upstream simulated radio configuration                  |
| `RC7_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `RC8_MAX`          | `2000.000000`  | Upstream simulated radio configuration                  |
| `RC8_MIN`          | `1000.000000`  | Upstream simulated radio configuration                  |
| `RC8_TRIM`         | `1500.000000`  | Upstream simulated radio configuration                  |
| `FLTMODE1`         | `7`            | Upstream simulated radio configuration                  |
| `FLTMODE2`         | `9`            | Upstream simulated radio configuration                  |
| `FLTMODE3`         | `6`            | Upstream simulated radio configuration                  |
| `FLTMODE4`         | `3`            | Upstream simulated radio configuration                  |
| `FLTMODE5`         | `5`            | Upstream simulated radio configuration                  |
| `FLTMODE6`         | `0`            | Upstream simulated radio configuration                  |
| `SIM_BARO_RND`     | `0`            | Official SITL Copter baseline                           |
| `INS_ACCOFFS_X`    | `0.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACCOFFS_Y`    | `0.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACCOFFS_Z`    | `0.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACCSCAL_X`    | `1.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACCSCAL_Y`    | `1.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACCSCAL_Z`    | `1.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACC2OFFS_X`   | `0.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACC2OFFS_Y`   | `0.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACC2OFFS_Z`   | `0.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACC2SCAL_X`   | `1.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACC2SCAL_Y`   | `1.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACC2SCAL_Z`   | `1.001`        | Upstream simulated accelerometer calibration            |
| `INS_ACC3OFFS_X`   | `0.000`        | Upstream simulated accelerometer calibration            |
| `INS_ACC3OFFS_Y`   | `0.000`        | Upstream simulated accelerometer calibration            |
| `INS_ACC3OFFS_Z`   | `0.000`        | Upstream simulated accelerometer calibration            |
| `INS_ACC3SCAL_X`   | `1.000`        | Upstream simulated accelerometer calibration            |
| `INS_ACC3SCAL_Y`   | `1.000`        | Upstream simulated accelerometer calibration            |
| `INS_ACC3SCAL_Z`   | `1.000`        | Upstream simulated accelerometer calibration            |
| `MOT_THST_EXPO`    | `0.65`         | Upstream built-in quad airframe and controller baseline |
| `MOT_THST_HOVER`   | `0.39`         | Upstream built-in quad airframe and controller baseline |
| `MOT_BAT_VOLT_MIN` | `9.6`          | Upstream built-in quad airframe and controller baseline |
| `MOT_BAT_VOLT_MAX` | `12.8`         | Upstream built-in quad airframe and controller baseline |

## Primary protocol references

- [Building on Linux](https://ardupilot.org/dev/docs/building-setup-linux.html), [using SITL and multiple vehicles](https://ardupilot.org/dev/docs/using-sitl-for-ardupilot-testing.html).
- [Mission/fence upload and download](https://ardupilot.org/dev/docs/mavlink-mission-upload-download.html), [inclusion polygons](https://ardupilot.org/copter/docs/common-polygon_fence.html), [Copter parameters](https://ardupilot.org/copter/docs/parameters.html).
- [Message intervals](https://ardupilot.org/dev/docs/mavlink-requesting-data.html), [Copter guided commands](https://ardupilot.org/dev/docs/copter-commands-in-guided-mode.html), [mode changes](https://ardupilot.org/dev/docs/mavlink-get-set-flightmode.html), [BRAKE](https://ardupilot.org/copter/docs/brake-mode.html).
- [QGroundControl plan-file format](https://docs.qgroundcontrol.com/master/en/qgc-dev-guide/file_formats/plan.html): export uses root version 1, mission/fence/rally versions 2, firmware type `MAV_AUTOPILOT_ARDUPILOTMEGA=3`, vehicle type quadrotor=2, seven SimpleItem parameters and the readback inclusion polygon. Empty active missions produce an explicit error. ArduPilot's sequence-zero home item becomes `plannedHomePosition`; actual mission items begin at sequence one.

## Standalone lifecycle check

```sh
.venv-cosys/bin/python -m mission_planner.gcs.fleet --output .local/evidence/gcs/new-fleet-run
# SIGTERM the printed process's parent service, or use Ctrl-C for this CLI.
```

Do not run this alongside the mission service: it deliberately binds the same four ports. All flight/control acceptance uses the service handlers; the standalone entry point exists to verify lifecycle and initialization.

## Measured initialization

`mission_planner/measurements/gcs-sitl.json` summarizes the accepted 4.7.2 run at `.local/evidence/gcs/sitl-4.7.2-01/report.json`: all four instances initialized in **45.836078573 s**, followed by a **10.000522945 s** telemetry sample. Each reported official 4.7.2, commit `fb773567`, GPS fix type 6, 10 satellites and healthy EKF. All eight fence vertices uploaded and read back, and all nine safety-parameter assertions passed on every aircraft. Position rates for Drone1–Drone4 were **9.999142091, 9.996355293, 9.998536545 and 9.998478229 Hz**. Reported home horizontal errors relative to the encoded spawn plan were 0.00359, 0.00813, 0.00925 and 0.01178 m. All four home altitudes were 2632.83 m MSL, 0.10 m above the configured physics origin as the model settled.

`.local/evidence/gcs/sitl-4.7.2-01/cleanup.json` records that all four child PIDs exited and independent UDP binds succeeded on ports 14600–14603 after shutdown. The full accepted 4.7.1 measurement remains under `previous_accepted_runs` in the compact JSON; its raw report remains at `.local/evidence/gcs/sitl-05/report.json`. Earlier initialization failures and their explicit implementation corrections are also retained.

Reproduce this check with `.venv-cosys/bin/python -m mission_planner.gcs.bench_sitl --output .local/evidence/gcs/<new-name>`. It launches real aircraft, samples ten seconds after initialization, and stops them. Do not run alongside the service. Geometric safety checks run with `.venv-cosys/bin/python -m unittest mission_planner.gcs.test_safety`.

## Measured control and browser validation

The committed [acceptance measurements](../measurements/gcs-acceptance.json) contain the
file inventory with line counts, exact samples, known failures and evidence paths.

| Check                       | Recorded result                                                                                                                                                                                                       |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| UI flight sequence          | Passed in 169.778 s: four approved takeoffs to 30 m, two distinct AUTO routes, BRAKE, resume, RTL, then all four landed/disarmed. 21 state checkpoints; no browser errors.                                            |
| Other implemented tools     | All eleven handlers returned successful live results across the agent and operator runs. A separate BRAKE → GUIDED command reached its requested point at 3 m/s; declining a flight returned `operator declined`.     |
| Service refusals            | North 900 m route and unknown `Drone9` rejected explicitly.                                                                                                                                                           |
| Full Wonder flight sequence | **Not passed.** Meta Responses HTTP 500 interrupted a status request and the response after the third successful takeoff. No failed request was retried; the three aircraft were landed through approved UI commands. |
| Wonder tool selection       | 24/25 (96%). Sole final-trial miss: a conceptual autonomy question unnecessarily invoked `get_mission`. All expected/actual arguments and prior trials are preserved.                                                 |
| Receipt-to-render latency   | 22,960 distinct position render samples; p50 80.995 ms, p90 123.081 ms.                                                                                                                                               |
| Panning                     | 457 animation frames during six recorded drags totaling 7.6639 s: 59.630 fps.                                                                                                                                         |
| Memory                      | 611.631 s observer run; samples from 0.139 to 598.250 s. Service RSS 65.289 → 67.879 MiB; browser JS heap 6.723 → 14.236 MiB (peak 32.974 MiB).                                                                       |
| QGC export                  | Drone1 read-back mission with three waypoints and eight fence vertices passed the documented Plan v1/Mission v2 subset validator, `firmwareType: 3`.                                                                  |
| WebSocket boundary          | Same-origin upgrade accepted; foreign/missing origins and altered paths rejected with 403.                                                                                                                            |

Latency starts when Python receives MAVLink `GLOBAL_POSITION_INT` and ends at the first
animation-frame callback after React commits that snapshot. It uses same-host epoch
clocks and excludes upstream autopilot/transport delay. FPS counts animation callbacks
only within the drag intervals. Memory is service resident memory and browser JavaScript
heap, not total browser or GPU memory. The full raw observer data is
`.local/evidence/gcs/performance-1/`; it includes the live flight activity. Later visual
review removed a duplicate lost-link alert; that isolated display correction was not
included in the performance run.

Repeatable acceptance commands, with the service already running:

```sh
.venv-cosys/bin/python -m mission_planner.agents.accept_chat --case <uuid> --part flight --output .local/evidence/gcs/<new-chat-run>
node --import tsx mission_planner/gcs/accept-gcs-ui.mjs 'http://127.0.0.1:3104/mission?case=<uuid>' .local/evidence/gcs/<new-ui-run>
node mission_planner/gcs/measure-gcs-browser.mjs 'http://127.0.0.1:3104/mission?case=<uuid>' .local/evidence/gcs/<new-performance-run> <service-pid> 600
```

Run the flight sequences separately, starting with four landed aircraft. Existing output
paths fail. `--part inspection` exercises read/view requests separately from flights.
The performance observer sends no aircraft commands.

After validation all four aircraft were landed. SIGTERM stopped the service and all four
SITL children in **0.050625 s**; ports 14600–14603 were independently rebound. The local
validation web server was also stopped. See `.local/evidence/gcs/service-shutdown.json`.

## Visual review

The supplied QGC, fleet, flight-view and chat references were viewed, without copying
third-party source or images. The layout uses existing fonts plus OFL IBM Plex Mono via
`web/public/fonts/sources.json` and its checksum pipeline. The agent panel is 420 px at
1920×1080 and 380 px at 1440×900; paired captures verify that all fleet cards and the
composer fit. Sampled text contrast was 6.92–14.69:1; this is a sample of solid surfaces,
not a complete accessibility audit.

All captures below are under `.local/evidence/gcs/screenshots/`, with both `1920` and
`1440` suffixes. Video captures are honest no-source views; telemetry remains live.

| State                  | Capture pattern                                                         | Review                                                                                                                                                      |
| ---------------------- | ----------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| No case / disconnected | `empty-final-{map,video}-*.png`, `disconnected-final-{map,video}-*.png` | Explicit recovery directions; dashes for unavailable values; controls fit both sizes.                                                                       |
| Initializing           | `startup-{map,video}-*.png`                                             | Saved case loaded, vehicle telemetry withheld until verified; no invented aircraft state.                                                                   |
| Approvals              | `ui-flight-1/approval-*.png`, `landing-approval-*.png`                  | Arguments, drone colour and explicit approve/decline controls stay within the panel.                                                                        |
| Routes / fleet         | `ui-flight-1/routes-*.png`                                              | Actual read-back waypoints, active item, aircraft and home markers. Zoom reveals closely spaced launch points.                                              |
| Refusal / log          | `refusal-*.png`, `log-*.png`                                            | Expanded precise error and timestamped autopilot/tool events.                                                                                               |
| Lost link              | `stale-final-*.png`                                                     | A parked Drone3 was deliberately paused; its card shows LINK LOST/0 Hz, commands disabled, and one alert. The process was explicitly resumed after capture. |
| Autonomous mode        | `autonomous-final-*.png`                                                | Clearly labelled execution mode; restored to Supervised after capture.                                                                                      |
| Final map/video        | `reviewed-{map,video}-*.png`                                            | No clipping, visible unit-bearing HUD, current read-back missions and honest no-video state.                                                                |

Review corrected an error banner covering navigation, promotional empty-state copy,
raw inline emphasis markers, and a duplicated lost-link alert. The final captures retain
those corrections; earlier diagnostic captures are preserved separately. The square
terrain image keeps its geographic aspect ratio; solid margins do not invent adjacent
terrain. There are no gradients, blur, decorative animation or simulated camera frames.

## Implementation provenance

The original phase work added the GCS modules/UI, tools, benchmarks and validators now
located in these domain folders. Pre-existing edits were limited to the Wonder client
(tool-output-only turns and instructions), dependency pins, web routing/WebSocket proxy,
briefing launch navigation, and the font source/checksum entries. Short Layout and Running
blocks were originally appended to `AGENTS.md`; the user's later workspace restructure
incorporated them into the current domain layout. The simulator and calibration data
were not changed by this work. The detailed current source line counts are in the
acceptance JSON; raw captures and vendored code remain ignored in `.local/`.
