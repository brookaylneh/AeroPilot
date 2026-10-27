#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${UE_ROOT:?Set UE_ROOT to an installed Unreal Engine 5.8.2 directory}"
python3 - "$UE_ROOT" <<'PY'
import json,sys,pathlib
v=json.loads((pathlib.Path(sys.argv[1])/'Engine/Build/Build.version').read_text())
assert (v['MajorVersion'],v['MinorVersion'],v['PatchVersion']) == (5,8,2), 'Use UE 5.8.2 for this pinned Cosys binary; rebuild plugin for other engines.'
PY
project="$PWD/unreal/ScenarioStudio/ScenarioStudio.uproject"
if [ ! -f unreal/ScenarioStudio/Plugins/AirSim/AirSim.uplugin ]; then
  echo 'Stage the pinned plugin with python3 scripts/fetch-cosys.py.' >&2
  exit 2
fi
# These must be real, authored UE assets. The Blocks compatibility scene is not the wilderness deliverable.
for asset in unreal/ScenarioStudio/Content/OpenMeadow/Catalog.uasset unreal/ScenarioStudio/Content/OpenMeadow/OpenMeadow.umap; do
  if [ ! -f "$asset" ]; then
    echo "Missing authored runtime asset: $asset. See docs/setup.md and docs/asset-dependencies.md." >&2
    exit 2
  fi
done
# A full cook is required here: legacy iteration can retain default-material
# shader maps when runtime Landscape Grass permutations change.
"$UE_ROOT/Engine/Build/BatchFiles/RunUAT.sh" BuildCookRun \
  -project="$project" -noP4 -platform=Linux -clientconfig=Development \
  -build -cook -map=/Game/OpenMeadow/OpenMeadow -stage -pak -archive \
  -ddc=InstalledNoZenLocalFallback -AdditionalCookerOptions="-SkipZenStore" \
  -archivedirectory="$PWD/unreal/Packaged" | tee "$PWD/evidence/unreal-package-internal.log"
python3 - <<'PY'
from pathlib import Path
log = Path('evidence/unreal-package-internal.log').read_text(errors='replace')
failures = ['Failed to compile Material', "doesn't have a valid ShaderMap", 'LogCook: Error:']
if any(message in log for message in failures):
    raise SystemExit('Package contains cook/material failures; inspect evidence/unreal-package-internal.log')
Path('unreal/Packaged/Linux/scenario-map.txt').write_text('/Game/OpenMeadow/OpenMeadow\n')
PY
