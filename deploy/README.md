# AWS host

The project runs as `brook_avionuas` (uid 1002, gid 1003), with Docker and video group membership,
at `/home/brook_avionuas/snr`. Keep application data and toolchains on the persistent root volume.
The NVMe instance store is not provisioned: stopping the EC2 instance erases it.

## Provision

`host.sh` targets x86-64 Ubuntu 22.04 with the AWS kernel specified in the script already booted.
Run each stage explicitly. A failed assertion or download stops that stage; existing users and
toolchain directories are errors. Inspect the failure before taking another action.

`host-packages.lock` pins the host packages and the additional dependencies selected by APT.
The verification directory records the complete installed-package inventory. Package availability
in the configured Ubuntu and NVIDIA repositories is required; unavailable versions fail installation.

Stage the tracked `deploy/` files and a main-only Git bundle on the target. The user stage can
run from the staging copy before the repository exists:

```sh
sudo bash deploy/host.sh user
sudo git clone --branch main --single-branch /path/to/main.bundle /home/brook_avionuas/snr
sudo chown -R brook_avionuas:brook_avionuas /home/brook_avionuas/snr
```

Place the staged `deploy/` files in the checkout before the remaining stages. Verify the bundle with `git bundle verify` and the cloned history with `git fsck --full --strict`.
Delete the temporary bundle and remove its local Git remote after verification.

```sh
sudo bash /home/brook_avionuas/snr/deploy/host.sh packages
sudo -u brook_avionuas bash /home/brook_avionuas/snr/deploy/host.sh toolchains
sudo systemctl reboot
```

After reconnecting, run verification with a new output directory:

```sh
sudo -u brook_avionuas bash /home/brook_avionuas/snr/deploy/host.sh verify \
  /home/brook_avionuas/snr/.local/evidence/host/<new-run>
```

The package stage holds only the kernel, NVIDIA driver and container-toolkit packages in the lock.
Docker, containerd, runc and system Python remain eligible for security updates. It disables unattended reboots and unattended kernel removal while leaving
other security updates enabled. The prebuilt `-aws` open NVIDIA module supplies the driver;
DKMS is not used. The NVIDIA persistence daemon starts at boot with persistence enabled.
Docker uses the NVIDIA runtime configured by `nvidia-ctk`.

## Toolchains

All downloaded tools and archives live under `~/snr/.local/toolchains/`; nothing is installed
in `/opt`. Versions are defined in `host.sh`; archive hashes are in `toolchains.sha256`.
The installer records executable hashes in `.local/toolchains/binaries.sha256`.

| Entry under `.local/toolchains/bin/` | Source and use                                                 |
| ------------------------------------ | -------------------------------------------------------------- |
| `node`, `npm`, `npx`                 | Official Node Linux archive; web build and services            |
| `uv`                                 | Astral release archive; Python environment tooling             |
| `python3.12`                         | Astral standalone CPython; model deployment environment        |
| `python3.10`                         | Link to the system interpreter; Cosys environment              |
| `vulkaninfo`                         | Binary from the LunarG SDK; current Vulkan feature enumeration |

Use `export PATH="$HOME/snr/.local/toolchains/bin:$PATH"` in an interactive shell.
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
npm run build
bash world/unreal/scripts/stage-live-release.sh .local/unreal/<new-build>/Linux
sudo bash deploy/install-services.sh
sudo systemctl start snr-turn snr-signalling snr-unreal
timeout 120 bash -c 'until ss -H -ltn sport = :41451 | rg -q "127.0.0.1:41451"; do sleep 1; done'
sudo systemctl start snr-mission snr-web
# Start Caddy after the security group permits HTTP on TCP 80.
sudo systemctl start snr-caddy
```

Build the immutable SAM device image and stage its verified checkpoint, warmup
fixture and compile-cache directory before starting `snr-mission`. Set all six
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

| Route or endpoint                  | Purpose                                                         | Listener / upstream                                             |
| ---------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------- |
| `http://35.81.229.78/`             | Redirect to `/home`                                             | Caddy, 80 TCP                                                   |
| `/home`                            | World readiness and planner/scenario entry                      | web, `127.0.0.1:3001`                                           |
| `/studio`                          | Scenario Creator                                                | web, `127.0.0.1:3001`                                           |
| `/missionplanner`                  | General mission entry or active-session resume                  | web, `127.0.0.1:3001`                                           |
| `/missionplanner?case=<uuid>`      | Mission Control with a launched rescue scenario                 | web, `127.0.0.1:3001`                                           |
| `/mission`                         | Compatibility redirect, preserving the query                    | web, `127.0.0.1:3001`                                           |
| `/api/mission`                     | Mission WebSocket                                               | web origin check, then `127.0.0.1:8765`                         |
| `/api/evidence/<mission>/<asset>`  | Registered immutable original JPEGs and matching mask PNGs      | same web origin, mission-owned registry                         |
| `/api/unreal/status`               | Renderer readiness and occupancy                                | web, signalling health                                          |
| `/api/stream`                      | Pixel Streaming player WebSocket                                | web origin check, then `127.0.0.1:8080`                         |
| `/healthz`                         | All seven units plus mission, renderer and TURN protocol health | web; 503 if any check fails                                     |
| Internal streamer                  | Unreal to signalling                                            | `127.0.0.1:8888`                                                |
| Internal Cosys RPC                 | Native simulation control                                       | `127.0.0.1:41451`                                               |
| Internal MAVLink                   | Four SITL telemetry inputs                                      | `127.0.0.1:14600–14603` UDP                                     |
| Internal RC input                  | Four SITL inputs                                                | `127.0.0.1:5501,5511,5521,5531` UDP                             |
| Internal AirSim sensor and control | Cosys to SITL, SITL instances 0–3                               | `127.0.0.1:9003–9033` and `9002–9032` UDP, step 10              |
| TURN                               | Browser relay control                                           | `172.31.61.189:3478` TCP/UDP, public EIP translation            |
| TURN relay                         | Relayed media                                                   | `172.31.61.189:49300–49350` UDP                                 |
| Unreal WebRTC                      | Local media endpoint reached by TURN                            | `49160–49200` UDP; no public ingress required with forced relay |
| Development                        | One server, reached by SSH tunnel                               | `127.0.0.1:5174`                                                |

