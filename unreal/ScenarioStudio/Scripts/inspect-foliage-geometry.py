"""Compare leaf geometry before/after reduction without modifying assets."""
import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
source = json.loads((root / 'evidence/wilderness-import.json').read_text())
report = []
for index, entry in enumerate(source['models']['fir_tree_01']):
    for kind, path in [('source', entry['path']), ('runtime', f'/Game/Wilderness/Runtime/SM_Fir_{index}')]:
        asset = unreal.load_asset(path)
        mesh = unreal.DynamicMesh()
        mesh, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh_v2(asset, mesh,
            unreal.GeometryScriptCopyMeshFromAssetOptions(),
            unreal.GeometryScriptMeshReadLOD(lod_type=unreal.GeometryScriptLODType.SOURCE_MODEL, lod_index=0))
        slots = []
        for material_id, material in enumerate(asset.get_editor_property('static_materials')):
            _, selection = unreal.GeometryScript_MeshSelection.select_mesh_elements_by_material_id(mesh, material_id)
            _, count = unreal.GeometryScript_MeshSelection.get_mesh_selection_info(selection)
            slots.append({'material': str(material.material_interface.get_path_name()), 'triangles': count})
        report.append({'index': index, 'kind': kind, 'triangles': mesh.get_triangle_count(), 'slots': slots})
        (root / 'evidence/foliage-geometry.json').write_text(json.dumps(report, indent=2) + '\n')
        del mesh
unreal.log('SNR_FOLIAGE_INSPECTED')
