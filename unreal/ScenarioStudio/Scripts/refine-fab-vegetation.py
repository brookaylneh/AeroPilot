"""Bound grass rendering cost and show the vegetation in the opening view."""
import json
import math
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
lib = unreal.EditorAssetLibrary
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
assert levels.load_level('/Game/OpenMeadow/OpenMeadow')
settings = {
    'LowMeadow': ([420, 320], 5500, 9000, False),
    'DetailedGrass': ([220, 140], 3500, 6000, True),
    'GrassPatches': ([100], 4000, 7000, True),
    'FlowerPatchesA': ([150, 120], 4000, 6500, True),
    'FlowerPatchesB': ([90, 120], 4000, 6500, True),
}
report_path = root / 'evidence/fab-vegetation-integration.json'
report = json.loads(report_path.read_text())
for group in report['groups']:
    name = group['name']
    densities, near, far, shadows = settings[name]
    grass_type = unreal.load_asset('/Game/OpenMeadow/Vegetation030/GT_' + name)
    varieties = list(grass_type.get_editor_property('grass_varieties'))
    assert len(varieties) == len(densities)
    for index, variety in enumerate(varieties):
        variety.set_editor_property('grass_density', unreal.PerPlatformFloat(default=densities[index]))
        variety.set_editor_property('start_cull_distance', unreal.PerPlatformInt(default=near))
        variety.set_editor_property('end_cull_distance', unreal.PerPlatformInt(default=far))
        variety.set_editor_property('cast_dynamic_shadow', shadows)
        variety.set_editor_property('cast_contact_shadow', shadows)
        group['varieties'][index]['density'] = densities[index]
    grass_type.set_editor_property('grass_varieties', varieties)
    lib.save_loaded_asset(grass_type)
    group['cullDistanceCm'] = [near, far]
    group['castDynamicShadow'] = shadows

catalog = unreal.load_asset('/Game/OpenMeadow/Catalog')
regions = list(catalog.get_editor_property('regions'))
assert len(regions) == 1
target = unreal.Vector(0, 0, 1100)
pitch, yaw, distance = math.radians(18), math.radians(-135), 4500
location = target + unreal.Vector(math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)) * distance
regions[0].set_editor_property('preview_target_cm', target)
regions[0].set_editor_property('preview_camera', unreal.Transform(location=location))
catalog.set_editor_property('regions', regions)
lib.save_loaded_asset(catalog)
assert levels.save_current_level()
report['previewDistanceMetres'] = 45
report['refined'] = True
report_path.write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_FAB_VEGETATION_REFINED')
