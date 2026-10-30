#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
: "${UE_ROOT:?Set UE_ROOT to an installed Unreal Engine 5.8.2 directory}"
world_config=$(realpath "${SNR_WORLD_CONFIG:?Set SNR_WORLD_CONFIG to the world manifest matching the authored Content}")
cosys_settings=$(realpath "${SNR_COSYS_SETTINGS:?Set SNR_COSYS_SETTINGS to the release camera and vehicle settings}")
[[ -f "$world_config" && -f "$cosys_settings" ]] || { echo 'Release manifest inputs must exist.' >&2; exit 2; }
bash world/cosys/verify-plugin.sh
archive_dir=$(realpath -m "${1:?Usage: package-unreal.sh NEW_BUILD_DIRECTORY NEW_EVIDENCE_DIRECTORY}")
evidence_dir=$(realpath -m "${2:?Usage: package-unreal.sh NEW_BUILD_DIRECTORY NEW_EVIDENCE_DIRECTORY}")
[[ ! -e "$archive_dir" && ! -e "$evidence_dir" ]] || { echo 'Build and evidence destinations must be new.' >&2; exit 2; }
mkdir -p "$archive_dir" "$evidence_dir"
export uebp_EngineSavedFolder="$evidence_dir/commandlets"
export uebp_LogFolder="$evidence_dir/automation"
mkdir "$uebp_EngineSavedFolder" "$uebp_LogFolder"
# A bundled pipeline cache is valid only for the content it was recorded from. A recording
# package carries none and exists to record one; it is never staged.
pso_cache="${SNR_PSO_CACHE:?Set SNR_PSO_CACHE to bundled or recording}"
[[ "$pso_cache" = bundled || "$pso_cache" = recording ]] || { echo 'SNR_PSO_CACHE must be bundled or recording' >&2; exit 2; }
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
python3 - "$archive_dir" "$evidence_dir" "$world_config" "$cosys_settings" "$pso_cache" <<'PY'
import hashlib
import json
import shutil
import sys
from pathlib import Path
archive, evidence, world_config, cosys_settings = map(Path, sys.argv[1:5])
pso_cache = sys.argv[5]
log = (evidence / 'package.log').read_text(errors='replace')
failures = ['Failed to compile Material', "doesn't have a valid ShaderMap", 'LogCook: Error:']
if any(message in log for message in failures):
    raise SystemExit(f'Package contains cook/material failures; inspect {evidence / "package.log"}')
(archive / 'Linux/scenario-map.txt').write_text('/Game/OpenMeadow/OpenMeadow\n')
config = Path('world/unreal/ScenarioStudio/Config/DefaultEngine.ini')
platforms = [line.split('=', 1)[1] for line in config.read_text().splitlines() if line.startswith('+TargetedRHIs=')]
assert len(platforms) == 1 and platforms[0] in ('SF_VULKAN_SM5', 'SF_VULKAN_SM6'), platforms
(archive / 'Linux/shader-platform.txt').write_text(platforms[0] + '\n')
shutil.copy2(config, archive / 'Linux/DefaultEngine.ini')
shutil.copy2(world_config, archive / 'Linux/world.json')
shutil.copy2(cosys_settings, archive / 'Linux/cosys-settings.json')
keys = Path('world/unreal/ScenarioStudio/Saved/Cooked/Linux/ScenarioStudio/Metadata/PipelineCaches')
key_output = evidence / 'ShaderStableKeys'
key_output.mkdir()
for library in ('Global', 'ScenarioStudio'):
    source = keys / f'ShaderStableInfo-{library}-{platforms[0]}.shk'
    assert source.is_file(), source
    shutil.copy2(source, key_output / source.name)
source_cache = Path(f'world/unreal/ScenarioStudio/Build/Linux/PipelineCaches/ScenarioStudio_{platforms[0]}.spc')
cooked_cache = keys / f'ScenarioStudio_{platforms[0]}.stable.upipelinecache'
if pso_cache == 'recording':
    assert not source_cache.exists() and not cooked_cache.exists(), 'A recording package must not bundle a cache'
    (archive / 'Linux/pso-cache.txt').write_text('recording\n')
else:
    assert source_cache.is_file(), source_cache
    assert cooked_cache.is_file() and cooked_cache.stat().st_size > 0, cooked_cache
    cache_copy = evidence / cooked_cache.name
    shutil.copy2(cooked_cache, cache_copy)
    source_sha256 = hashlib.sha256(source_cache.read_bytes()).hexdigest()
    (evidence / 'pipeline-cache.json').write_text(json.dumps({
        'source': str(source_cache),
        'sourceSha256': source_sha256,
        'cooked': str(cache_copy),
        'cookedBytes': cache_copy.stat().st_size,
        'cookedSha256': hashlib.sha256(cache_copy.read_bytes()).hexdigest(),
    }, indent=2) + '\n')
    (archive / 'Linux/pso-cache.txt').write_text(f'bundled {source_sha256}\n')
PY

mkdir "$archive_dir/Linux/licenses"
cp .local/vendor/rocketbox-motion/LICENSE.md "$archive_dir/Linux/licenses/Microsoft-Rocketbox-MIT.txt"
