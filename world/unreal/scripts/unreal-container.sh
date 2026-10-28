#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
engine_root="${UE_ROOT:-$HOME/UnrealEngine/5.8.2}"
mode="${1:-build}"
if (( $# )); then shift; fi
cache_root="$PWD/.local/vendor/unreal-container-home"
mkdir -p "$cache_root"
cpu_limit="${SNR_CPU_LIMIT:-6}"
[[ "$cpu_limit" =~ ^[1-9][0-9]*$ ]] || { echo 'SNR_CPU_LIMIT must be a positive integer' >&2; exit 2; }
memory_limit="${SNR_MEMORY_LIMIT:-48g}"
[[ "$memory_limit" =~ ^[1-9][0-9]*[mg]$ ]] || { echo 'SNR_MEMORY_LIMIT must use Docker units, for example 24g' >&2; exit 2; }
container_flags=(--rm --init "--cpus=$cpu_limit" "--memory=$memory_limit" --shm-size=2g --ulimit core=0 --network=host
  --user "$(id -u):$(id -g)"
  --mount "type=bind,src=$PWD,dst=$PWD"
  --mount "type=bind,src=$cache_root,dst=/home/snr"
  --workdir "$PWD")
# Cooked releases contain the engine runtime. Only editor/build modes need the SDK.
if [[ "$mode" != run-packaged ]]; then
  [[ -f "$engine_root/Engine/Build/Build.version" ]] || { echo "Missing Unreal SDK: $engine_root" >&2; exit 2; }
  container_flags+=(--mount "type=bind,src=$engine_root,dst=$engine_root" -e "UE_ROOT=$engine_root")
fi
gpu_flags=(--runtime=nvidia --gpus all -e "NVIDIA_DRIVER_CAPABILITIES=graphics,utility,compute,video,display")
# This host's NVIDIA container runtime mounts the libraries but omits the Vulkan ICD manifest.
for icd_path in /usr/share/vulkan/icd.d/nvidia_icd.json /etc/vulkan/icd.d/nvidia_icd.json; do
  if [ -f "$icd_path" ]; then
    gpu_flags+=(--mount "type=bind,src=$icd_path,dst=/etc/vulkan/icd.d/nvidia_icd.json,readonly")
    break
  fi
done
for egl_path in /usr/share/glvnd/egl_vendor.d/10_nvidia.json /etc/glvnd/egl_vendor.d/10_nvidia.json; do
  if [ -f "$egl_path" ]; then
    gpu_flags+=(--mount "type=bind,src=$egl_path,dst=/usr/share/glvnd/egl_vendor.d/10_nvidia.json,readonly")
    break
  fi
done
case "$mode" in
  build)
    exec docker run "${container_flags[@]}" snr-unreal:5.8.2-ubuntu24.04 bash world/unreal/scripts/build-unreal.sh "$@"
    ;;
  run-packaged)
    if [ -n "${SNR_CONTAINER_NAME:-}" ]; then
      [[ "$SNR_CONTAINER_NAME" =~ ^snr-[a-z0-9-]+$ ]] || exit 2
      container_flags+=(--name "$SNR_CONTAINER_NAME")
    fi
    exec docker run "${container_flags[@]}" "${gpu_flags[@]}" --pid=host --ulimit memlock=-1 -e "SNR_STREAMER_PORT=${SNR_STREAMER_PORT:?}" \
      -e "SNR_WEBRTC_MIN_PORT=${SNR_WEBRTC_MIN_PORT:?}" -e "SNR_WEBRTC_MAX_PORT=${SNR_WEBRTC_MAX_PORT:?}" \
      -e "SNR_MEDIA_CAPTURE=${SNR_MEDIA_CAPTURE:-1}" \
      -e "SNR_PROFILE=${SNR_PROFILE:-0}" \
      -e "SNR_COSYS_SETTINGS=${SNR_COSYS_SETTINGS:-$PWD/world/cosys/settings.json}" \
      -e "SNR_DRONE_DATA_DIR=${SNR_DRONE_DATA_DIR:-}" \
      -e "SNR_STREAM_CODEC=${SNR_STREAM_CODEC:-VP8}" \
      -e "SNR_STREAM_FPS=${SNR_STREAM_FPS:-60}" \
      -e "SNR_STREAM_MAX_BITRATE=${SNR_STREAM_MAX_BITRATE:-10000000}" \
      -e "SNR_STREAM_MIN_BITRATE=${SNR_STREAM_MIN_BITRATE:-1000000}" \
      -e "SNR_STREAM_TARGET_BITRATE=${SNR_STREAM_TARGET_BITRATE:-}" \
      -e "SNR_STREAM_HEIGHT=${SNR_STREAM_HEIGHT:-720}" \
      -e "SNR_RHI_THREAD=${SNR_RHI_THREAD:-1}" \
      -e "SNR_PACKAGE_ROOT=${SNR_PACKAGE_ROOT:-$PWD/.local/unreal/Packaged/Linux}" \
      -e "SNR_SCENARIO_DATA_DIR=${SNR_SCENARIO_DATA_DIR:-$PWD/.local/data/unreal-scenarios}" \
      "${SNR_UNREAL_IMAGE:-snr-unreal:5.8.2-ubuntu24.04}" bash world/unreal/scripts/run-packaged.sh "$@"
    ;;
  exec)
    # Concurrent authoring commandlets must have unique PIDs for UE's shared
    # shader-worker filenames under the mounted project Intermediate directory.
    exec docker run "${container_flags[@]}" "${gpu_flags[@]}" --pid=host \
      snr-unreal:5.8.2-ubuntu24.04 "$@"
    ;;
  character-authoring)
    authoring_name="${SNR_CONTAINER_NAME:-snr-character-authoring}"
    [[ "$authoring_name" =~ ^snr-[a-z0-9-]+$ ]] || exit 2
    exec docker run "${container_flags[@]}" "${gpu_flags[@]}" --pid=host \
      --name "$authoring_name" --ulimit memlock=-1 snr-unreal-fab:5.8.2 \
      xvfb-run -a --server-args="-screen 0 2048x2048x24 -nolisten tcp" "$@" -ScriptErrorsAreFatal
    ;;
  metahuman-login)
    exec docker run "${container_flags[@]}" "${gpu_flags[@]}" \
      --name snr-metahuman-authoring -e "SNR_AUTH_DIR=$PWD/.secrets" \
      --mount "type=bind,src=$PWD/world/unreal/scripts/epic-login-open.py,dst=/usr/bin/xdg-open,readonly" \
      snr-unreal:5.8.2-ubuntu24.04 timeout 900 \
      "$engine_root/Engine/Binaries/Linux/UnrealEditor-Cmd" \
      "$PWD/world/unreal/ScenarioStudio/ScenarioStudio.uproject" \
      -run=pythonscript -script="$PWD/world/unreal/ScenarioStudio/Scripts/prepare-metahuman.py" \
      -EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,MetaHumanCharacter \
      -ddc=InstalledNoZenLocalFallback -nullrhi -unattended -nosound -nocrashreports -stdout -FullStdOutLogOutput
    ;;
  fab-authoring)
    # Editor-only asset acquisition. CEF debugging is local; never expose it
    # through the authoring website. Login URLs are captured in private files.
    exec docker run "${container_flags[@]}" "${gpu_flags[@]}" --pid=host \
      --name snr-fab-authoring -e "SNR_AUTH_DIR=$PWD/.secrets" -e SNR_AUTH_FILE=fab-login.url \
      --mount "type=bind,src=$PWD/world/unreal/scripts/epic-login-open.py,dst=/usr/bin/xdg-open,readonly" \
      snr-unreal-fab:5.8.2 \
      xvfb-run -a --server-args="-screen 0 1600x1000x24 -nolisten tcp" \
      "$engine_root/Engine/Binaries/Linux/UnrealEditor" \
      "$PWD/world/unreal/ScenarioStudio/ScenarioStudio.uproject" /Engine/Maps/Entry \
      -ExecutePythonScript="$PWD/world/unreal/ScenarioStudio/Scripts/open-fab.py" \
      -EnablePlugins=Fab,PythonScriptPlugin,EditorScriptingUtilities \
      -vulkan -sm5 -cefdebug=9223 -ExecCmds="t.MaxFPS 10" -ResX=1440 -ResY=900 \
      -ddc=InstalledNoZenLocalFallback -unattended -nosound -NoSplash -nocrashreports -stdout -FullStdOutLogOutput
    ;;
  *)
    echo 'Usage: unreal-container.sh build | run-packaged | exec command ... |' \
      'character-authoring command ... | metahuman-login | fab-authoring' >&2
    exit 2
    ;;
esac