Caddy proxies to the web server so production and development share the same-origin WebSocket
checks. The root redirects explicitly; unknown paths return 404. TURN and its media endpoints are the network-facing exception to the loopback service rule.
Caddy serves the public IP over HTTP on TCP 80. Automatic HTTPS and the admin endpoint are disabled.
WebSockets use `ws://` on the same origin; no DNS name or certificate is required.
Signalling supplies `iceTransportPolicy: relay` to browsers. Unreal advertises its local candidates;
Epic's frontend `ForceTURN` flag is unused because it also rejects those remote candidates.

The Unreal world advances only while the four disarmed autopilots return motor outputs, so start
`snr-mission` after `snr-unreal` and it starts the fleet immediately. Its health state is `starting`
until the fleet is ready, then `idle` until general planner entry or Studio's Launch request
opens a session. A general session has no rescue case; a Studio session retains its saved
briefing. Reload and Home navigation preserve the session. Studio requires an explicit
end-session action with every aircraft landed and disarmed before resetting native rendering
to character selection; it never initiates a return flight. The coupled fleet remains running.
A later launch can select a new case. No model inference or autonomous flight starts merely
by opening the planner or launching the briefing. See [web entry acceptance](../web/README.md).
General and rescue sessions use one versioned flight plan; the mission state exposes the public map
as `landmarks`. `snr-vision.service` launches one local SAM 3.1 container; Glimmer
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
ssh -i ~/.ssh/snr-oregon -L 5174:127.0.0.1:5174 ubuntu@35.81.229.78
```

Acceptance uses the installed public origin. Do not allocate temporary listening ports.

```sh
node --env-file=.local/runtime.env deploy/accept-browser.mjs \
  http://35.81.229.78 .local/evidence/services/<new-browser-run>
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

The operator runs these commands with AWS credentials. Do not run an authorize operation again after
success: AWS rejects duplicate rules. Keep SSH restricted to the existing source address.

```sh
aws ec2 authorize-security-group-ingress --region us-west-2 --group-id sg-007dd04266b7ed321 \
  --ip-permissions '[{"IpProtocol":"tcp","FromPort":80,"ToPort":80,"IpRanges":[{"CidrIp":"0.0.0.0/0","Description":"SNR public HTTP origin"}]}]'
aws ec2 revoke-security-group-ingress --region us-west-2 --group-id sg-007dd04266b7ed321 \
  --protocol tcp --port 5173 --cidr 0.0.0.0/0
```

TCP 443 is unused by this HTTP configuration. Remove its existing ingress rule with:

```sh
aws ec2 revoke-security-group-ingress --region us-west-2 --group-id sg-007dd04266b7ed321 \
  --protocol tcp --port 443 --cidr 0.0.0.0/0
```

Keep TURN 3478 TCP/UDP and 49300–49350 UDP open. After a browser measurement verifies a selected relay
candidate, remove direct WebRTC ingress:

```sh
aws ec2 revoke-security-group-ingress --region us-west-2 --group-id sg-007dd04266b7ed321 \
  --protocol udp --port 49160-49200 --cidr 0.0.0.0/0
```

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
