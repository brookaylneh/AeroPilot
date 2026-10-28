# Transfer

Target paths are relative to /home/brook_avionuas/snr on AWS. Sizes are regular-file payload bytes,
excluding directory entries, allocation and transport overhead. Every copied file has a SHA-256 record
in .local/evidence/transfer/20261028/. deploy/transfer.mjs verifies file contents, executable flags and
symbolic links, rejects escaping links, and fails if a source file changes during hashing.

| Source                                                                        | Target                                     |                Bytes | Method                                  | Verification                                             |
| ----------------------------------------------------------------------------- | ------------------------------------------ | -------------------: | --------------------------------------- | -------------------------------------------------------- |
| /srv/snr main                                                                 | Git repository                             |     7,427,191 bundle | git bundle and main-only clone          | All 30 commits retained; original main hash; strict fsck |
| /srv/snr/mission_planner/agents/perception/{README.md,run_calibration.py} WIP | .local/staging/calibration-wip.patch       |                5,437 | Git diff                                | git apply --check; unapplied                             |
| /srv/snr/world/unreal/ScenarioStudio/Content                                  | world/unreal/ScenarioStudio/Content        |        3,045,592,496 | rsync over SSH                          | 1,768 files match                                        |
| Cosys 5.8-v3.5.0 release plus tracked patch                                   | world/unreal/ScenarioStudio/Plugins/AirSim |  342,489,217 rebuilt | Tracked fetcher                         | 2,766 source/content files match                         |
| /srv/snr/world/unreal/ScenarioStudio/Plugins/PixelStreaming2                  | Same relative path                         |          681,313,058 | rsync over SSH                          | 378 files match                                          |
| /srv/snr/world/unreal/ScenarioStudio/Plugins/LibVpxCodecs                     | Same relative path                         |              127,900 | rsync over SSH                          | 23 files match                                           |
| /srv/snr/.local/vendor                                                        | .local/vendor                              |          358,361,746 | rsync over SSH, then dependency rebuild | 30,712 files and 74 links verified                       |
| /srv/snr/.local/data/unreal-scenarios                                         | .local/data/unreal-scenarios               |               56,519 | rsync over SSH                          | 56 files match                                           |
| /srv/snr/.local/data/four-drones                                              | .local/data/four-drones                    | 196,548,464 retained | rsync over SSH                          | 26 persistent files match                                |
| /srv/snr/.local/data/calibration                                              | .local/data/calibration                    |        4,489,731,056 | rsync over SSH                          | 1,111 files match                                        |
| /srv/snr/.local/evidence                                                      | .local/evidence                            |            3,793,710 | rsync over SSH, verified before rename  | 104 files match; relative paths retained                 |
| /home/brook_avionuas/snr-gcs/.local/evidence/gcs                              | .local/evidence/gcs                        |          236,931,593 | rsync over SSH                          | 755 files match                                          |
| /opt/snr/UnrealEngine/5.8.2                                                   | .local/toolchains/UnrealEngine-5.8.2       |       90,806,750,582 | rsync over SSH                          | 300,659 files match                                      |
| /srv/snr/{.env,.secrets/turn.json,.secrets/turnserver.conf}                   | Same relative paths                        |  937 before rotation | scp, then install mode 600              | Staged byte comparisons; credential rotation asserted    |

Total copied from GCP: **99,826,648,831 bytes** (99.826648831 GB;
92.9708116045 GiB). This includes 8,142 bytes of transient four-drone state quarantined after the
initial live-file mismatch. Retained source payload is 99,826,640,689 bytes.
AirSim adds 342,489,217 bytes rebuilt from upstream, giving
100,169,129,906 bytes of retained source and rebuilt plugin payload.

These totals count regular-file contents, the transmitted Git bundle, the calibration patch and
pre-rotation secret files. They exclude network overhead, manifests, symlink strings, upstream
dependency downloads and generated build outputs. The directory manifests cover
338,358 regular files and 74 symlinks.

## Plugin provenance

AirSim comes from the archive pinned in world/unreal/dependencies.lock.json. The source installation
contains one local SimHUD.cpp change. world/cosys/airsim-ue58.patch preserves it; the fetcher applies
the patch after archive verification. The resulting source/content files match the source installation.

PixelStreaming2 and LibVpxCodecs have no tracked fetcher. They derive from the licensed UE 5.8.2 engine
plugins, with two modified files in each: PixelCapture header access and VP8 core-budget handling in
PixelStreaming2; VP8 configuration propagation and thread logging in LibVpxCodecs. The copied files
preserve these changes. epic-plugin-engine-differences.txt records the recursive engine comparison.

