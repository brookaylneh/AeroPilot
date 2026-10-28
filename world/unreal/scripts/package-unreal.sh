#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
: "${UE_ROOT:?Set UE_ROOT to an installed Unreal Engine 5.8.2 directory}"
package_path="$(realpath -m "${1:?Usage: package-unreal.sh NEW_PACKAGE_DIRECTORY NEW_EVIDENCE_DIRECTORY}")"
evidence_path="$(realpath -m "${2:?Provide a new evidence directory}")"
test ! -e "$package_path"
test ! -e "$evidence_path"
mkdir -p "$package_path" "$evidence_path"
python3 - "$UE_ROOT" <<'PY'
import json,sys,pathlib
v=json.loads((pathlib.Path(sys.argv[1])/'Engine/Build/Build.version').read_text())
assert (v['MajorVersion'],v['MinorVersion'],v['PatchVersion']) == (5,8,2), 'Use UE 5.8.2 for this pinned Cosys binary; rebuild plugin for other engines.'
PY
project="$PWD/world/unreal/ScenarioStudio/ScenarioStudio.uproject"
if [ ! -f world/unreal/ScenarioStudio/Plugins/AirSim/AirSim.uplugin ]; then
  echo 'Stage the pinned plugin with python3 world/cosys/fetch-cosys.py.' >&2
  exit 2
fi
# These must be real, authored UE assets. The Blocks compatibility scene is not the wilderness deliverable.
for asset in world/unreal/ScenarioStudio/Content/OpenMeadow/Catalog.uasset world/unreal/ScenarioStudio/Content/OpenMeadow/OpenMeadow.umap; do
  if [ ! -f "$asset" ]; then
    echo "Missing authored runtime asset: $asset. See world/unreal/README.md and world/unreal/assets.md." >&2
    exit 2
  fi
done
# A full cook is required here: legacy iteration can retain default-material
# shader maps when runtime Landscape Grass permutations change.
"$UE_ROOT/Engine/Build/BatchFiles/RunUAT.sh" BuildCookRun \
  -project="$project" -noP4 -platform=Linux -clientconfig=Development \
  -build -cook -map=/Game/OpenMeadow/OpenMeadow -stage -pak -archive \
  -ddc=InstalledNoZenLocalFallback -AdditionalCookerOptions="-SkipZenStore" \
  -archivedirectory="$package_path" | tee "$evidence_path/package.log"
python3 - "$package_path" "$evidence_path" <<'PY'
import sys
from pathlib import Path
package, evidence = map(Path, sys.argv[1:])
log = (evidence / 'package.log').read_text(errors='replace')
failures = ['Failed to compile Material', "doesn't have a valid ShaderMap", 'LogCook: Error:']
if any(message in log for message in failures):
    raise SystemExit('Package contains cook/material failures; inspect the recorded package.log')
(package / 'Linux/scenario-map.txt').write_text('/Game/OpenMeadow/OpenMeadow\n')
config = Path('world/unreal/ScenarioStudio/Config/DefaultEngine.ini').read_text()
platforms = [line.split('=', 1)[1] for line in config.splitlines() if line.startswith('+TargetedRHIs=')]
assert len(platforms) == 1 and platforms[0] in ('SF_VULKAN_SM5', 'SF_VULKAN_SM6'), platforms
(package / 'Linux/shader-platform.txt').write_text(platforms[0] + '\n')
(evidence / 'DefaultEngine.ini').write_text(config)

PY

mkdir -p "$package_path/Linux/licenses"
cp .local/vendor/rocketbox-motion/LICENSE.md "$package_path/Linux/licenses/Microsoft-Rocketbox-MIT.txt"
