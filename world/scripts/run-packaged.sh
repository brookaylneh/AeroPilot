#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")/../.."
package_root="${SNR_PACKAGE_ROOT:-$PWD/world/unreal/Packaged/Linux}"
launcher="$package_root/ScenarioStudio.sh"
runtime_binary="$package_root/ScenarioStudio/Binaries/Linux/ScenarioStudio"
# A release must identify its cooked map; never substitute an unrelated world.
read -r world_map < "$package_root/scenario-map.txt"
[[ "$world_map" =~ ^/Game/[A-Za-z0-9_/]+$ ]] || { echo 'Invalid release map manifest' >&2; exit 2; }
streamer_port="${SNR_STREAMER_PORT:-8888}"
[[ "$streamer_port" =~ ^[0-9]+$ ]] && ((streamer_port > 0 && streamer_port < 65536)) || exit 2
stream_fps="${SNR_STREAM_FPS:-60}"
[[ "$stream_fps" = 30 || "$stream_fps" = 60 ]] || { echo 'SNR_STREAM_FPS must be 30 or 60' >&2; exit 2; }
max_bitrate="${SNR_STREAM_MAX_BITRATE:-10000000}"
case "$max_bitrate" in
  10000000|20000000|25000000|40000000) ;;
  *) echo 'SNR_STREAM_MAX_BITRATE must be 10000000, 20000000, 25000000 or 40000000' >&2; exit 2 ;;
esac
# WebRTC's delay-based estimate collapses to ~1-3 Mbps on jittery Wi-Fi; the
# floor keeps moving grass encodable. It must stay below the viewer's capacity.
min_bitrate="${SNR_STREAM_MIN_BITRATE:-1000000}"
case "$min_bitrate" in
  1000000|5000000|10000000) ;;
  *) echo 'SNR_STREAM_MIN_BITRATE must be 1000000, 5000000 or 10000000' >&2; exit 2 ;;
esac
start_bitrate=$max_bitrate
stream_codec="${SNR_STREAM_CODEC:-VP8}"
case "$stream_codec" in
  VP8|VP9|H264|AV1) ;;
  *) echo 'SNR_STREAM_CODEC must be VP8, VP9, H264 or AV1' >&2; exit 2 ;;
esac
stream_height="${SNR_STREAM_HEIGHT:-720}"
case "$stream_height" in 1080) stream_width=1920 ;; 720) stream_width=1280 ;; 540) stream_width=960 ;; *) exit 2 ;; esac
extra_flags=()
if [[ "$stream_codec" = H264 || "$stream_codec" = AV1 ]]; then
  # Keyframes only on browser request: periodic ones burst and get lost on Wi-Fi.
  # The latency value must be the exact enum name; the plugin asserts otherwise.
  extra_flags+=(-PixelStreamingEncoderQualityPreset=HighQuality -PixelStreamingEncoderLatencyMode=LowLatency)
fi
target_bitrate="${SNR_STREAM_TARGET_BITRATE:-}"
if [ -n "$target_bitrate" ]; then
  # Ignores WebRTC congestion control entirely; a last resort for a known link.
  case "$target_bitrate" in
    10000000|15000000|20000000) ;;
    *) echo 'SNR_STREAM_TARGET_BITRATE must be 10000000, 15000000 or 20000000' >&2; exit 2 ;;
  esac
  extra_flags+=("-PixelStreamingEncoderTargetBitrate=$target_bitrate")
fi
cosys_settings="${SNR_COSYS_SETTINGS:-$PWD/world/unreal/cosys-settings.json}"
[[ -f "$cosys_settings" ]] || { echo "Missing Cosys settings: $cosys_settings" >&2; exit 2; }
if [ -n "${SNR_DRONE_DATA_DIR:-}" ]; then
  install -d -m 700 "$SNR_DRONE_DATA_DIR"
  # Engineering capture requests are transient; every new instance starts at 4K.
  rm -f "$SNR_DRONE_DATA_DIR/capture-request.json" "$SNR_DRONE_DATA_DIR/runtime.json"
  extra_flags+=("-DroneFoundationDir=$SNR_DRONE_DATA_DIR")
fi
if [ "${SNR_RHI_THREAD:-1}" = 0 ]; then extra_flags+=(-norhithread); fi
runtime_commands="t.MaxFPS $stream_fps"
if [ "${SNR_PROFILE:-0}" = 1 ]; then runtime_commands+=',PixelStreaming2.LogStats 1'; fi
if [ "${1:-}" = diagnostics ]; then
  extra_flags+=(-ScenarioDiagnostics)
  runtime_commands+=',PixelStreaming2.LogStats 1'
fi
if [ "${SNR_MEDIA_CAPTURE:-1}" = 1 ]; then extra_flags+=(-PixelStreamingUseMediaCapture=true); else extra_flags+=(-PixelStreamingUseMediaCapture=false); fi
scenario_data_dir="${SNR_SCENARIO_DATA_DIR:-$PWD/data/unreal-scenarios}"
install -d -m 700 "$scenario_data_dir"
if [ ! -x "$launcher" ] || [ ! -x "$runtime_binary" ]; then
  echo 'Packaged launcher is missing. Run package-unreal.sh in the Unreal container first.' >&2
  exit 2
fi
install -D -m 644 world/unreal/materials.csv "$HOME/Documents/AirSim/materials.csv"
# Run the packaged binary directly so Docker/systemd termination reaches UE
# and flushes its device-specific pipeline cache during graceful shutdown.
exec "$runtime_binary" ScenarioStudio "$world_map" \
  -RenderOffscreen -unattended -nosound -NoSplash -vulkan -sm5 \
  -savevulkanpsocacheonexit \
  -dpcvars=r.pso.PrecompileThreadPoolSizeMax=2,r.Streaming.PoolSize=4096,r.ScreenPercentage=100 \
  "-ResX=$stream_width" "-ResY=$stream_height" -ForceRes \
  -settings="$cosys_settings" \
  "-ScenarioSaveDir=$scenario_data_dir" \
  "-PixelStreamingConnectionURL=ws://127.0.0.1:$streamer_port" \
  "-PixelStreamingEncoderCodec=$stream_codec" "-PixelStreamingWebRTCFps=$stream_fps" \
  "-PixelStreamingWebRTCStartBitrate=$start_bitrate" "-PixelStreamingWebRTCMinBitrate=$min_bitrate" "-PixelStreamingWebRTCMaxBitrate=$max_bitrate" \
  -PixelStreamingWebRTCDisableTransmitAudio=true \
  -PixelStreamingWebRTCMinPort=49160 -PixelStreamingWebRTCMaxPort=49200 \
  "${extra_flags[@]}" \
  -ExecCmds="$runtime_commands" -stdout -FullStdOutLogOutput
