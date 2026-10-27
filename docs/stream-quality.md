# Streaming image quality

The packaged launcher renders at **100% internal resolution**. A 1920 × 1080
stream therefore starts with a native 1920 × 1080 render. UE 5.8's automatic
desktop resolution previously rendered this viewport at 1400 × 788 and used
Temporal Super Resolution (TSR) to reconstruct the delivered image. The output
video dimensions alone did not reveal that difference.

TSR remains enabled for antialiasing. Motion blur remains disabled. Texture
streaming retains its 4096 MB budget; increase this only after measuring actual
pool pressure with `DumpTextureStreamingStats`.

## L4 profile

- H.264 through NVENC, 1920 × 1080, 60 FPS target.
- `HighQuality` encoding preset with the `LowLatency` encoder mode. The value
  must be the exact enum name; an unrecognized latency value fails an engine
  assertion when the encoder starts.
- No periodic keyframes; the browser requests one when it needs it.
- Adaptive bitrate: 10 Mbps minimum (`SNR_STREAM_MIN_BITRATE`), starting at the
  25 Mbps maximum (`SNR_STREAM_MAX_BITRATE`).
- No fixed encoder bitrate. `SNR_STREAM_TARGET_BITRATE` overrides congestion
  control entirely and is reserved for a known, stable link.

A packet capture of a real Wi-Fi viewer (53 Mbps download, 10 ms idle and 136 ms
loaded latency) showed WebRTC's delay-based estimate holding the previous
profile at 1–3 Mbps while moving: 2–6 KB frames, 10–15 KB periodic keyframes, and
a loss burst at about 2.8 Mbps. The floor keeps moving grass encodable; the lower
ceiling leaves headroom so the stream does not fill the viewer's Wi-Fi queue.
A floor above a viewer's real capacity causes frame drops, so lower it for
slower clients. The same-host 20-second movement check at this profile delivered
59.79 FPS, 18.8 Mbps, no drops or freezes.

The H.264 encoder's Baseline profile already enables CABAC and the adaptive 8×8
transform, so High profile adds little. AV1 is accepted by the launcher and the
browser build but is not the default. The TURN relay limits each session with
coturn's `max-bps`, which is in bytes per second; 6,250,000 allows 50 Mbps.
This profile changes the browser stream; drone-camera capture resolution and the
world/scenario data are independent.

## Verify changes

Close other editing tabs, checkpoint the current scenario, and run against a
packaged instance with exclusive camera access:

```sh
SNR_NATIVE_URL=http://127.0.0.1:5173/unreal \
SNR_QUALITY_OUTPUT=evidence/quality-check \
SNR_QUALITY_ROUNDS=20 \
node scripts/check-stream-quality.mjs
```

The test traverses the map with fast movement and abrupt turns, stops to capture
settled frames, and inspects the person from ground, close, and elevated views.
It records full-resolution PNG frames, per-second WebRTC statistics, camera
timings, compression quantization, bitrate, packet loss, and resolution changes.
It checks that the authoring state is unchanged and requires at least 55.2
decoded FPS, no freezes, and a constant 1920 × 1080 stream. Inspect the images as
well as the numbers. Reports and screenshots contain private scenario data.

Use `stat tsr` on the private simulator console to verify **internal** render
resolution; turn it off after inspection. Compare direct Unreal screenshots with
decoded browser frames to distinguish rendering artifacts from compression.
Never expose console execution through the website.

Server-side and cloud-browser tests do not measure a user's home connection or
display. A stable GPU frame rate does not exclude packet loss, client decoding
limits, or bandwidth adaptation. Native rendering reduces temporal reconstruction
work but cannot eliminate every vegetation or antialiasing artifact.

## Resolution and CPU capacity

On the L4, native 1080p sustained a 16.67 ms engine frame during a 333-second
navigation run. GPU time averaged 9.01 ms (95th percentile 11.42 ms). The receiving
cloud browser decoded 58.37 FPS at the 40 Mbps ceiling. Its decoder averaged
14.41 ms per frame. A separate 30 Mbps run delivered 59.70 FPS over 62 seconds,
with greater compression. These browser results do not establish the performance
of a user's device or hardware video decoder.

Short character-view motion tests delivered genuine frames at each resolution:

| Native resolution | Mean GPU render time | Browser decoded FPS | Mean browser decode time |
| --- | ---: | ---: | ---: |
| 1920 × 1080 | 14.44 ms | 59.21 | 15.34 ms |
| 2560 × 1440 | 22.38 ms | 41.05 | 23.96 ms |
| 3840 × 2160 | 43.80 ms | 17.29 | 57.15 ms |

These 15-second samples used the same camera-turn routine, with a 40 Mbps ceiling
at 1080p/1440p and 60 Mbps at 4K. The game thread averaged 2.43–3.20 ms. GPU use
reached 98–99% above 1080p. Both higher resolutions failed the 60 FPS gate. The
live profile remains native 1080p/60.

NVENC utilization was 7–9% in the first 1440p test. Its driver-reported latency
was inconsistent and is not used as an encoding-time measurement. Unreal's stock
latency report returned encode times of 8.62, 9.85, and 23.78 ms respectively;
each value remained unchanged throughout its sample. Treat these as reported
snapshots, not a verified distribution of per-frame encoding latency. The
performance script records this interface alongside browser and engine timings.

Additional server CPU does not address this measured GPU limit or decoding on
the user's device. The deployed application has a six-vCPU container limit;
resizing the VM alone would not change that limit. The resolution-test host
samples averaged about three CPU cores of application use, with less than 0.01 s
of CPU quota throttling over 109 seconds. Benchmark higher resolutions with the
intended client before changing the default. A 4K drone still image is independent
of a sustained 4K browser stream.

The long test retained its failed result because its original ICE-route sampler
raised an instrumentation error. The sampler now uses the transport's selected
candidate pair. Subsequent shorter tests passed. Do not interpret the long run's
performance measurements as a passing end-to-end test.

## References

- [Epic: TSR accumulation and internal resolution](https://dev.epicgames.com/documentation/en-us/unreal-engine/temporal-super-resolution-in-unreal-engine)
- [Epic: texture streaming metrics](https://dev.epicgames.com/documentation/en-us/unreal-engine/texture-streaming-metrics-in-unreal-engine)
- [Epic: Pixel Streaming 2 settings migration](https://github.com/EpicGames/PixelStreamingInfrastructure/blob/master/Docs/pixel-streaming-2-migration-guide.md)

Launch parameters were also checked against the installed UE 5.8.2 source;
several settings in older Pixel Streaming guides do not apply to Pixel Streaming 2.
