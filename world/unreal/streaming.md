# Streaming image quality

The live profile is **3840×2160 output at a 60 FPS target**, reconstructed by TSR
from 55% internal resolution (2112×1188). Startup values are defined in
`deploy/runtime.env.example` and installed in `.local/runtime.env`. Quality remains
fixed throughout a session.

The package uses Vulkan SM6, Lumen GI and reflections with hardware ray tracing,
virtual shadow maps, Nanite including foliage, and runtime-generated grass. The
approved Rocketbox characters use their mesh hair.

H.264 uses NVENC's HighQuality preset and LowLatency mode, with adaptive bitrate
from 10 to 40 Mbps through TURN. The renderer has twelve container CPUs, a 24 GiB
memory limit and a 4096 MB texture streaming pool. Normal serving uses
`SNR_PROFILE=0` to retain writable pipeline caches. The package contains the
bundled pipeline cache recorded from its own Content.

Drone RGB sensors render native 3840×2160 with Lumen GI and reflections, manual
exposure from `world/unreal/valley-lighting.json`, white balance 6500 K, no bloom
and no motion blur.

For recording, set `SNR_STREAM_HEIGHT=1440`, `SNR_STREAM_FPS=30` and
`SNR_RENDER_PERCENTAGE=100` in `.local/runtime.env`, then restart `snr-unreal` with
Studio released. This gives native 2560×1440 at 30 FPS with the same lighting and
sensor features. Restore the live startup values from `deploy/runtime.env.example`
and restart after recording. The 30 FPS mode is separate from the 60 FPS live gate.

Measurements use the public HTTP origin and TURN. They do not establish performance
on a user's device or network. Committed results are in
`mission_planner/measurements/integration.json`; raw evidence stays under `.local/evidence/`.
