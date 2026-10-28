"""Import the pinned Poly Haven assets into cooked runtime asset paths."""
import json
from pathlib import Path
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
SOURCE = ROOT / 'vendor/polyhaven'
BASE = '/Game/Wilderness/Assets'
MODELS = ['fir_tree_01', 'pine_sapling_small', 'fern_02', 'grass_medium_01', 'rock_moss_set_01']
TERRAIN = ['forest_ground_04', 'rocky_terrain_02', 'aerial_grass_rock']
tools = unreal.AssetToolsHelpers.get_asset_tools()
library = unreal.EditorAssetLibrary
materials = unreal.MaterialEditingLibrary
report = {'models': {}, 'textures': {}, 'materials': {}}


def checkpoint():
    (ROOT / 'evidence/wilderness-import.json').write_text(json.dumps(report, indent=2) + '\n')


def import_file(source, destination, options=None):
    task = unreal.AssetImportTask()
    task.filename = str(source)
    task.destination_path = destination
    task.automated = True
    task.replace_existing = False
    task.save = True
    if options:
        task.options = options
        task.factory = unreal.FbxFactory()
    tools.import_asset_tasks([task])
    paths = list(task.imported_object_paths)
    if not paths:
        raise RuntimeError(f'Import produced no assets: {source.name}')
    return paths


for asset in MODELS + TERRAIN:
    folder = SOURCE / asset
    destination = f'{BASE}/{asset}'
    texture_sources = sorted(p for p in folder.rglob('*') if p.suffix.lower() in ('.png', '.jpg', '.exr'))
    textures = {}
    for source in texture_sources:
        path = f'{destination}/{source.stem}'
        texture = unreal.load_asset(path) if library.does_asset_exist(path) else None
        if texture is None:
            path = import_file(source, destination)[0]
            texture = unreal.load_asset(path)
        is_normal = '_nor_' in source.stem
        texture.set_editor_property('srgb', '_diff_' in source.stem)
        if is_normal:
            texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP)
            texture.set_editor_property('flip_green_channel', '_nor_gl_' in source.stem)
        library.save_loaded_asset(texture)
        textures[source.stem] = path
    report['textures'][asset] = textures

    if asset in MODELS:
        existing = [p for p in library.list_assets(destination, recursive=False)
                    if isinstance(unreal.load_asset(p), unreal.StaticMesh)]
        if not existing:
            options = unreal.FbxImportUI()
            options.set_editor_property('automated_import_should_detect_type', False)
            options.set_editor_property('mesh_type_to_import', unreal.FBXImportType.FBXIT_STATIC_MESH)
            options.set_editor_property('import_mesh', True)
            options.set_editor_property('import_as_skeletal', False)
            options.set_editor_property('import_materials', False)
            options.set_editor_property('import_textures', False)
            static = options.static_mesh_import_data
            static.set_editor_property('combine_meshes', False)
            static.set_editor_property('auto_generate_collision', False)
            static.set_editor_property('generate_lightmap_u_vs', False)
            static.set_editor_property('import_mesh_lods', True)
            import_file(folder / f'{asset}_2k.fbx', destination, options)
            existing = [p for p in library.list_assets(destination, recursive=False)
                        if isinstance(unreal.load_asset(p), unreal.StaticMesh)]
        meshes = []
        for path in existing:
            mesh = unreal.load_asset(path)
            box = mesh.get_bounding_box()
            meshes.append({'path': path, 'min': [box.min.x, box.min.y, box.min.z],
                           'max': [box.max.x, box.max.y, box.max.z],
                           'slots': [str(s.material_slot_name) for s in mesh.static_materials]})
        report['models'][asset] = meshes
    checkpoint()
    unreal.log(f'SNR_WILDERNESS_IMPORTED {asset}')

unreal.log('SNR_WILDERNESS_IMPORT_COMPLETE')
