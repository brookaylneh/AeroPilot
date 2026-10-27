"""Correct the generated terrain's front faces without changing its coordinates."""
import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
lib = unreal.EditorAssetLibrary
terrain = unreal.load_asset('/Game/Wilderness/SM_AlderTerrain')
if lib.get_metadata_tag(terrain, 'ScenarioFrontFaces') != 'corrected-v1':
    dynamic = unreal.DynamicMesh()
    dynamic, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh_v2(
        terrain, dynamic, unreal.GeometryScriptCopyMeshFromAssetOptions(),
        unreal.GeometryScriptMeshReadLOD(lod_type=unreal.GeometryScriptLODType.SOURCE_MODEL, lod_index=0))
    dynamic, selection = unreal.GeometryScript_MeshSelection.create_select_all_mesh_selection(dynamic)
    unreal.GeometryScript_Normals.flip_triangle_selection_normals(dynamic, selection, True, False)
    _, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(dynamic, terrain,
        unreal.GeometryScriptCopyMeshToAssetOptions(enable_recompute_tangents=True), unreal.GeometryScriptMeshWriteLOD())
    lib.set_metadata_tag(terrain, 'ScenarioFrontFaces', 'corrected-v1')
    lib.save_loaded_asset(terrain)
material = unreal.load_asset('/Game/Wilderness/Materials/M_AlderGround')
material.set_editor_property('two_sided', False)
unreal.MaterialEditingLibrary.recompile_material(material)
lib.save_loaded_asset(material)
pack = unreal.load_asset('/Game/Scenario/Equipment/SM_Daypack')
(root / 'evidence/geometry-repair.json').write_text(json.dumps({'terrainWindingCorrected': True,
    'daypackBounds': str(pack.get_bounding_box())}, indent=2)+'\n')
unreal.log('SNR_TERRAIN_WINDING_REPAIRED')
