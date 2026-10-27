"""Inspect the authored native assets without changing the scene."""
import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level('/Game/OpenMeadow/OpenMeadow')
landscapes = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.Landscape)]
assert len(landscapes) == 1
landscape = landscapes[0]
origin, extent = landscape.get_actor_bounds(False)
components = landscape.get_components_by_class(unreal.LandscapeComponent)
assert len(components) == 256
assert abs(extent.x * 2 - 160934.4) < .1 and abs(extent.y * 2 - 160934.4) < .1
catalog = unreal.load_asset('/Game/OpenMeadow/Catalog')
regions = catalog.get_editor_property('regions')
assert len(regions) == 1 and str(regions[0].get_editor_property('id')) == 'open-meadow'
assert not any(isinstance(a, unreal.InstancedFoliageActor) for a in actors.get_all_level_actors())
path = root / 'evidence/open-meadow-terrain.json'
report = json.loads(path.read_text())
assert str(catalog.get_editor_property('asset_version')) == report['assetVersion']
assert report['assetVersion'] in ('open-meadow-unreal-0.2.0', 'open-meadow-unreal-0.3.0')
assert report['widthMetres'] == report['heightMetres'] == 1609.344
for key in ('min', 'max'):
    v = regions[0].get_editor_property(key)
    assert all(abs(actual - expected) < .01 for actual, expected in zip((v.x, v.y, v.z), report['boundsCm'][key]))
report.update(nativeExtentCm=[extent.x * 2, extent.y * 2, extent.z * 2], nativeGeometryVerified=True,
    landscapeComponents=len(components), nativeLocationCount=len(regions), actorLabels=[a.get_actor_label() for a in actors.get_all_level_actors()])
path.write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_OPEN_MEADOW_NATIVE_GEOMETRY_VERIFIED')
