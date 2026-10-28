"""Add scaled ground cover to the meadow and trail margins using licensed meshes."""
import json
import math
import random
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[2]
source = json.loads((root / '.local/evidence/wilderness-import.json').read_text())
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not levels.load_level('/Game/Wilderness/AlderValley'):
    raise RuntimeError('Wilderness map unavailable')
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
def height(x, y):
    return (13 * math.exp(-((x - 65) / 40) ** 2 - ((y - 58) / 32) ** 2)
        + max(0, (max(abs(x), abs(y)) - 100) / 60) ** 2 * 22
        + 1.2 * math.sin(x / 29) * math.cos(y / 32) + .6 * math.sin(y / 13 + x / 35)
        + .12 * math.sin(x * .81) * math.sin(y * .69) + .07 * math.sin(x * 1.71 + y * .4))
rng = random.Random(74022)
report = {}
for asset, count in [('grass_medium_01', 75000), ('fern_02', 2400)]:
    choices = source['models'][asset]
    choices = [m for m in choices if 'LOD2' in m['path']] or choices
    groups = [[] for _ in choices]
    for i in range(count):
        if asset == 'grass_medium_01' and i < 52000:
            x, y = rng.uniform(-90, -25), rng.uniform(15, 80)
            scale = rng.uniform(1.0, 1.7)
        else:
            x, y = rng.uniform(-38, 35), rng.uniform(-36, 22)
            scale = rng.uniform(.8, 1.4)
        trail = 7 * math.sin(x * .045) + 2 * math.sin(x * .09)
        if abs(y - trail) < 1.8 or (math.sin(x * .8) * math.sin(y * .6) > .7):
            continue
        group_index = i % len(choices)
        entry = choices[group_index]
        pivot = unreal.Vector((entry['min'][0] + entry['max'][0]) / 2,
                              (entry['min'][1] + entry['max'][1]) / 2, entry['min'][2])
        transform = unreal.Transform(location=unreal.Vector(x * 100, y * 100, height(x, y) * 100 - 1),
            rotation=unreal.Rotator(yaw=rng.uniform(0, 360)), scale=unreal.Vector(scale, scale, scale))
        transform.translation = transform.translation * 2 - unreal.MathLibrary.transform_location(transform, pivot)
        groups[group_index].append(transform)
    for index, (entry, group) in enumerate(zip(choices, groups)):
        name, folder = f'FT_Cover_{asset}_{index}', '/Game/Wilderness/Foliage'
        ft = unreal.load_asset(folder + '/' + name) if lib.does_asset_exist(folder + '/' + name) else tools.create_asset(
            name, folder, unreal.FoliageType_InstancedStaticMesh, unreal.FoliageType_InstancedStaticMeshFactory())
        unreal.InstancedFoliageActor.remove_all_instances(world, ft)
        ft.set_editor_property('mesh', unreal.load_asset(entry['path']))
        ft.set_editor_property('cull_distance', unreal.Int32Interval(min=7500, max=11000))
        lib.save_loaded_asset(ft)
        unreal.InstancedFoliageActor.add_instances(world, ft, group)
    report[asset] = sum(map(len, groups))
if not levels.save_current_level():
    raise RuntimeError('Ground cover could not be saved')
(root / '.local/evidence/ground-cover.json').write_text(json.dumps({'counts': report, 'seed': 74022, 'visuallyAccepted': False}, indent=2) + '\n')
unreal.log('SNR_GROUND_COVER_SAVED')
