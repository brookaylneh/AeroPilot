#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
patches=(world/cosys/airsim-*.patch)
[[ -f "${patches[0]}" ]] || { echo 'Required Cosys patches are missing.' >&2; exit 2; }
for patch_file in "${patches[@]}"; do
  patch --force --reverse --dry-run --fuzz=0 -p1 \
    -d world/unreal/ScenarioStudio/Plugins/AirSim -i "$PWD/$patch_file"
done
