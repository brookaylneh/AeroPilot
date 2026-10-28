#!/usr/bin/env bash
set -euo pipefail
trap 'printf "host.sh failed at line %s\n" "$LINENO" >&2' ERR

deploy_dir=$(cd "$(dirname "$0")" && pwd)
service_user=brook_avionuas
project=/home/$service_user/snr
toolchains=$project/.local/toolchains
kernel=6.8.0-1066-aws
driver=580.178.04
node_version=22.23.4
uv_version=0.13.6
python_version=3.12.12
python_build=20260211
vulkan_version=1.4.363.0
vulkan_archive=vulkansdk-linux-x86_64-$vulkan_version.tar.xz
node_archive=node-v$node_version-linux-x64.tar.xz
uv_archive=uv-x86_64-unknown-linux-gnu.tar.gz
python_archive=cpython-$python_version+$python_build-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
gpu_image=ubuntu:24.04@sha256:186072bba1b2f436cbb91ef2567abca677337cfc786c86e107d25b7072feef0c
mode=${1:?Usage: host.sh user|packages|toolchains|verify}

test "$(uname -m)" = x86_64
test "$(uname -r)" = "$kernel"
grep -qx 'VERSION_ID="22.04"' /etc/os-release

download() {
  curl --fail --silent --show-error --location --connect-timeout 15 --max-time 300 \
    --retry 0 "$1" --output "$2"
}

held_packages() {
  grep -E '^(linux-|nvidia|libnvidia)' "$deploy_dir/host-packages.lock"
}

check_packages() {
  while IFS='=' read -r package version; do
    test "$(dpkg-query -W -f='${Version}' "$package")" = "$version"
  done <"$deploy_dir/host-packages.lock"
}

case "$mode" in
  user)
    test "$(id -u)" = 0
    ! getent passwd "$service_user"
    ! getent passwd 1002
    ! getent group "$service_user"
    ! getent group 1003
    test ! -e "/home/$service_user"
    groupadd --gid 1003 "$service_user"
    useradd --uid 1002 --gid 1003 --groups video \
      --create-home --shell /bin/bash "$service_user"
    id "$service_user"
    ;;
  packages)
    test "$(id -u)" = 0
    test "$(id -u "$service_user")" = 1002
    test "$(id -g "$service_user")" = 1003
    install -d -m 0755 /etc/apt/keyrings
    key_file=$project/.local/evidence/host/provisioning/nvidia-container.asc
    install -d -o "$service_user" -g "$service_user" "$project/.local/evidence/host/provisioning"
    download https://nvidia.github.io/libnvidia-container/gpgkey "$key_file"
    printf '%s  %s\n' \
      c880576d6cf75a48e5027a871bac70fd0421ab07d2b55f30877b21f1c87959c9 "$key_file" |
      sha256sum --check --strict
    gpg --batch --yes --dearmor --output /etc/apt/keyrings/nvidia-container.gpg "$key_file"
    printf '%s\n' \
      'deb [signed-by=/etc/apt/keyrings/nvidia-container.gpg] https://nvidia.github.io/libnvidia-container/stable/deb/amd64 /' \
      >/etc/apt/sources.list.d/nvidia-container-toolkit.list
    cat >/etc/apt/apt.conf.d/99-snr <<'APT'
Acquire::Retries "0";
Acquire::http::Timeout "30";
Acquire::https::Timeout "30";
APT::Update::Error-Mode "any";
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Automatic-Reboot-WithUsers "false";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "false";
APT
    apt-get update
    mapfile -t packages <"$deploy_dir/host-packages.lock"
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
    check_packages
    usermod --append --groups docker "$service_user"
    mapfile -t held < <(held_packages | cut -d= -f1)
    apt-mark hold "${held[@]}"
    nvidia-ctk runtime configure --runtime=docker
    install -d /etc/systemd/system/nvidia-persistenced.service.d
    cat >/etc/systemd/system/nvidia-persistenced.service.d/snr.conf <<'UNIT'
[Unit]
StopWhenUnneeded=false

