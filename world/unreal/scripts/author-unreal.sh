#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
author_script="${1:?Usage: author-unreal.sh script-name [extra-plugins]}"
[[ "$author_script" =~ ^[a-z0-9-]+$ ]] || exit 2
engine_root="${UE_ROOT:-$HOME/UnrealEngine/5.8.2}"
# Every level save bakes the landscape grass maps (ALandscapeProxy::PreSave), which needs a
# renderer; under -nullrhi the bake silently does nothing.
exec bash world/unreal/scripts/unreal-container.sh exec \
  "$engine_root/Engine/Binaries/Linux/UnrealEditor-Cmd" \
  "$PWD/world/unreal/ScenarioStudio/ScenarioStudio.uproject" \
  -run=pythonscript -script="$PWD/world/unreal/ScenarioStudio/Scripts/$author_script.py" \
  -EnablePlugins="PythonScriptPlugin,EditorScriptingUtilities${2:+,$2}" \
  -ddc=InstalledNoZenLocalFallback -AllowCommandletRendering -RenderOffscreen -vulkan -unattended -nosound -nocrashreports \
  -stdout -FullStdOutLogOutput
