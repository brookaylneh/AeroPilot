# Streaming image quality

The live profile is **3840×2160 output at a 60 FPS target**, with TSR
at 55% internal resolution (2112×1188). Its startup values are defined in
`deploy/runtime.env.example`; it does not switch quality at runtime. A 301.02-second
four-drone run passed the defined gate: 59.98 decoded FPS, no browser-reported freezes
or callback gaps over 100 ms, and GPU mean/p95/p99 of 8.50/9.15/14.63 ms. Browser
presentation averaged 58.03 FPS. The test used 34.45 Mbps and the whole GPU averaged
236.41 W. Bundled-cache validation, the 30-minute stability run, recording mode and
concurrent-session capacity remain pending.

The package uses Vulkan SM6, Lumen GI and reflections with hardware ray tracing,
volumetric clouds and fog, and scalability level 3. Virtual shadow maps and Nanite
are off. The current grass is temporary and will be replaced separately; the new
assets will require another performance measurement.

H.264 uses NVENC's HighQuality preset and LowLatency mode, with adaptive bitrate
from 10 to 40 Mbps through TURN. The renderer has twelve container CPUs, a 24 GiB
memory limit and a 4096 MB texture streaming pool.

Drone RGB sensors capture native 3840×2160 at fixed EV100 13 and white balance
6500 K, with world GI and reflections, no bloom and no motion blur. The current
experiment explicitly selects 1 Hz per drone; production mission cadence is not
established by this test.

Measurements use a browser on AWS through the public HTTP origin and TURN. They
record other active workloads and do not establish performance on a user's device
or network. Run `scripts/measure-stream.mjs` as documented in [README.md](README.md).
Committed results are in `mission_planner/measurements/render-quality.json`; raw
evidence remains under `.local/evidence/`.