[Service]
ExecStart=
ExecStart=/usr/bin/nvidia-persistenced --user nvidia-persistenced --persistence-mode --verbose
ExecStartPost=/usr/bin/nvidia-smi --persistence-mode=1
Restart=no

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable nvidia-persistenced docker
    systemctl restart nvidia-persistenced docker
    cat /proc/sys/kernel/random/boot_id >"$project/.local/evidence/host/provisioning/pre-reboot-id"
    ;;
  toolchains)
    test "$(id -un)" = "$service_user"
    test ! -e "$toolchains"
    mkdir -p "$toolchains/downloads" "$toolchains/bin"
    cd "$toolchains/downloads"
    download "https://nodejs.org/dist/v$node_version/$node_archive" "$node_archive"
    download "https://github.com/astral-sh/uv/releases/download/$uv_version/$uv_archive" "$uv_archive"
    download "https://github.com/astral-sh/python-build-standalone/releases/download/$python_build/$python_archive" \
      "$python_archive"
    download "https://sdk.lunarg.com/sdk/download/$vulkan_version/linux/$vulkan_archive" "$vulkan_archive"
    sha256sum --check --strict "$deploy_dir/toolchains.sha256"
    tar -xJf "$node_archive" -C "$toolchains"
    tar -xJf "$vulkan_archive" -C "$toolchains" "$vulkan_version/x86_64/bin/vulkaninfo"
    mv "$toolchains/$vulkan_version" "$toolchains/vulkan-sdk-$vulkan_version"
    ln -s "../vulkan-sdk-$vulkan_version/x86_64/bin/vulkaninfo" "$toolchains/bin/vulkaninfo"
    mkdir "$toolchains/uv-$uv_version"
    tar -xzf "$uv_archive" --strip-components=1 -C "$toolchains/uv-$uv_version"
    ln -s "../node-v$node_version-linux-x64/bin/node" "$toolchains/bin/node"
    ln -s "../node-v$node_version-linux-x64/bin/npm" "$toolchains/bin/npm"
    ln -s "../node-v$node_version-linux-x64/bin/npx" "$toolchains/bin/npx"
    ln -s "../uv-$uv_version/uv" "$toolchains/bin/uv"
    ln -s /usr/bin/python3.10 "$toolchains/bin/python3.10"
    mkdir -p "python/$python_build"
    ln "$python_archive" "python/$python_build/$python_archive"
    export UV_PYTHON_INSTALL_DIR=$toolchains/python
    export UV_PYTHON_INSTALL_MIRROR=file://$toolchains/downloads/python
    export UV_PYTHON_CPYTHON_BUILD=$python_build
    export UV_PYTHON_INSTALL_BIN=0 UV_HTTP_RETRIES=0 UV_HTTP_TIMEOUT=30 UV_HTTP_CONNECT_TIMEOUT=15
    export UV_CACHE_DIR=$project/.local/cache/uv
    "$toolchains/bin/uv" python install "$python_version"
    ln -s "../python/cpython-$python_version-linux-x86_64-gnu/bin/python3.12" \
      "$toolchains/bin/python3.12"
    test "$("$toolchains/bin/node" --version)" = "v$node_version"
    "$toolchains/bin/uv" --version | grep -E "^uv $uv_version "
    test "$("$toolchains/bin/python3.12" --version)" = "Python $python_version"
    test "$("$toolchains/bin/python3.10" --version)" = 'Python 3.10.12'
    sha256sum "$toolchains/bin/node" "$toolchains/bin/uv" \
      "$toolchains/bin/python3.10" "$toolchains/bin/python3.12" "$toolchains/bin/vulkaninfo" >"$toolchains/binaries.sha256"
    ;;
  verify)
    test "$(id -un)" = "$service_user"
    evidence=${2:?Usage: host.sh verify NEW_EVIDENCE_DIRECTORY}
    test ! -e "$evidence"
    mkdir -p "$evidence"
    evidence=$(realpath "$evidence")
    exec > >(tee "$evidence/host.txt") 2>&1
    date --utc --iso-8601=seconds
    id
    test "$(id -u)" = 1002
    test "$(id -g)" = 1003
    id -nG | tr ' ' '\n' | grep -x docker
    id -nG | tr ' ' '\n' | grep -x video
    tee "$evidence/boot-id" </proc/sys/kernel/random/boot_id
    test "$(cat "$evidence/boot-id")" != "$(cat "$project/.local/evidence/host/provisioning/pre-reboot-id")"
    uname -a
    lscpu >"$evidence/lscpu.txt"
    free -b
    lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
    df -B1 "$project"
    while IFS='=' read -r package version; do
      test "$(dpkg-query -W -f='${Version}' "$package")" = "$version"
    done < <(held_packages)
    dpkg-query -W -f='${binary:Package}\t${Version}\n' >"$evidence/packages.tsv"
    apt-mark showhold >"$evidence/holds.txt"
    held_packages | cut -d= -f1 | sed 's/:amd64$//' | sort >"$evidence/expected-holds.txt"
    diff -u "$evidence/expected-holds.txt" "$evidence/holds.txt"
    apt-config dump >"$evidence/apt-config.txt"
    grep -x 'Unattended-Upgrade::Automatic-Reboot "false";' "$evidence/apt-config.txt"
    systemctl is-enabled docker nvidia-persistenced
    systemctl is-active docker nvidia-persistenced
    test "$(modinfo -F version nvidia)" = "$driver"
    test "$(modinfo -F license nvidia)" = 'Dual MIT/GPL'
    modinfo nvidia >"$evidence/nvidia-module.txt"
    cat /proc/driver/nvidia/version
    nvidia-smi | tee "$evidence/nvidia-smi.txt"
    nvidia-smi -q >"$evidence/nvidia-smi-q.txt"
    test "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader)" = "$driver"
    test "$(nvidia-smi --query-gpu=persistence_mode --format=csv,noheader)" = Enabled
    nvidia-smi --query-gpu=name,power.limit,power.max_limit,clocks.max.graphics,clocks.max.memory,memory.total \
      --format=csv
    export VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/nvidia_icd.json
    export XDG_RUNTIME_DIR=$evidence/runtime
    mkdir -m 0700 "$XDG_RUNTIME_DIR"
    "$toolchains/bin/vulkaninfo" >"$evidence/vulkaninfo.txt" 2>"$evidence/vulkaninfo.stderr"
    for feature in shaderImageInt64Atomics meshShader taskShader rayQuery accelerationStructure; do
      grep -E "^[[:space:]]*${feature}[[:space:]]*=[[:space:]]*true" "$evidence/vulkaninfo.txt"
    done
    grep -E 'deviceName|apiVersion|VK_EXT_mesh_shader' "$evidence/vulkaninfo.txt"
    ffmpeg -hide_banner -nostdin -f lavfi -i testsrc2=size=1920x1080:rate=60 \
      -frames:v 120 -c:v h264_nvenc -pix_fmt yuv420p -f h264 "$evidence/nvenc.h264" \
      >"$evidence/nvenc.txt" 2>&1
    ffmpeg -hide_banner -nostdin -hwaccel cuda -hwaccel_output_format cuda \
      -c:v h264_cuvid -i "$evidence/nvenc.h264" -vf hwdownload,format=nv12 -f null - \
      >"$evidence/nvdec.txt" 2>&1
    ffprobe -v error -count_frames -select_streams v:0 \
      -show_entries stream=codec_name,width,height,nb_read_frames \
      -of json "$evidence/nvenc.h264" | tee "$evidence/nvenc.json"
    test "$(jq -r '.streams[0].nb_read_frames' "$evidence/nvenc.json")" = 120
    docker pull "$gpu_image"
    docker image inspect "$gpu_image" >"$evidence/docker-image.json"
    docker run --rm --gpus all "$gpu_image" nvidia-smi | tee "$evidence/docker-gpu.txt"
    grep -v "/bin/python3.10$" "$toolchains/binaries.sha256" | sha256sum --check --strict
    (cd "$toolchains/downloads" && sha256sum --check --strict "$deploy_dir/toolchains.sha256")
    "$toolchains/bin/node" --version
    "$toolchains/bin/uv" --version
    "$toolchains/bin/python3.10" --version
    "$toolchains/bin/python3.12" --version
    printf 'Host verification passed.\n'
    ;;
  *)
    printf 'Unknown mode: %s\n' "$mode" >&2
    exit 1
    ;;
esac
