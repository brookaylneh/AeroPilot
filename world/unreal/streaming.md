# Streaming image quality

The live profile is native **3840×2160 at a 60 FPS target**, configured in
`deploy/runtime.env.example`. Changes take effect when `snr-unreal` starts; the
application does not switch quality profiles while running.

- H.264 through NVENC, `HighQuality` preset and `LowLatency` mode.
- 100% internal render resolution with TSR antialiasing; motion blur is disabled.
- Adaptive bitrate from 10 to 40 Mbps; the encoder starts at the ceiling.
- No fixed encoder bitrate and no periodic keyframes. The browser can request a keyframe.
- Browser media uses TURN. Its configured 6,250,000 bytes/s limit allows 50 Mbps.
- Epic scalability level 3, a 4096 MB texture streaming pool and six container CPUs.

The current package uses `SF_VULKAN_SM5`, screen-space GI and screen-space reflections.
Hardware ray tracing and virtual shadow maps are off; Nanite assets use fallback meshes
on this platform. Both UE 5.8.2 SM6 device profiles pass on the RTX PRO 6000, but enabling
Lumen and hardware ray tracing requires an SM6 cook with the complete uncooked assets.
The current recovery release uses explicitly approved temporary cooked content.

The five-minute navigation run delivered 59.99 decoded FPS at 35.04 Mbps with no dropped
frames or browser-reported freezes. The largest video callback gap was 99.9 ms. Sampled
GPU time averaged 8.62 ms, with p95 9.33 ms and p99 10.22 ms. These are camera telemetry
samples at a target 10 Hz, not an exhaustive per-frame trace.

A 61-second close-character run delivered 60.01 decoded FPS at 34.77 Mbps, with no dropped
frames or browser-reported freezes and a largest video callback gap of 50 ms. Sampled GPU
time averaged 10.25 ms, with p95 10.55 ms and p99 10.70 ms. GPU power averaged 296.28 W;
the highest sampled temperature was 45 °C.

These measurements used a browser on AWS through the public HTTP origin and TURN. They do
not establish performance on a user's device or network, or across every character.
Four-drone flight/capture load and the complete realism gate remain unmeasured.
Drone-camera capture resolution is independent of the browser stream.

Run reproducible measurements with `scripts/measure-stream.mjs`, as documented in
[README.md](README.md). It records browser statistics, native timing samples, NVIDIA
power/clocks/temperature and native-resolution screenshots. Committed results are in
`mission_planner/measurements/render-quality.json`; raw evidence remains under `.local/evidence/`.

## References

- [Epic: temporal super resolution](https://dev.epicgames.com/documentation/en-us/unreal-engine/temporal-super-resolution-in-unreal-engine)
- [Epic: Pixel Streaming 2 settings](https://github.com/EpicGames/PixelStreamingInfrastructure/blob/master/Docs/pixel-streaming-2-migration-guide.md)
- The installed UE 5.8.2 `VulkanPC/DataDrivenPlatformInfo.ini` defines SM5/SM6 feature support.
