"""Use the asset artist's leaf-card meshes, preserving existing world transforms."""
import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
exports = json.loads((root / 'evidence/fir-export.json').read_text())
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
unreal.load_module('StaticMeshEditor')
subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem) or unreal.new_object(unreal.StaticMeshEditorSubsystem)
imported = {}
for entry in exports:
    name = Path(entry['path']).stem
    folder = '/Game/Wilderness/AuthoredFoliage'
    options = unreal.FbxImportUI()
    for key, value in {'automated_import_should_detect_type': False,
                       'mesh_type_to_import': unreal.FBXImportType.FBXIT_STATIC_MESH,
                       'import_mesh': True, 'import_as_skeletal': False,
                       'import_materials': False, 'import_textures': False}.items():
        options.set_editor_property(key, value)
    for key, value in {'combine_meshes': True, 'auto_generate_collision': False,
                       'generate_lightmap_u_vs': False}.items():
        options.static_mesh_import_data.set_editor_property(key, value)
    task = unreal.AssetImportTask()
    for key, value in {'filename': str(root / entry['path']), 'destination_path': folder,
                       'destination_name': name, 'automated': True, 'replace_existing': True,
                       'save': True, 'options': options, 'factory': unreal.FbxFactory()}.items():
        task.set_editor_property(key, value)
    tools.import_asset_tasks([task])
    mesh = unreal.load_asset(folder + '/' + name)
    if not mesh:
        raise RuntimeError(f'Missing imported foliage: {name}')
    for index, slot in enumerate(mesh.get_editor_property('static_materials')):
        suffix = str(slot.material_slot_name)
        if suffix == 'fir_tree_01_dead_branches':
            suffix = 'fir_tree_01_bark'
        material = unreal.load_asset('/Game/Wilderness/Materials/M_' + suffix)
        if not material:
            raise RuntimeError(f'Missing foliage material: {suffix}')
        mesh.set_material(index, material)
    lib.save_loaded_asset(mesh)
    imported[(entry['variant'], entry['lod'])] = mesh
report = []
for index, variant in enumerate('abc'):
    destination = unreal.load_asset(f'/Game/Wilderness/Runtime/SM_Fir_{index}')
    subsystem.remove_lods(destination)
    source = imported[(variant, 1)]
    destination.set_editor_property('static_materials', list(source.get_editor_property('static_materials')))
    for lod in (0, 1):
        result = subsystem.set_lod_from_static_mesh(destination, lod, imported[(variant, lod + 1)], 0, True)
        if result != lod:
            raise RuntimeError(f'Could not install authored LOD {variant}:{lod}')
    subsystem.set_lod_screen_sizes(destination, [1., .18])
    lib.set_metadata_tag(destination, 'ScenarioFoliageSource', 'PolyHavenArtistLODs-v1')
    lib.save_loaded_asset(destination)
    report.append({'mesh': destination.get_path_name(), 'vertices': [destination.get_num_vertices(i) for i in range(destination.get_num_lods())],
                   'bounds': str(destination.get_bounding_box()), 'visuallyAccepted': False})
(root / 'evidence/authored-fir-lods.json').write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_AUTHORED_FIR_LODS_INSTALLED')
