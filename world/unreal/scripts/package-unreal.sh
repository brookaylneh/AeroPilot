#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
: "${UE_ROOT:?Set UE_ROOT to an installed Unreal Engine 5.8.2 directory}"
.venv-cosys/bin/python -m world.cosys.plugin
archive_dir=$(realpath -m "${1:?Usage: package-unreal.sh NEW_BUILD_DIRECTORY NEW_EVIDENCE_DIRECTORY}")
evidence_dir=$(realpath -m "${2:?Usage: package-unreal.sh NEW_BUILD_DIRECTORY NEW_EVIDENCE_DIRECTORY}")
[[ ! -e "$archive_dir" && ! -e "$evidence_dir" ]] || { echo 'Build and evidence destinations must be new.' >&2; exit 2; }
mkdir -p "$archive_dir" "$evidence_dir"
export uebp_EngineSavedFolder="$evidence_dir/commandlets"
export uebp_LogFolder="$evidence_dir/automation"
mkdir "$uebp_EngineSavedFolder" "$uebp_LogFolder"
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
  -UbtArgs="-MaxParallelActions=6 -NoUBA" \
  -ddc=InstalledNoZenLocalFallback -AdditionalCookerOptions="-SkipZenStore -notraceserver" \
  -archivedirectory="$archive_dir" | tee "$evidence_dir/package.log"
python3 - "$archive_dir" "$evidence_dir" <<'PY'
import shutil
import sys
from pathlib import Path
archive, evidence = map(Path, sys.argv[1:])
log = (evidence / 'package.log').read_text(errors='replace')
failures = ['Failed to compile Material', "doesn't have a valid ShaderMap", 'LogCook: Error:']
if any(message in log for message in failures):
    raise SystemExit(f'Package contains cook/material failures; inspect {evidence / "package.log"}')
(archive / 'Linux/scenario-map.txt').write_text('/Game/OpenMeadow/OpenMeadow\n')
shutil.copy2('world/cosys/world.json', archive / 'Linux/world.json')
shutil.copy2('world/cosys/settings.json', archive / 'Linux/cosys-settings.json')
PY
