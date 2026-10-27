#!/usr/bin/env bash
# Runs the Blocks compatibility world via the editor executable, not a packaged release.
set -euo pipefail
umask 077
cd "$(dirname "$0")/.."
engine_root="${UE_ROOT:-$HOME/UnrealEngine/5.8.2}"
shader_model="${1:-sm5}"
scene="${2:-foundation}"
extra_flags=()
if [ "${3:-}" = diagnostics ]; then extra_flags+=(-ScenarioDiagnostics); fi
case "$scene" in
  foundation) world=/Game/FlyingCPP/Maps/FlyingExampleMap ;;
  wilderness) world=/Game/Wilderness/AlderValley ;;
  *) echo 'Use foundation or wilderness.' >&2; exit 2 ;;
esac
case "$shader_model" in sm5|sm6) ;; *) echo 'Use sm5 or sm6.' >&2; exit 2 ;; esac
if [ ! -f unreal/ScenarioStudio/Content/Scenario/BP_ScenarioCamera.uasset ]; then
  echo 'Prepare the foundation camera with the Unreal Python commandlet first. See docs/unreal-integration.md.' >&2
  exit 2
fi
# Cosys 3.5 requires this file even with annotation disabled. An empty catalog is
# valid and declares no material labels. Preserve any future authored catalog.
if [ ! -e "$HOME/Documents/AirSim/materials.csv" ]; then
  install -D -m 644 unreal/materials.csv "$HOME/Documents/AirSim/materials.csv"
fi
exec "$engine_root/Engine/Binaries/Linux/UnrealEditor" \
  "$PWD/unreal/ScenarioStudio/ScenarioStudio.uproject" \
  "$world" -game -RenderOffscreen -unattended -ddc=InstalledNoZenLocalFallback \
  -nosound -NoSplash -nocrashreports -vulkan "-$shader_model" -ResX=1280 -ResY=720 -ForceRes \
  -settings="$PWD/unreal/cosys-settings.json" \
  "-ScenarioSaveDir=$PWD/data/unreal-scenarios" \
  -PixelStreamingConnectionURL=ws://127.0.0.1:8888 \
  -PixelStreamingEncoderCodec=VP8 -PixelStreamingWebRTCFps=24 \
  "${extra_flags[@]}" \
  -ExecCmds="t.MaxFPS 30" \
  -log -abslog="$PWD/evidence/unreal-$scene-$shader_model.log" \
  -stdout -FullStdOutLogOutput
