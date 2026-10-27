# Wildlands Scenario Studio

Browser authoring for an Unreal wilderness simulation, with four stock Cosys-AirSim quadcopters and deterministic engineering tests.

## Current capabilities

- A 1,609.344 × 1,609.344 m landscape with gentle hills and meadow vegetation.
- Nine people: five scanned children, two adult MetaHumans and two senior MetaHumans, with clothing colors and supported accessories.
- Live character and situation previews, full-world double-click placement, and an editable rescue briefing with an actual Unreal portrait.
- Pixel Streaming 2 navigation, a minimap, and exact JSON scenario save/load.
- Four independently addressable SimpleFlight aircraft on a 14 × 14 m slab outside the southern world boundary.
- Configurable downward/oblique RGB capture at 1080p, 4K and 8K; measured flight and capture tests.

The creator ends at the launch button; no mission is dispatched. Private scenario truth and reported rescue information use separate files and API schemas. Four children support all three situations; the fifth retains a fixed skateboard and supports standing situations only. Clothing customization changes colors, not garment meshes. See the [validation report](docs/scenario-creator.md#packaged-validation--2026-10-27) for tested behavior and remaining visual limitations. Sustained 1080p60 depends on the host and scene.

## Start here

- [Setup and deployment](docs/setup.md)
- [Architecture and coordinate conventions](docs/architecture.md)
- [Scenario creator, character preparation and validation](docs/scenario-creator.md)
- [Asset dependencies and distribution](docs/asset-dependencies.md)
- [Preparing the public repository](docs/public-source.md)
- [Contributing](CONTRIBUTING.md)

The public source export excludes Unreal Engine, MetaHuman/Fab content, packaged releases, private scenarios, credentials and deployment evidence. A source checkout alone cannot reproduce the photorealistic scene without separately acquired assets. No project source license has been selected yet.

## Local browser preview

Use Node 22.12+ within the Node 22 release line and Python 3.10+.

```sh
npm ci
npm run assets
npm run build
npm start
```

Open **http://127.0.0.1:3001** for the small Three.js development preview. Its procedural people, wardrobe and terrain are separate from the Unreal scene. The real packaged scene is at **/unreal** and requires the runtime setup described above.

```sh
npm test
npm run typecheck
npm run format:check
```

Only one browser may edit a given Unreal instance. Keep native RPC and signalling ports on loopback. The supplied web application has session ownership, not account authentication; use access control before making a deployment generally accessible.
