#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
engine_root="${UE_ROOT:-$HOME/UnrealEngine/5.8.2}"
python3 - "$engine_root" <<'PY'
import json, pathlib, sys
v = json.loads((pathlib.Path(sys.argv[1]) / 'Engine/Build/Build.version').read_text())
assert tuple(v[k] for k in ('MajorVersion', 'MinorVersion', 'PatchVersion')) == (5, 8, 2), 'Expected pinned UE 5.8.2'
PY
exec "$engine_root/Engine/Build/BatchFiles/Linux/Build.sh" \
  ScenarioStudioEditor Linux Development \
  -Project="$PWD/world/unreal/ScenarioStudio/ScenarioStudio.uproject" \
  -WaitMutex -MaxParallelActions=6 -NoUBA "$@"
