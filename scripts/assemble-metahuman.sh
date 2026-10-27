#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
ulimit -c 0
engine_root="${UE_ROOT:-$HOME/UnrealEngine/5.8.2}"
# TextureGraph's engine normally skips commandlet initialization. MetaHuman's
# garment baker requires its Blobber; the official opt-in prevents a null access.
exec bash scripts/unreal-container.sh exec \
  "$engine_root/Engine/Binaries/Linux/UnrealEditor-Cmd" \
  "$PWD/unreal/ScenarioStudio/ScenarioStudio.uproject" \
  -run=pythonscript -script="$PWD/unreal/ScenarioStudio/Scripts/assemble-metahuman.py" \
  -EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,MetaHumanCharacter \
  -ini:Engine:[ConsoleVariables]:TextureGraph.AllowCommandlets=1 \
  -ddc=InstalledNoZenLocalFallback -AllowCommandletRendering \
  -RenderOffscreen -vulkan -sm5 -unattended -nosound -nocrashreports \
  -stdout -FullStdOutLogOutput
