# Streaming image quality

The live profile is **3840×2160 output at a 60 FPS target**, reconstructed by TSR
from 55% internal resolution (2112×1188). Startup values are defined in
`deploy/runtime.env.example` and installed in `.local/runtime.env`. Quality remains
fixed throughout a session.

The package uses Vulkan SM6, Lumen GI and reflections with hardware ray tracing,
volumetric clouds and fog, and scalability level 3. Virtual shadow maps and Nanite
are disabled. The current grass remains unchanged; its replacement requires a new
performance measurement. The approved Rocketbox characters use their mesh hair.
Moving grass loses fine detail in both native rendered frames and the transmitted
stream. The numerical gate passes; complete visual acceptance of moving grass remains
open alongside the deferred replacement. Source/stream comparison frames are retained
in the measurement record.

H.264 uses NVENC's HighQuality preset and LowLatency mode, with adaptive bitrate
from 10 to 40 Mbps through TURN. The renderer has twelve container CPUs, a 24 GiB
memory limit and a 4096 MB texture streaming pool. Normal serving uses
`SNR_PROFILE=0` to retain writable pipeline caches. The package also contains the
verified bundled cache of 131 graphics, compute and ray-tracing shader entries.

A 300.64-second four-drone run passed every performance gate and completed automatic
landing: 59.99 decoded FPS, 58.32 presented FPS, zero freezes or video-callback gaps
above 100 ms, and GPU mean/p95/p99 of 8.47/9.08/14.59 ms. It averaged 34.62 Mbps and
232.83 W. Restart to the first visible video frame took 8.80 seconds with existing
caches; this is not a cold host reboot measurement.

The 1800.06-second timing window measured 59.99 decoded FPS, 58.21 presented FPS,
zero freezes, and GPU mean/p95/p99 of 8.56/9.48/14.68 ms. Unreal cgroup memory peaked
at 2760.12 MiB and grew by 1123.24 MiB during that instrumented window. The highest sampled capture-worker
resident memory was 716.23 MiB. A longer steady memory plateau is not established.
The measurement record preserves the separate cleanup outcome for each run.

Drone RGB sensors render native 3840×2160 at fixed EV100 13 and white balance
6500 K, with world GI and reflections, no bloom and no motion blur. The measured
workload selects 1 Hz per drone, staggered across four aircraft. In the thirty-minute
window, per-drone mean capture latency was 204–217 ms, including readback, JPEG
encoding and hashing. Production mission cadence is not established by this test.

For recording, set `SNR_STREAM_HEIGHT=1440`, `SNR_STREAM_FPS=30` and
`SNR_RENDER_PERCENTAGE=100` in `.local/runtime.env`, then restart `snr-unreal` with
Studio released. This gives native 2560×1440 at 30 FPS with the same lighting and
sensor features. Restore the live startup values from `deploy/runtime.env.example`
and restart after recording. The recording runs and their durations are listed in
the measurement record; the 30 FPS mode is separate from the 60 FPS live gate.

One independent 1920×1080 session at 60 FPS and 100% internal resolution passed the
full four-drone gate. Two simultaneous instances failed: decoded FPS fell to
38.86 and 38.25, with host user/system CPU averaging 97.63%. Browser clients ran on
the same AWS machine, so their CPU cost is included in this measured capacity.

Measurements use the public HTTP origin and TURN. They do not establish performance
on a user's device or network. Run `scripts/measure-stream.mjs` as documented in
[README.md](README.md). Committed results, artifact hashes and qualifications are in
`mission_planner/measurements/render-quality.json`; raw evidence stays under
`.local/evidence/`.
