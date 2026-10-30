#!/usr/bin/env bash
# Keep packaging output separate from the executable serving browsers.
set -euo pipefail
cd "$(dirname "$0")/../../.."
package_path="$(realpath "${1:?Usage: stage-live-release.sh PACKAGE_LINUX_DIRECTORY}")"
test -x "$package_path/ScenarioStudio.sh"
# The packaged manifest must be the tracked world it was cooked from.
cmp world/cosys/world.json "$package_path/world.json"
read -r pso_cache _ < "$package_path/pso-cache.txt"
[[ "$pso_cache" = bundled ]] || { echo 'Only a package with a bundled pipeline cache can be staged' >&2; exit 2; }
release_id="$(date -u +%Y%m%dT%H%M%SZ)"
release_path="$PWD/.local/unreal/releases/$release_id"
mkdir -p "$release_path"
cp -a --reflink=auto "$package_path/." "$release_path/"
.venv-cosys/bin/python -m world.cosys.configure-drones \
  --rpc-port "${SNR_COSYS_PORT:?}" --instance-base "${SNR_SITL_INSTANCE_BASE:?}" \
  --output "$release_path/cosys-settings.json"
ln -s "releases/$release_id" .local/unreal/Live.next
mv -Tf .local/unreal/Live.next .local/unreal/Live
echo "Release staged: $release_id. Restart snr-unreal only after validation and a scene checkpoint."
