# Perception geometry and model benchmarks

Offline helpers for the nadir calibration frames under ignored `.local/data/calibration/` and for
provider benchmarks. Live missions never import this package.

| Module             | Purpose                                                                                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `geometry.py`      | `Camera`: world/NED frames, projection, terrain-ray ground points, round-trip checks                                                             |
| `configuration.py` | Pinned benchmark image transforms (1920×1080 Lanczos, 512 px crops, JPEG 95, no subsampling); operating point from `measurements/commander.json` |
| `analyst.py`       | The `analyst-v1` nadir two-image template used by the Modal qualification benchmark                                                              |
| `bench_models.py`  | Explicit timing cases against the warm Servers and Wonder; raw responses stay in `.local/evidence/models/`                                       |

World points are metres with +X north, +Y east and +Z up. `Camera` asserts the world convention,
pose agreement, normalized quaternions, 16:9 and the recorded horizontal field of view. Terrain-ray
intersection uses the heightfield with a 5 mm vertical residual limit and a bounded convergence
loop; every frame checks a 9-by-5 pixel grid round trip against 0.5 px.

`bench_models --case glimmer_frame_2160` measures the current full-frame Glimmer review on one saved
4K image. Its selected box comes from a separate SAM image request; it does not measure live SAM 3.1
tracking or recognition accuracy. The `sam_*` cases measure the standalone image endpoint and the
`wonder_*` cases measure Wonder request latency and token usage; every case writes a new output file.

Sources: [SAM 3 paper](https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/),
the [pinned postprocessor](https://github.com/facebookresearch/sam3/blob/f1647e8d0c1d280991f4d1b7d974c2704537fe66/sam3/eval/postprocessors.py)
(presence-head product scores, strict threshold filtering), [Cosys core APIs](https://cosys-lab.github.io/Cosys-AirSim/apis/)
and [Meta Glimmer prompting](https://dev.meta.ai/docs/muse-glimmer/prompting).
