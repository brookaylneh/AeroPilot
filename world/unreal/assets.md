# Asset dependencies

The repository contains first-party source and OFL fonts. It excludes all Unreal asset binaries and third-party source trees. Asset references and hashes are metadata, not redistribution permission.

| Dependency                                             | Acquisition / terms                                                                                                                                                                                                                                         |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Unreal Engine 5.8.2                                    | Obtain through Epic with an account authorized under the applicable Unreal terms. Do not publish the engine or signed download links.                                                                                                                       |
| Cosys-AirSim 3.5.0                                     | Pinned official `5.8-v3.5.0` release in `world/unreal/dependencies.lock.json`; MIT source with its third-party notices.                                                                                                                                     |
| Pixel Streaming 2 infrastructure                       | Pinned Epic commit in the same lock file; dependency licenses remain applicable.                                                                                                                                                                            |
| MetaHuman Kelvin and additional installed Epic presets | Obtain/assemble using Epic tools and the applicable MetaHuman/Unreal terms. No MetaHuman assets are included in this repository. Future AI evaluation rights remain unresolved.                                                                             |
| Five child scans by Leopold2026                        | Acquire from the Fab listing in `world/unreal/children-assets.json` under the displayed Fab Standard License. Source scans have no rigs or animations; the project authors those separately. Raw and derived asset files are excluded from this repository. |
| Poly Haven textures and meshes                         | CC0 sources and checksums are recorded in the asset lock files.                                                                                                                                                                                             |
| DM Sans and Manrope                                    | SIL Open Font License. Exported font files retain their adjacent license files.                                                                                                                                                                             |
| Character reference photographs                        | Selected FLUX Ultra images retained as visual references. Provenance and hashes are in `mission_planner/measurements/character-references.json`. [MetaHuman authoring](characters/README.md) uses Epic presets and explicit settings.                       |
| Original terrain, daypack and integration code         | Project-authored; the owner still needs to select a project source license before public release.                                                                                                                                                           |

Rendering permission does not establish permission for training or evaluating AI. This file records dependency provenance and the current unresolved decision; it is not a new legal assessment. Do not infer AI-evaluation clearance from the current rendering demo.

The authored landscape, assembled character and vegetation binaries are held privately with the deployment. Rebuilding the same world from this repository requires those dependencies and the documented authoring sequence; the repository is not a complete photorealistic content distribution.

### Character selection thumbnails

`web/public/portraits/` contains small final renders captured from the nine Unreal
presets, cropped for the character tabs. They contain no source meshes, source
textures, private scenario locations or EXIF metadata. The original character
licenses above still govern their underlying assets. The briefing no longer
displays or generates a missing-person portrait.
