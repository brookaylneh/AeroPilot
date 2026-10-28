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
sudo -u brook_avionuas git -C /home/brook_avionuas/snr switch -c aws-migration
```

Place the staged `deploy/` files in the checkout before the remaining stages. Preserve the bundle
under `.local/staging/main.bundle` and verify it from inside the checkout with
`git bundle verify .local/staging/main.bundle` and `git fsck --full --strict`.

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

The package stage holds the kernel, NVIDIA driver and toolkit, container runtime, and Python
packages in the lock. It disables unattended reboots and unattended kernel removal while leaving
other security updates enabled. The prebuilt `-aws` open NVIDIA module supplies the driver;
DKMS is not used. The NVIDIA persistence daemon starts at boot with persistence enabled.
Docker uses the NVIDIA runtime configured by `nvidia-ctk`.

## Toolchains

All downloaded tools and archives live under `~/snr/.local/toolchains/`; nothing is installed
in `/opt`. Versions are defined in `host.sh`; archive hashes are in `toolchains.sha256`.
The installer records executable hashes in `.local/toolchains/binaries.sha256`.

| Entry under `.local/toolchains/bin/` | Source and use |
| --- | --- |
| `node`, `npm`, `npx` | Official Node Linux archive; web build and services |
| `uv` | Astral release archive; Python environment tooling |
| `python3.12` | Astral standalone CPython; fal environment |
| `python3.10` | Link to the pinned system interpreter; Cosys environment |
| `vulkaninfo` | Binary from the LunarG SDK; current Vulkan feature enumeration |

Use `export PATH="$HOME/snr/.local/toolchains/bin:$PATH"` in an interactive shell.
Service units must use explicit tool paths. Python environment creation and Unreal Engine transfer
are separate operations. The engine's destination is
`.local/toolchains/UnrealEngine-5.8.2`.

The Python archive is downloaded and checked first. uv installs it from a local file mirror,
with the CPython build pinned and network retries disabled. It does not choose another interpreter.
Only `vulkaninfo` is extracted from the SDK; it uses the system loader and NVIDIA driver.
Ubuntu's older diagnostic binary does not enumerate the mesh-shader feature structure.

## Verification

Verification asserts the running kernel and package pins, user and groups, package holds,
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

## Sources

- [Ubuntu server NVIDIA installation](https://ubuntu.com/server/docs/nvidia-drivers-installation/)
- [NVIDIA Container Toolkit installation](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/1.17.8/install-guide.html)
- [Node release checksums](https://nodejs.org/dist/v22.23.4/SHASUMS256.txt)
- [Astral uv release](https://github.com/astral-sh/uv/releases/tag/0.13.6)
- [uv's pinned Python download metadata](https://github.com/astral-sh/uv/blob/0.13.6/crates/uv-python-managed/download-metadata.json)
- [uv environment settings](https://docs.astral.sh/uv/reference/environment/)
- [LunarG SDK installation](https://vulkan.lunarg.com/doc/view/latest/linux/getting_started.html)
- [NVIDIA codec engine and format matrix](https://developer.nvidia.com/video-encode-decode-support-matrix)
