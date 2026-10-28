"""Bake compact source meshes from completed reductions, then populate firs.

Runtime LOD percentages are relative to the baked source. This avoids compounding
the original high-resolution source reduction at each subsequent LOD.
"""
import json
import math
import random
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[2]
source = json.loads((root / 'evidence/wilderness-import.json').read_text())
ready = set()
for report_path in (root / 'evidence').glob('wilderness-lods*.json'):
    for item in json.loads(report_path.read_text()).get('meshes', []):
        if item['lods'] > 1:
            ready.add(item['path'])
unreal.load_module('StaticMeshEditor')
subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem) or unreal.new_object(unreal.StaticMeshEditorSubsystem)
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
available = []
for index, entry in enumerate(source['models']['fir_tree_01']):
    if entry['path'] not in ready:
        continue
    path = f'/Game/Wilderness/Runtime/SM_Fir_{index}'
    if lib.does_asset_exist(path):
        mesh = unreal.load_asset(path)
    else:
        original = unreal.load_asset(entry['path'])
        dynamic = unreal.DynamicMesh()
        dynamic, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh_v2(original, dynamic,
            unreal.GeometryScriptCopyMeshFromAssetOptions(),
            unreal.GeometryScriptMeshReadLOD(lod_type=unreal.GeometryScriptLODType.RENDER_DATA, lod_index=0),
            use_section_materials=False)
        if dynamic.get_triangle_count() == 0:
            raise RuntimeError(f'No reduced render geometry available: {outcome}')
        mesh, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(dynamic, path,
            unreal.GeometryScriptCreateNewStaticMeshAssetOptions(enable_collision=False))
        if not mesh:
            raise RuntimeError(str(outcome))
        mesh.set_editor_property('static_materials', list(original.get_editor_property('static_materials')))
        subsystem.set_lods(mesh, unreal.StaticMeshReductionOptions(auto_compute_lod_screen_size=False,
            reduction_settings=[unreal.StaticMeshReductionSettings(percent_triangles=f, screen_size=s)
                                for f, s in zip([1., .4, .15, .04], [1., .65, .25, .1])]))
        lib.save_loaded_asset(mesh)
    available.append((index, entry, mesh))
if not available:
    raise RuntimeError('No completed fir reduction is available yet')

levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not levels.load_level('/Game/Wilderness/AlderValley'):
    raise RuntimeError('Wilderness map unavailable')
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
for index in range(3):
    for name in (f'FT_fir_tree_01_{index}', f'FT_RuntimeFir_{index}'):
        ft = unreal.load_asset('/Game/Wilderness/Foliage/' + name)
        if ft:
            unreal.InstancedFoliageActor.remove_all_instances(world, ft)

def height(x, y):
    return (13 * math.exp(-((x - 65) / 40) ** 2 - ((y - 58) / 32) ** 2)
        + max(0, (max(abs(x), abs(y)) - 100) / 60) ** 2 * 22
        + 1.2 * math.sin(x / 29) * math.cos(y / 32) + .6 * math.sin(y / 13 + x / 35)
        + .12 * math.sin(x * .81) * math.sin(y * .69) + .07 * math.sin(x * 1.71 + y * .4))
rng = random.Random(74021)
locations, transforms = [], []
for _ in range(400):
    x, y = rng.uniform(-115, 115), rng.uniform(-105, 110)
    trail = 7 * math.sin(x * .045) + 2 * math.sin(x * .09)
    if abs(y - trail) < 4.5 or ((x + 60) ** 2 + (y - 45) ** 2 < 35 ** 2) or (x > 35 and y > 25):
        continue
    if any(math.hypot(x - a, y - b) < 6 for a, b in locations):
        continue
    locations.append((x, y))
    scale = rng.uniform(.68, 1.06)
    transforms.append(unreal.Transform(location=unreal.Vector(x * 100, y * 100, height(x, y) * 100 - 2),
        rotation=unreal.Rotator(yaw=rng.uniform(0, 360)), scale=unreal.Vector(scale, scale, scale)))

report = {'variants': [], 'instances': len(transforms), 'visuallyAccepted': False}
for group_index, (index, entry, mesh) in enumerate(available):
    name, folder = f'FT_RuntimeFir_{index}', '/Game/Wilderness/Foliage'
    ft = unreal.load_asset(folder + '/' + name) if lib.does_asset_exist(folder + '/' + name) else tools.create_asset(
        name, folder, unreal.FoliageType_InstancedStaticMesh, unreal.FoliageType_InstancedStaticMeshFactory())
    ft.set_editor_property('mesh', mesh)
    ft.set_editor_property('cull_distance', unreal.Int32Interval(min=20000, max=30000))
    lib.save_loaded_asset(ft)
    pivot = unreal.Vector((entry['min'][0] + entry['max'][0]) / 2,
                          (entry['min'][1] + entry['max'][1]) / 2, entry['min'][2])
    group = transforms[group_index::len(available)]
    for item in group:
        item.translation = item.translation * 2 - unreal.MathLibrary.transform_location(item, pivot)
    unreal.InstancedFoliageActor.add_instances(world, ft, group)
    report['variants'].append({'source': entry['path'], 'runtime': mesh.get_path_name(),
        'vertices': [mesh.get_num_vertices(i) for i in range(mesh.get_num_lods())]})
if not levels.save_current_level():
    raise RuntimeError('Could not save runtime foliage')
(root / 'evidence/runtime-foliage.json').write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_RUNTIME_FOLIAGE_READY')
