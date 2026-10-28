# Generated character sources

Generate and inspect reference images, then inspect textured 3D sources before MetaHuman authoring.
The roster describes fictional people; age and ancestry are prompt instructions, not verified properties
of generated output. It contains four children (minimum age eight), four adults and four seniors.

## Inference

Run from the project root with Python 3.10 and the shared environment loader:

```sh
.venv-cosys/bin/python -m world.unreal.characters.generate image adult-01 \
  .local/evidence/characters/<image-run>/adult-01
.venv-cosys/bin/python -m world.unreal.characters.generate mesh adult-01 \
  .local/evidence/characters/<mesh-run>/adult-01 \
  --image-run .local/evidence/characters/<image-run>/adult-01
```

Outputs must be new directories. Requests, provider responses, status logs, original media, SHA-256
checksums and client versions are preserved. HTTP failures raise; queue retries and provider model
fallbacks are disabled explicitly. Polling observes the same request without submitting it again.
These are hosted inference endpoints: GPU type, model weights and container digest are not exposed.
This workflow creates no deployments or task-owned runners.

`roster.json` defines the models and settings once. FLUX Ultra provides approximately four
megapixels; PNG avoids an additional JPEG encode. The pose-first prompt requests separate hands and
feet, frontal framing and soft neutral lighting. Standard and raw modes must be judged by output;
raw mode is not an assurance of better anatomy or prompt adherence.

Hunyuan Pro requests textured Normal output, PBR and the maximum exposed target of 1,500,000 faces.
Actual faces, texture dimensions and material channels must be inspected in the returned GLB.
The API does not expose texture resolution, diffusion steps, guidance or a GPU selector.
One front image leaves the rear surface inferred. Additional views help only when they depict the
same geometry and identity; independently invented side views must not be treated as ground truth.

## Review

Reject cropped bodies, fused fingers, extra limbs, implausible proportions and severe skin or fabric
artifacts. Review full-resolution faces and hands, then front, side and back geometry under neutral
lighting. PBR maps and a high face count do not establish anatomical or material accuracy.

A selection JSON contains one entry per roster ID, with required fields
`id`, `image_run`, `mesh_run` (null before 3D), and `review`.
Publish a new gallery into the existing web build:

```sh
.venv-cosys/bin/python -m world.unreal.characters.review \
  .local/evidence/characters/<selection>.json character-review-v1
```

Only the selected image and model files are published. No evidence directories, request logs,
credentials or scenario data are served. The viewer dependency is version-pinned and hash-checked.
A web rebuild removes the gallery; regenerate it using the saved selection. No service restart or
new listener is needed.

## Geometry inventory and screenshots

```sh
.venv-cosys/bin/python -m world.unreal.characters.inspect_mesh \
  .local/evidence/characters/<mesh-run>/adult-01/model.glb \
  .local/evidence/characters/<mesh-run>/adult-01/mesh-audit.json
node world/unreal/characters/validate-mesh.mjs \
  .local/evidence/characters/<mesh-run>/adult-01/model.glb \
  .local/evidence/characters/<mesh-run>/adult-01/gltf-validation.json
node --env-file=.local/runtime.env world/unreal/characters/capture-review.mjs \
  http://35.81.229.78/character-review-v1/index.html \
  .local/evidence/characters/<new-screenshot-run>
```

The GLB inventory checks binary bounds, finite vertex data, indices, normals, UVs and embedded images.
It records face counts and material texture references; it is not a full glTF conformance validator
or a proof of good anatomy. The separate validator uses the pinned Khronos glTF validator from
`package-lock.json` and retains errors and warnings before failing on invalid assets. Generated physical stature must be calibrated before importing into
Unreal centimetres. The browser capture records front, side, back and face views through the installed
HTTP service and releases its browser when finished. It never connects to the Studio editing lease.

## MetaHuman

UE 5.8's From Custom Mesh tool supports custom topology and AI-generated human meshes. Its solver
fits the input surface, including clothing. Loose clothes distort the inferred body shape. Hair,
garments, skin shading, eyes and deformation still need authoring and acceptance; the generated GLB
is not a finished MetaHuman. Children require individual proportion and deformation checks.
Do not import, conform or replace live characters before the user has reviewed the images and meshes.

Sources:

- [FLUX Ultra schema](https://fal.ai/models/fal-ai/flux-pro/v1.1-ultra/api)
- [Hunyuan Pro schema](https://fal.ai/models/fal-ai/hunyuan-3d/v3.1/pro/image-to-3d/api)
- [fal request reliability controls](https://fal.ai/docs/documentation/model-apis/inference/reliability)
- [Epic From Custom Mesh](https://dev.epicgames.com/documentation/metahuman/metahuman-creator-from-custom-mesh-tool-in-unreal-engine)
- [model-viewer](https://modelviewer.dev/)
