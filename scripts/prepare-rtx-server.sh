#!/usr/bin/env bash
# Prepare the existing Ubuntu 22.04 L4 host; no application or driver upgrade.
set -euo pipefail
umask 077
requirements=$(realpath "${1:?Usage: prepare-rtx-server.sh /path/to/simulation/phase2a/requirements.txt}")
[[ -f "$requirements" ]]

source /etc/os-release
[[ "$ID:$VERSION_ID:$(uname -m)" == ubuntu:22.04:x86_64 ]] || {
  echo 'Expected Ubuntu 22.04 on x86_64.' >&2
  exit 1
}
[[ $(id -u) != 0 ]] || { echo 'Run as the deployment user with sudo access.' >&2; exit 1; }
sudo -n true
sudo -n docker info >/dev/null
command -v nvidia-ctk >/dev/null
[[ $(nvidia-smi --query-gpu=name --format=csv,noheader) == 'NVIDIA L4' ]] || {
  echo 'Expected one NVIDIA L4.' >&2
  exit 1
}

driver_version=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader)
driver_package=$(dpkg-query -W -f='${Version}' libnvidia-compute-580-server)
[[ "$driver_package" == "$driver_version"-* ]] || {
  echo 'Installed driver package does not match the loaded kernel driver.' >&2
  exit 1
}
sudo -n apt-get update
sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends --no-upgrade \
  "libnvidia-encode-580-server=$driver_package" \
  "libnvidia-decode-580-server=$driver_package" \
  ca-certificates curl xz-utils unzip rsync git ripgrep jq python3-venv python3-pip \
  build-essential cmake ninja-build shellcheck ffmpeg vulkan-tools libegl1 libgl1 \
  libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libdbus-1-3 \
  libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxrandr2 libgbm1 \
  libxcb1 libxkbcommon0 libasound2 fonts-liberation libpango-1.0-0 libcairo2
sudo -n ldconfig
[[ $(nvidia-smi --query-gpu=driver_version --format=csv,noheader) == "$driver_version" ]]

# Use the supported Node 22 line with an exact archive checksum.
node_version=v22.23.4
node_archive="node-$node_version-linux-x64.tar.xz"
node_prefix="/opt/snr/node-$node_version-linux-x64"
node_sha=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce
download_dir=$(mktemp -d)
trap 'rm -rf "$download_dir"' EXIT
curl -fsSL "https://nodejs.org/dist/$node_version/$node_archive" -o "$download_dir/$node_archive"
printf '%s  %s\n' "$node_sha" "$download_dir/$node_archive" | sha256sum --check
sudo -n install -d -m 755 /opt/snr
sudo -n tar -xJf "$download_dir/$node_archive" -C /opt/snr
for executable in node npm npx; do
  destination="/usr/local/bin/$executable"
  if [[ -e "$destination" || -L "$destination" ]]; then
    [[ $(readlink "$destination") == "$node_prefix/bin/$executable" ]] || {
      echo "Refusing to replace existing $destination." >&2
      exit 1
    }
  fi
  sudo -n ln -sfn "$node_prefix/bin/$executable" "$destination"
done
[[ $(node --version) == "$node_version" ]]

sudo -n usermod -aG docker "$(id -un)"
sudo -n install -d -o "$(id -u)" -g "$(id -g)" -m 750 /srv/snr
python3 -m venv /srv/snr/.venv-cosys
/srv/snr/.venv-cosys/bin/python -m pip install \
  --requirement "$requirements"
/srv/snr/.venv-cosys/bin/python -m pip check
/srv/snr/.venv-cosys/bin/python -c 'import cosysairsim, numpy, PIL; print("Cosys dependencies imported successfully.")'
printf 'Preparation complete. Reconnect SSH to activate Docker group membership.\n'
