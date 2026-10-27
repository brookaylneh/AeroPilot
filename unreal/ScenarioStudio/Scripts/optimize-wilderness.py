"""Generate render LODs for the source meshes; retain original source geometry."""
import json
import os
from pathlib import Path
import unreal
unreal.load_module('StaticMeshEditor')
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
source = json.loads((root / 'evidence/wilderness-import.json').read_text())
subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
if subsystem is None:
    subsystem = unreal.new_object(unreal.StaticMeshEditorSubsystem)
lib = unreal.EditorAssetLibrary
report = {'meshes': [], 'visuallyAccepted': False}
selected = os.environ.get('SNR_LOD_FILTER', '')
report_path = root / ('evidence/wilderness-lods' + selected + '.json')
for asset, entries in source['models'].items():
    if asset not in ('fir_tree_01', 'pine_sapling_small'):
        continue
    fractions = [.1, .025, .006, .0015] if asset == 'fir_tree_01' else [.5, .15, .04, .01]
    for entry in entries:
        if selected and selected not in entry['path']:
            continue
        mesh = unreal.load_asset(entry['path'])
        if mesh.get_num_lods() == 1:
            options = unreal.StaticMeshReductionOptions(auto_compute_lod_screen_size=False,
                reduction_settings=[unreal.StaticMeshReductionSettings(percent_triangles=f, screen_size=s)
                                    for f, s in zip(fractions, [1.0, .65, .25, .10])])
            if subsystem.set_lods(mesh, options) != 4:
                raise RuntimeError('Could not create LODs: ' + entry['path'])
            lib.save_loaded_asset(mesh)
        report['meshes'].append({'path': entry['path'], 'lods': mesh.get_num_lods(),
                                'vertices': [mesh.get_num_vertices(i) for i in range(mesh.get_num_lods())]})
        report_path.write_text(json.dumps(report, indent=2) + '\n')
if not selected:
    terrain = unreal.load_asset('/Game/Wilderness/SM_AlderTerrain')
    ground = unreal.load_asset('/Game/Wilderness/Materials/M_AlderGround')
    report['terrainMaterialBefore'] = str(terrain.get_material(0))
    if not terrain.get_material(0):
        terrain.set_editor_property('static_materials', [unreal.StaticMaterial(material_interface=ground, material_slot_name='Ground')])
        lib.save_loaded_asset(terrain)
    report['terrainMaterialAfter'] = str(terrain.get_material(0))
    (root / 'evidence/wilderness-lods.json').write_text(json.dumps(report, indent=2) + '\n')
    unreal.log('SNR_WILDERNESS_LODS_READY')

unreal.log('SNR_SELECTED_WILDERNESS_LODS_READY')