## Vendor provenance

None of the 54 raw Poly Haven files in the wilderness/equipment asset locks exists in the source vendor
tree. They were not transferred. Imported project content is covered by the Content manifest.

All three Open Meadow files match deterministic regeneration with the tracked generators and pinned
NumPy, SciPy and Pillow versions. Their reproduced hashes are in world/unreal/open-meadow-assets.lock.json;
the heightfield also matches world/cosys/world.json.

Pixel Streaming infrastructure matches the Epic commit in dependencies.lock.json. Reinstalling package-lock
dependencies and rebuilding with the tracked setup script reproduced every file except Git's mutable index.
The materials CSV matches world/cosys/materials.csv. The Epic configuration file that disables unattended
bug reporting was copied with a matching checksum. Repository, dependency and configuration checks apply
to these files; the Poly Haven asset locks do not cover them.

## Exclusions and live data

Project and project-plugin Binaries, Intermediate, Saved and DerivedDataCache were excluded from rsync.
No source .local/dist, .local/unreal release, staging directory, failed calibration directory, mission
runtime directory, virtual environment, Python bytecode cache, Ruff cache, Isaac Sim image or GCS worktree
was copied. The engine SDK retains its DDC, intermediate files and other installed components.

The initial four-drone check failed because the live source rewrote runtime.json between hashing and copying.
The 26 persistent files matched. The runtime record, controller lock and console responses were moved out
of the active AWS data directory into .local/staging/four-drones-transient/. The initial manifest remains
as evidence. AWS must generate its own runtime state.

## Rebuilt dependencies and credentials

Both Python environments were recreated from their requirements files. npm dependencies and the web bundle
were rebuilt. Exact installed Python and container package inventories are retained with the evidence.
ArduPilot was rebuilt from its recorded Copter commit with mission_planner/gcs/build-sitl.sh;
mission_planner/measurements/aws-sitl-build.json records the result.

Both Unreal images were rebuilt from the tracked Dockerfiles and passed GPU, Vulkan and video-library checks.
deploy/images.lock.json records their digests and the coturn 4.18.0-r0 digest. No source Docker image was copied.

TURN uses public IP 35.81.229.78 and listening/relay IP 172.31.61.189. The configure-turn module's explicit
--rotate action generated a new shared credential. The three secret files are mode 600. Credential values
and credential hashes are excluded from reports.

## Engine SDK

The SDK is UE 5.8.2, changelist 56702186. All installed components were retained, excluding Python
bytecode. The source manifest and destination verification cover the full 90,806,750,582 bytes.
These are measured component sizes; retaining them does not establish that every cache is essential.

| Component               |   Files |          Bytes |
| ----------------------- | ------: | -------------: |
| Engine/Binaries         |  20,223 | 37,070,580,773 |
| Engine/Plugins          | 133,727 | 34,616,792,658 |
| Engine/Intermediate     |  21,676 |  5,377,913,040 |
| Engine/DerivedDataCache |       1 |  1,135,055,834 |
| Engine/Source           |  64,664 |  6,188,050,404 |

The remaining components are itemized in mission_planner/measurements/aws-transfer.json.

## Packaging verification

Engine destination verification passed. The editor build succeeded in 217.02 seconds.
The current SF_VULKAN_SM5 package cooked all 2,547 packages; BuildCookRun succeeded in 864.33 seconds
(730.37 seconds cooking). The generated Linux package contains 49 regular files totaling
2,058,641,649 bytes, with a complete SHA-256 manifest. No source project build output or release was used.

The cold cook spent time reducing the final fir-tree mesh. The saved 10-second CPU profile attributes
75.28% of sampled cycles inclusively to ReduceLOD. A 12-second process trace also ran during this build;
its elapsed duration is not a clean performance benchmark. No mesh or renderer setting was changed.

The cook retains AirSim engine-version and vehicle-animation warnings, an uncooked MH_Adult authoring-asset
warning, and MaterialShaderMap non-deterministic cache-record messages. The assembled runtime BP_Adult,
body mesh and standing animation are listed in the package staging manifest. There were no material
compile failures, invalid ShaderMaps or LogCook errors matched by the packaging check.

The browser/runtime acceptance belongs to the service deployment phase. This result proves compilation,
cooking and staging, and does not establish rendering or browser parity.

Accepted measurements and raw-log references are in mission_planner/measurements/aws-transfer.json.
