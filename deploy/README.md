# Setup and deployment

## Step-by-step setup

For a judge reviewing code, start with the [credential-free checks](../README.md#run-the-code-checks). For the running demo, use the [hosted application](http://35.81.229.78/). The steps below install the complete simulation on a **new, dedicated host**. They are not an upgrade procedure for an occupied demo server.

### 1. Check the prerequisites

| Requirement                                                                    | Needed for                                 |
| ------------------------------------------------------------------------------ | ------------------------------------------ |
| x86-64 Linux, Node 22, Python 3.10 and ripgrep (`rg`)                          | Application and source checks              |
| NVIDIA GPU, compatible driver, Vulkan, Docker and NVIDIA Container Toolkit     | Unreal rendering and local SAM inference   |
| Dedicated service account, uid 1002 / gid 1003                                 | Current device image and systemd installer |
| Unreal Engine 5.8.2 and the authorized world content                           | Building the matching simulation package   |
| Meta Model API credential                                                      | Wonder mission planning                    |
| Hugging Face access and the relevant model licenses                            | Downloading model weights                  |
| Modal credentials and H200 capacity, or a compatible existing Glimmer endpoint | Evidence review                            |
| A public IPv4 address and TURN networking                                      | Browser video outside the host             |

The pinned host installer targets **Ubuntu 22.04 with AWS kernel 6.8.0-1066-aws**. Review [Provision](#provision) before running it; it is not a generic Linux installer. The qualified SAM process uses a 16-billion-byte software GPU budget; this is not the memory requirement for SAM and Unreal together. See the [measured hardware and scope](../mission_planner/measurements/aeropilot-v1.json).

The public checkout includes source, tests, terrain, public map geometry, portrait renders and asset manifests. It excludes model weights, Unreal Engine, cooked world content, raw mission data and the original warmup fixture. Obtain the world content under the [asset terms](../world/unreal/assets.md), or follow the domain authoring instructions to rebuild it from the referenced inputs. There is no public download of the complete licensed demo world in this repository.

### 2. Clone and install application dependencies

Provision the service account and pinned host toolchains using [Provision](#provision). In that account's checkout, with Node 22 and Python 3.10 on `PATH`:

```sh
git clone https://github.com/brookaylneh/AeroPilot.git
cd AeroPilot
npm ci
python3.10 -m venv .venv-cosys
.venv-cosys/bin/python -m pip install -r requirements.txt
mkdir -p .local/vendor .local/build .local/run .local/data .local/evidence
```

On a host provisioned at `SNR_ROOT`, use that existing clone instead of cloning a second time. The installed service scripts expect the toolchain executables under `.local/toolchains/bin/`; [Toolchains](#toolchains) gives their pinned versions and paths.

### 3. Configure credentials and host values

```sh
cp .env.example .env
cp deploy/runtime.env.example .local/runtime.env
chmod 600 .env .local/runtime.env
```

Edit `.env` privately. Runtime entry points require `HF_TOKEN`, `MODEL_API_KEY`, `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`. Remote clients also need their own `SAM_URL`, `GLIMMER_URL`, `SAM_AUTHORIZATION` and `GLIMMER_AUTHORIZATION`. The local live-video detector uses a Unix socket rather than `SAM_URL`.

Edit `.local/runtime.env`: replace `/srv/snr` paths with the actual checkout path; set `SNR_ROOT`, public/private IPs, `SNR_ORIGIN`, the GPU UUID from `nvidia-smi -L`, and the image identities and artifact paths produced below. Keep `SNR_SLOTS=1` initially. The example image identities describe a qualified build and are not downloadable registry releases. Build or explicitly import the corresponding images on the new host.

```sh
export PATH="$PWD/.local/toolchains/bin:$PATH"
set -a
. .local/runtime.env
set +a
.venv-cosys/bin/python -c 'from mission_planner.environment import load_env; load_env()'
```

The final command validates credential presence without printing values. Neither `.env` nor `.local/` belongs in Git.

### 4. Build tracking and autopilot dependencies

```sh
bash mission_planner/gcs/build-csrt.sh
bash mission_planner/gcs/build-sitl.sh .local/build/ardupilot
```

Both require new destination directories. CSRT uses a pinned Emscripten container and does not use the GPU. SITL fetches the recorded ArduPilot revision, applies all four project patches and runs the battery-reset unit test. Set `SNR_SITL_BINARY` in `.local/runtime.env` to the absolute path of `.local/build/ardupilot/build/sitl/bin/arducopter`. This patched binary is required for the demo battery reset control.

### 5. Prepare the world and video transport

Use [Unreal build and packaging](../world/unreal/README.md#build-and-package) and [character authoring](../world/unreal/characters/README.md) to restore or author the matching world content. Set `UE_ROOT` to the licensed engine installation. Acquire the pinned Cosys plugin and verify its required patches before building:

```sh
.venv-cosys/bin/python -m world.cosys.fetch_cosys
bash world/cosys/verify-plugin.sh
bash world/unreal/scripts/setup-pixel-streaming.sh
bash deploy/fetch-caddy.sh
bash deploy/fetch-browser.sh
```

Follow [Rendering and packaging](../world/unreal/README.md#rendering-and-packaging) with explicit world/settings manifests and the matching pipeline cache. The resulting Linux package must include the executable, cooked content, manifests and `pso-cache.txt`; a source checkout alone is not a runnable Unreal package. Build the runtime image as documented in [Run the packaged world](../world/unreal/README.md#run-the-packaged-world), and use its immutable image ID in `SNR_UNREAL_IMAGE`.

For a new installation, configure TURN once:

```sh
set -a
. .local/runtime.env
set +a
.venv-cosys/bin/python world/unreal/scripts/configure-turn.py \
  --public-ip "$SNR_PUBLIC_IP" --private-ip "$SNR_PRIVATE_IP" --rotate
```

This creates private files under `.secrets/`. Configure the [security group](#security-group) for HTTP and TURN; keep Cosys, MAVLink, mission and signalling listeners private.

### 6. Prepare the local AeroPilot-v1 device service

Build the pinned image using its minimal context:

```sh
bash mission_planner/agents/services/build-sam.sh snr-sam31:aeropilot-v1

docker image inspect --format '{{.Id}}' snr-sam31:aeropilot-v1
```

Set `SNR_SAM_IMAGE` to that returned image ID. Download the published checkpoint into a new local destination, then verify it:

```sh
mkdir -p .local/vendor/sam31/aeropilot-v1-259ecc64/cache
test ! -e .local/vendor/sam31/aeropilot-v1-259ecc64/aeropilot_v1.pt
curl --fail --location --retry 0 --connect-timeout 30 --max-time 1800 \
  https://huggingface.co/AeroPilot/AeroPilot-v1/resolve/259ecc64a35dece97f443fe650d3580dd8efaf5c/aeropilot_v1.pt \
  --output .local/vendor/sam31/aeropilot-v1-259ecc64/aeropilot_v1.pt
printf '%s  %s\n' \
  9c0813310a2e82c9cbf8347216b51cc27ac0898d360693e70c4b4cdbc12b72b9 \
  .local/vendor/sam31/aeropilot-v1-259ecc64/aeropilot_v1.pt | sha256sum --check --strict
```

Set the checkpoint/cache paths and GPU UUID in `.local/runtime.env`. The checkpoint is 3,502,749,201 bytes and contains the fine-tuned detector plus the preserved official video tracker.

A valid warmup manifest and its images are **required before startup**. See [Preparing a warmup fixture](../mission_planner/agents/services/README.md#preparing-a-warmup-fixture) for the exact folder layout and manifest creation. Use images you have permission to process. A newly created fixture must be qualified; it does not reproduce the committed benchmark simply by having the same dimensions. Set `SNR_SAM_WARMUP` to its absolute `protocol.json` path.

### 7. Configure Glimmer and the separate calibration endpoint

The live application uses Wonder through Meta Model API and Glimmer through the configured endpoint. If using an existing compatible Glimmer service, put its endpoint and full authorization header in `.env` and verify the [request contract](../mission_planner/agents/README.md).

For your own Modal deployment, follow [Modal calibration SAM and Glimmer](../mission_planner/agents/services/README.md#modal-calibration-sam-and-glimmer). Before deployment, create the `snr-weights` Volume and stage these artifacts from the **pinned revisions listed there**:

- `Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf`
- `mmproj-Muse-Glimmer-30B-Q4_K_M.gguf`
- `sam3.1_multiplex.pt` from `facebook/sam3.1` for the independent calibration endpoint

Accept the applicable model terms and verify each file's SHA-256 against the pinned Hugging Face metadata or [recorded hashes](../mission_planner/measurements/modal.json). Upload them under those exact filenames at the Volume root. With verified downloads under `.local/vendor/modal-weights/`, the first-time commands are:

```sh
set -a
. .env
set +a
.venv-cosys/bin/modal volume create snr-weights
.venv-cosys/bin/modal volume put snr-weights \
  .local/vendor/modal-weights/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf /
.venv-cosys/bin/modal volume put snr-weights \
  .local/vendor/modal-weights/mmproj-Muse-Glimmer-30B-Q4_K_M.gguf /
.venv-cosys/bin/modal volume put snr-weights \
  .local/vendor/modal-weights/sam3.1_multiplex.pt /
.venv-cosys/bin/modal deploy -m mission_planner.agents.services.app
```

Do not overwrite an existing workspace's weight files. Create Modal proxy credentials for the new endpoints and store their full headers in `.env`. This deployment starts both warm Servers and incurs provider charges; live detection still uses the local AeroPilot-v1 service. The services do not download weights on startup.

### 8. Build a release and install the services

Check all paths in `.local/runtime.env` again and load it. Follow [Installed services](#installed-services) in order: stage the prepared Unreal package, generate slot configuration, create a detached release checkout, build its own browser bundle and install the systemd units. Start vision, TURN, signalling and Unreal before the mission service; the mission service waits for its Cosys listener. Start the web server and Caddy last.

The installer requires a clean detached checkout, the service uid/gid above, working CSRT artifacts, the terrain hash, TURN configuration and the prepared Unreal release. It deliberately fails when a prerequisite is missing. Do not run the evidence-registry conversion on a fresh installation; it is only for old mission data.

### 9. Verify the new installation

```sh
systemctl is-active snr-web snr-mission@0 snr-vision@0 \
  snr-signalling@0 snr-unreal@0 snr-turn snr-caddy
curl --fail --max-time 15 http://127.0.0.1:3001/healthz
curl --fail --max-time 15 "$SNR_ORIGIN/healthz"
```

With the mission and vision services idle, run the [SAM qualification](../mission_planner/agents/services/README.md#qualification) on the selected device. On the new host, run the [browser acceptance](#installed-services) and [shared-session acceptance](../web/README.md) before opening it to judges. Browser acceptance creates a scenario and requires an idle mission and available video controller. Follow the root README's demo flow for a manual search; unit tests alone do not establish live detection or flight performance.

Development uses the single `127.0.0.1:5174` listener with `npm run dev` after the backend services are configured. Use the documented SSH tunnel. Stop a coupled simulation with [the ordered shutdown](../world/cosys/README.md#install-and-restart); stopping autopilots while Unreal is waiting for their motor replies can stall the world.

## Host reference

The project runs as a dedicated service user with Docker and video group membership, at the
checkout path `SNR_ROOT` (`/srv/snr` and user `snr` in the examples below). Its uid and gid must
match the SAM device image (1002:1003 in the qualified build). `host.sh` reads `SNR_SERVICE_USER`,
`SNR_SERVICE_UID`, `SNR_SERVICE_GID` and `SNR_ROOT` from the environment. Keep application data
and toolchains on the persistent root volume.
The NVMe instance store is not provisioned: stopping the EC2 instance erases it.

## Provision

`host.sh` targets x86-64 Ubuntu 22.04 with the AWS kernel specified in the script already booted.
Run each stage explicitly. A failed assertion or download stops that stage; existing users and
toolchain directories are errors. Inspect the failure before taking another action.

`host-packages.lock` pins the host packages and the additional dependencies selected by APT.
The verification directory records the complete installed-package inventory. Package availability
in the configured Ubuntu and NVIDIA repositories is required; unavailable versions fail installation.

Stage the tracked `deploy/` files on the target. The user stage can
run from that staging copy before the service checkout exists:

```sh
export SNR_SERVICE_USER=snr SNR_SERVICE_UID=1002 SNR_SERVICE_GID=1003 SNR_ROOT=/srv/snr
sudo -E bash deploy/host.sh user
sudo git clone --branch main --single-branch https://github.com/brookaylneh/AeroPilot.git "$SNR_ROOT"
sudo chown -R "$SNR_SERVICE_USER:$SNR_SERVICE_USER" "$SNR_ROOT"
```

Verify the cloned repository with `git fsck --full --strict` before the remaining stages.

```sh
sudo -E bash "$SNR_ROOT/deploy/host.sh" packages
sudo -E -u "$SNR_SERVICE_USER" bash "$SNR_ROOT/deploy/host.sh" toolchains
sudo systemctl reboot
```

After reconnecting, run verification with a new output directory:

```sh
sudo -E -u "$SNR_SERVICE_USER" bash "$SNR_ROOT/deploy/host.sh" verify \
  "$SNR_ROOT/.local/evidence/host/<new-run>"
```

The package stage holds only the kernel, NVIDIA driver and container-toolkit packages in the lock.
Docker, containerd, runc and system Python remain eligible for security updates. It disables unattended reboots and unattended kernel removal while leaving
other security updates enabled. The prebuilt `-aws` open NVIDIA module supplies the driver;
DKMS is not used. The NVIDIA persistence daemon starts at boot with persistence enabled.
Docker uses the NVIDIA runtime configured by `nvidia-ctk`.

## Toolchains

All downloaded tools and archives live under `$SNR_ROOT/.local/toolchains/`; nothing is installed
in `/opt`. Versions are defined in `host.sh`; archive hashes are in `toolchains.sha256`.
The installer records executable hashes in `.local/toolchains/binaries.sha256`.

| Entry under `.local/toolchains/bin/` | Source and use                                                 |
| ------------------------------------ | -------------------------------------------------------------- |
| `node`, `npm`, `npx`                 | Official Node Linux archive; web build and services            |
| `uv`                                 | Astral release archive; Python environment tooling             |
| `python3.12`                         | Astral standalone CPython; model deployment environment        |
| `python3.10`                         | Link to the system interpreter; Cosys environment              |
| `vulkaninfo`                         | Binary from the LunarG SDK; current Vulkan feature enumeration |

Use `export PATH="$SNR_ROOT/.local/toolchains/bin:$PATH"` in an interactive shell.
Service units must use explicit tool paths. Python environment creation and Unreal Engine transfer
are separate operations. The engine's destination is
`.local/toolchains/UnrealEngine-5.8.2`.

The Python archive is downloaded and checked first. uv installs it from a local file mirror,
with the CPython build pinned and network retries disabled. It does not choose another interpreter.
Only `vulkaninfo` is extracted into `vulkan-sdk-1.4.363.0/`; it uses the system loader and NVIDIA driver.
Ubuntu's older diagnostic binary does not enumerate the mesh-shader feature structure.

## Verification

Verification asserts the running kernel and held-package pins, user and groups, package holds,
disabled automatic reboot, boot services, a changed boot ID, the open NVIDIA module, and
persistence mode. It records GPU power limits, clock limits, ECC, CPU, memory and disk information.

Vulkan enumeration selects NVIDIA's ICD explicitly and requires image int64 atomics, mesh and
task shaders, ray queries, and acceleration structures. This is a host capability check; Unreal's
`VP_UE_Vulkan_SM6` profile and a packaged SM6 build still need their own acceptance.

The codec check encodes 120 synthetic 1080p frames through H.264 NVENC, decodes them through
CUDA/CUVID, and checks the encoded frame count. It establishes codec availability, not sustained
simulation performance. A digest-pinned Ubuntu container must also expose the GPU to the service
user with `docker run --gpus all`. Raw verification output stays in `.local/evidence/host/`;
accepted host measurements belong in `mission_planner/measurements/`.

## Engine build

The engine SDK in `.local/toolchains/UnrealEngine-5.8.2` keeps its intermediates and DDC.
Project and project-plugin build outputs are regenerated. Build with explicit host resources:

```sh
export UE_ROOT="$PWD/.local/toolchains/UnrealEngine-5.8.2"
export SNR_CPU_LIMIT=12 SNR_MEMORY_LIMIT=96g
bash world/unreal/scripts/unreal-container.sh build
```

Package and stage with the manifests and pipeline-cache mode described in
[world/unreal/README.md](../world/unreal/README.md).

The build CPU limit is a packaging setting; it does not select the live simulation profile.

## Installed services

`runtime.env.example` is the single runtime configuration: copy it to `.local/runtime.env` before
building or installing. Its values contain no secrets. Keep the copied file in sync when changing a
profile. The installer renders the repository path and service identity into the tracked systemd units.

```sh
cp deploy/runtime.env.example .local/runtime.env
export PATH="$PWD/.local/toolchains/bin:$PATH"
set -a
. .local/runtime.env
set +a
bash deploy/fetch-caddy.sh
bash deploy/fetch-browser.sh
bash world/unreal/scripts/stage-live-release.sh .local/unreal/<new-build>/Linux
node --env-file=.local/runtime.env --import tsx web/slots.ts write
deploy_source=$PWD
deploy_release="$PWD/.local/staging/release-$(git rev-parse --short=12 HEAD)"
git worktree add --detach "$deploy_release" HEAD
mkdir "$deploy_release/.local"
for entry in .env .secrets .venv-cosys node_modules; do
  ln -s "$deploy_source/$entry" "$deploy_release/$entry"
done
for entry in runtime.env toolchains unreal slots vendor world data evidence run build; do
  ln -s "$deploy_source/.local/$entry" "$deploy_release/.local/$entry"
done
cd "$deploy_release"
npm run build
sudo bash deploy/install-services.sh
sudo systemctl start snr-turn snr-vision@0 snr-signalling@0 snr-unreal@0
timeout 120 bash -c 'until ss -H -ltn sport = :41451 | rg -q "127.0.0.1:41451"; do sleep 1; done'
sudo systemctl start snr-mission@0 snr-web
# Start Caddy after DNS resolves here and the security group permits TCP 80 and 443.
sudo systemctl start snr-caddy
```

## Evidence registry conversion

Releases before `f1a4c83` wrote each mission's evidence registry as `evidence/assets.json`; the web
server and mission service now read and append `evidence/assets.txt`. Convert once, before the first
release that serves `assets.txt` starts, with every mission service stopped so no old writer appends
to a registry while it is converted:

```sh
sudo systemctl stop 'snr-mission@*'
.venv-cosys/bin/python -m mission_planner.gcs.migrate_registry .local/data/missions .local/slots/*/missions
```

The tool checks that every registered asset exists, writes `assets.txt` beside each `assets.json`,
refuses to overwrite one that already exists and leaves the originals in place. It is not part of the
installation recipe above; running it again after the conversion fails on the existing files. Record
one evidence URL from the oldest and the newest mission before stopping the services and request
both after the new web service starts.

## Environment slots

Each environment contains one Unreal world, its signalling server, mission service
with four autopilots, and local SAM container. Browser sessions can share its mission;
one viewer at a time controls its video and Studio. `SNR_SLOTS` sets how many environments run;
`SNR_SLOT_HOSTS` lists one IPv4 host per slot. `web/slots.ts` derives every slot value from the
base file and asserts that no listener collides; `write` stores each slot's overrides in
`.local/slots/<k>/runtime.env` with its Cosys settings, and the `@<k>` units read that file after
`.local/runtime.env`. Command-line tools for a slot load both files in that order.

| Value                      | Slot 0                                | Slot 1                                |
| -------------------------- | ------------------------------------- | ------------------------------------- |
| Player / streamer          | `8080` / `8888`                       | `8081` / `8889`                       |
| Mission / Cosys RPC        | `8765` / `41451`                      | `8766` / `41452`                      |
| SITL instances             | 0–3                                   | 10–13                                 |
| MAVLink / RC UDP           | `14600–14603` / `5501–5531`           | `14610–14613` / `5601–5631`           |
| AirSim UDP                 | `9002–9033`                           | `9102–9133`                           |
| Unreal WebRTC UDP          | `49160–49200`                         | `49210–49250`                         |
| Containers                 | `snr-unreal-slot0`, `snr-sam31-slot0` | `snr-unreal-slot1`, `snr-sam31-slot1` |
| Vision socket, streamer ID | `.local/run/vision-0.sock`, `Slot0`   | `vision-1.sock`, `Slot1`              |
| CPU limit                  | `SNR_CPU_LIMIT / SNR_SLOTS`           | same                                  |

A third slot collides with the TURN relay range and fails at startup until that range moves.
The current installed services support loopback slots on this host only; remote slot hosts
are rejected. Each slot also has separate `scenarios/`, `missions/`, `drone-data/` and fences.
Saved scenarios are copied explicitly during migration, never shared writable between worlds.
Each additional slot starts a full renderer and a separate SAM instance with its own
16-billion-byte process budget. The slot count is a capacity choice, not a throughput claim;
measure rendering, inference and GPU memory with every configured slot running.
Before reducing `SNR_SLOTS`, stop the removed environments with the coupled shutdown.
The installer rejects active removed slots and disables their boot units once stopped.
The web server gives every browser an anonymous `snr_session` cookie and joins it to the first
ready mission, including an active mission already watched by other browsers; assignments survive web restarts in
`.local/slots/assignments.json`. See [web](../web/README.md) for release rules and explicit
fault recovery. The mission unit waits up to 120 seconds for its own Cosys listener before
starting SITL, including at host boot. A timeout fails the unit without restarting it.

The browser build also requires the packaged CSRT artifacts. Run
`bash mission_planner/gcs/build-csrt.sh` once before the first `npm run build`;
the build and verified source versions are documented in
[live camera overlays](../mission_planner/gcs/README.md#live-camera-and-overlays).
The installer checks native CSRT in the deployment checkout's `.venv-cosys` before
changing units. Install `requirements.txt` in that environment too.

Evidence registries written before 2026-11-04 are JSON arrays; `migrate_registry` converts each
mission's registry once into the line format the web server reads and leaves the original beside
it. Fetch one registered evidence URL before and after the switch to prove access. The browser
bundle, web server and mission service share one protocol (the public map arrives with the
mission history), so install and restart them from the same release and reload open browsers.

Installed services use a clean detached release checkout with its own browser bundle.
The installer rejects an attached branch, uncommitted source changes, or a bundle shared
through a `.local` symlink. Spawned model workers import Python source after the service
starts; an editable deployment can mix an old mission process with unfinished new code.
Build and verify the selected commit, then install its release checkout as above.
Keep its environment, data, toolchain and dependency links available while installed;
leave release source and `.local/dist` unchanged. The installer verifies the terrain
heightfield checksum and shape, requires the TURN configuration, and starts the one-worker
and four-worker model pools without model requests to verify the host bootstrap.
Check
`systemctl show snr-web snr-mission@0 -p WorkingDirectory` before a deployment.

Build the immutable SAM device image and stage its verified checkpoint, warmup
fixture and compile-cache directory before starting `snr-mission@<k>`. Set all six
`SNR_SAM_*` values in `.local/runtime.env`; the example image ID identifies the
qualified build and the GPU UUID must identify the intended device. See the
[device package](../mission_planner/agents/services/README.md#local-sam-device-package).
Its systemd dependency waits for vision readiness; a later vision failure must not stop
the mission owner or its flight override controls. No additional TCP port is allocated.

Acquisition scripts require new destinations; do not rerun them over installed toolchains.
Units start at boot, fail visibly and do not restart automatically after errors.
`journalctl -u snr-<service>` records their output. Restart only the affected installed unit after a fix.
For the coupled simulator, follow the [world-first shutdown](../world/cosys/README.md)
and wait for Unreal's Cosys listener before starting the mission service. Starting the
autopilots before Unreal is ready exhausts their first sensor-message timeout.
The rendering and encoding profile is documented in [streaming](../world/unreal/streaming.md);
its startup values are defined in `runtime.env.example`.

| Route or endpoint                                 | Purpose                                                      | Listener / upstream                                              |
| ------------------------------------------------- | ------------------------------------------------------------ | ---------------------------------------------------------------- |
| `$SNR_ORIGIN/`                                    | Mission Control: map, world exploration and scenario tabs    | Caddy, 443 TCP (80 redirects), then web `127.0.0.1:3001`         |
| `/home`, `/studio`, `/missionplanner`, `/mission` | Redirect to `/`                                              | web, `127.0.0.1:3001`                                            |
| `/api/slots`                                      | This session's environment, or how many are in use           | web, slot broker                                                 |
| `/api/mission`                                    | Mission WebSocket                                            | web origin and session check, then the slot's mission port       |
| `/api/evidence/<mission>/<asset>`                 | Registered immutable original JPEGs and matching mask PNGs   | same web origin, mission-owned registry                          |
| `/api/unreal/status`                              | The session's renderer readiness and occupancy               | web, slot signalling health                                      |
| `/api/stream`                                     | Pixel Streaming player WebSocket                             | web origin and session check, then the slot's player port        |
| `/healthz`                                        | Shared units, every slot's units, mission, renderer and TURN | web; 503 if any check fails or a slot is faulted; detail on host |
| Internal streamer, Cosys RPC, MAVLink, SITL       | Per slot                                                     | see [environment slots](#environment-slots)                      |
| TURN                                              | Browser relay control                                        | `$SNR_PRIVATE_IP:3478` TCP/UDP, public IP translation            |
| TURN relay                                        | Relayed media                                                | `$SNR_PRIVATE_IP:49300–49350` UDP                                |
| Unreal WebRTC                                     | Local media endpoint reached by TURN, per slot               | `49160–49250` UDP; no public ingress required with forced relay  |
| Development                                       | One server, reached by SSH tunnel                            | `127.0.0.1:5174`                                                 |

Caddy proxies to the web server so production and development share the same-origin WebSocket
checks. The root redirects explicitly; unknown paths return 404. TURN and its media endpoints are the network-facing exception to the loopback service rule.
Signalling supplies `iceTransportPolicy: relay` to browsers. Unreal advertises its local candidates;
Epic's frontend `ForceTURN` flag is unused because it also rejects those remote candidates.

The Unreal world advances only while the four disarmed autopilots return motor outputs, so start
`snr-mission@<k>` after `snr-unreal@<k>` and it starts the fleet immediately. Its health state is `starting`
until the fleet is ready, then `idle` until general planner entry or Studio's Launch request
opens a session. A general session has no rescue case; a Studio session retains its saved
briefing. Reload and Home navigation preserve the session. Studio requires an explicit
end-session action with every aircraft landed and disarmed before resetting native rendering
to character selection; it never initiates a return flight. The coupled fleet remains running.
A later launch can select a new case. No model inference or autonomous flight starts merely
by opening the planner or launching the briefing. See [web entry acceptance](../web/README.md).
General and rescue sessions use one versioned flight plan; the mission connection delivers the public
map once as the history's `landmarks`. Each `snr-vision@<k>` launches one local SAM 3.1 container; Glimmer
and the standalone SAM calibration service remain on Modal. Four independent drone
sessions share one model within a 16-billion-byte GPU budget and expose only
`SNR_VISION_SOCKET`. See [vision installation and protocol](../mission_planner/agents/services/README.md).

A vision/client/schema migration also needs the mission and web processes to load
the new code. Mission shutdown terminates its four SITL controllers, so a live
cutover follows the world-first shutdown linked above. Stage the complete image,
web build, runtime configuration and rendered units before that coordinated stop.
Do not replace an active mission's vision worker independently. An idle fleet
status does not remove the simulator's coupled shutdown requirement.

Run development with `npm run dev`. Vite middleware and API/WebSocket handlers share the single fixed
listener; reload the browser after changes. Tunnel it with:

```sh
ssh -L 5174:127.0.0.1:5174 "${SNR_SSH_TARGET:?Set SNR_SSH_TARGET to your admin-user@public-ip}"
```

Acceptance uses the installed public origin. Do not allocate temporary listening ports.

```sh
node --env-file=.local/runtime.env deploy/accept-browser.mjs \
  "$SNR_ORIGIN" ".local/evidence/services/browser-$(date -u +%Y%m%dT%H%M%SZ)"
```

The harness creates a scenario, measures one minute of navigation and selected ICE candidates,
then launches its briefing into Mission Control. It records screenshots, decoded FPS, drops,
freezes and bitrate. Relay verification checks the browser policy, TURN transport and configured relay port range.
Candidate addresses are recorded when Chromium exposes them. ICE can label a relayed candidate `prflx` after discovering its private
address, so the candidate type alone is insufficient. The harness requires an idle mission service
and complete character assets.
The parity browser runs on AWS and therefore does not establish remote Wi-Fi performance.

The packaged module permanently disables TraceLog through `UE::Trace::Panic()` before the first frame.
UE 5.8 exposes no runtime flag for its 1985 control listener; the permanent mute prevents that listener
and subsequent trace updates. The engine logs its standard TraceLog-disabled diagnostic. CSV profiling
and the native frame/GPU telemetry remain available. Editor tracing is unchanged.

Renderer readiness requires a connected streamer and fresh native `scenarioReady` telemetry. A connected
signalling socket alone is insufficient. Invalid scenario initialization terminates the renderer visibly.
Before the first native telemetry file arrives, readiness remains `starting`.

The `Live` link selects a staged release: a package cooked from the tracked world manifest with
the eleven approved Rocketbox characters and its bundled pipeline cache.

## Security group

Public ingress is HTTP on TCP 80 and HTTPS on TCP 443 to Caddy, TURN on 3478 TCP/UDP, and
the TURN relay range 49300–49350 UDP. The direct WebRTC range 49160–49250 UDP needs no
public ingress once a browser measurement verifies a selected relay candidate. Keep SSH restricted to the
operator's source address. The cloud console commands with the account's group identifiers
are an operator runbook kept outside the repository.

## Sources

- [WebRTC peer-reflexive candidate discovery](https://webrtc.googlesource.com/src/+/refs/heads/main/p2p/base/connection.cc#1767)
- [Caddy automatic HTTPS controls](https://caddyserver.com/docs/automatic-https)
- [Caddy reverse proxy and WebSockets](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- [Caddy systemd service guidance](https://caddyserver.com/docs/running)
- [AWS security-group ingress CLI](https://docs.aws.amazon.com/cli/latest/reference/ec2/authorize-security-group-ingress.html)

- [Ubuntu server NVIDIA installation](https://ubuntu.com/server/docs/nvidia-drivers-installation/)
- [NVIDIA Container Toolkit installation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.17.8/install-guide.html)
- [Node release checksums](https://nodejs.org/dist/v22.23.4/SHASUMS256.txt)
- [Astral uv release](https://github.com/astral-sh/uv/releases/tag/0.13.6)
- [uv's pinned Python download metadata](https://github.com/astral-sh/uv/blob/0.13.6/crates/uv-python-managed/download-metadata.json)
- [uv environment settings](https://docs.astral.sh/uv/reference/environment/)
- [LunarG SDK installation](https://vulkan.lunarg.com/doc/view/latest/linux/getting_started.html)
- [NVIDIA codec engine and format matrix](https://developer.nvidia.com/video-encode-decode-support-matrix)

## GPU containers

GPU containers explicitly declare the NVIDIA device nodes in `unreal-container.sh`,
in addition to requesting the NVIDIA runtime and driver libraries. This preserves
GPU access when runc updates the cgroup. The NVIDIA hook alone does not preserve access
when CPU limits change on this host.
Set CPU limits in `runtime.env` before recreating the service container.
See [NVIDIA's container-update diagnosis](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/troubleshooting.html#containers-losing-access-to-gpus-with-error-failed-to-initialize-nvml-unknown-error).
