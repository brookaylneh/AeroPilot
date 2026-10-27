"""Inventory the acquired packs without modifying their source assets."""
import datetime
import json
from pathlib import Path
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[1]
registry = unreal.AssetRegistryHelpers.get_asset_registry()
folders = ['/Game/PN_OpenWorldFoliage', '/Game/PN_GrassLibrary', '/Game/PN_MeadowFlowers']
registry.scan_paths_synchronous(folders, force_rescan=True)
report = {'createdAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
          'engineVersion': unreal.SystemLibrary.get_engine_version(), 'packs': []}
materials = {}
ml = unreal.MaterialEditingLibrary
for folder in folders:
    assets = registry.get_assets_by_path(folder, recursive=True)
    assert assets, 'Missing imported pack: ' + folder
    meshes = []
    for data in assets:
        if str(data.asset_class_path.asset_name) != 'StaticMesh':
            continue
        mesh = data.get_asset()
        if '/Meshes/' not in mesh.get_path_name():
            continue
        box = mesh.get_bounding_box()
        slots = list(mesh.get_editor_property('static_materials'))
        mesh_materials = []
        for slot in slots:
            mat = slot.material_interface
            if not mat:
                continue
            path = mat.get_path_name()
            mesh_materials.append(path)
            if path not in materials:
                values = {}
                for name in ml.get_scalar_parameter_names(mat):
                    if isinstance(mat, unreal.MaterialInstanceConstant):
                        values[str(name)] = ml.get_material_instance_scalar_parameter_value(mat, name)
                materials[path] = {'class': mat.get_class().get_name(), 'scalars': values}
        meshes.append({'path': mesh.get_path_name(),
            'min': [box.min.x, box.min.y, box.min.z], 'max': [box.max.x, box.max.y, box.max.z],
            'lods': mesh.get_num_lods(),
            'vertices': [mesh.get_num_vertices(i) for i in range(mesh.get_num_lods())],
            'materials': mesh_materials})
    report['packs'].append({'folder': folder, 'assetCount': len(assets), 'meshes': meshes})
report['materials'] = materials
(ROOT / 'evidence/fab-vegetation-inventory.json').write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_FAB_VEGETATION_INVENTORIED')
