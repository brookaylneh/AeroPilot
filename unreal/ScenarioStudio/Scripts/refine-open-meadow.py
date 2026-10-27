"""Update visual assets without regenerating terrain or changing saved positions."""
from pathlib import Path
import json
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[1]
lib = unreal.EditorAssetLibrary
ml = unreal.MaterialEditingLibrary
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level('/Game/OpenMeadow/OpenMeadow')

# The generated ring uses the opposite winding from Unreal's front face.
# Correct culling without flipping its supplied upward lighting normals.
horizon_path = '/Game/OpenMeadow/M_Horizon_020'
horizon = unreal.load_asset(horizon_path) if lib.does_asset_exist(horizon_path) else lib.duplicate_asset('/Game/OpenMeadow/M_Meadow', horizon_path)
horizon.set_editor_property('two_sided', False)
ml.recompile_material(horizon)
lib.save_loaded_asset(horizon)
background = next(a for a in actors.get_all_level_actors() if a.get_actor_label() == 'scenery.non-placeable-horizon')
background.static_mesh_component.set_material(0, horizon)
background.static_mesh_component.set_editor_property('reverse_culling', True)
background.static_mesh_component.set_editor_property('cast_shadow', False)

grass_type = unreal.load_asset('/Game/OpenMeadow/GT_Meadow')
varieties = list(grass_type.get_editor_property('grass_varieties'))
fade_path = '/Game/OpenMeadow/M_GrassFade'
if lib.does_asset_exist(fade_path):
    fade_material = unreal.load_asset(fade_path)
else:
    original = varieties[0].get_editor_property('grass_mesh').get_material(0)
    fade_material = lib.duplicate_asset(original.get_path_name(), fade_path)
    opacity = ml.get_material_property_input_node(fade_material, unreal.MaterialProperty.MP_OPACITY_MASK)
    output = ml.get_material_property_input_node_output_name(fade_material, unreal.MaterialProperty.MP_OPACITY_MASK)
    assert opacity, 'Expected masked grass material'
    fade = ml.create_material_expression(fade_material, unreal.MaterialExpressionPerInstanceFadeAmount)
    multiply = ml.create_material_expression(fade_material, unreal.MaterialExpressionMultiply)
    assert ml.connect_material_expressions(opacity, output, multiply, 'A')
    assert ml.connect_material_expressions(fade, '', multiply, 'B')
    assert ml.connect_material_property(multiply, '', unreal.MaterialProperty.MP_OPACITY_MASK)
    ml.recompile_material(fade_material)
    lib.save_loaded_asset(fade_material)

for variety in varieties:
    variety.set_editor_property('grass_density', unreal.PerPlatformFloat(default=120))
    mesh = variety.get_editor_property('grass_mesh')
    mesh.set_material(0, fade_material)
    lib.save_loaded_asset(mesh)
grass_type.set_editor_property('grass_varieties', varieties)
lib.save_loaded_asset(grass_type)
assert levels.save_current_level()
report_path = ROOT / 'evidence/open-meadow-terrain.json'
report = json.loads(report_path.read_text())
report.update(grassDensityPerVariety=120, grassDistanceFade=True, twoSidedBackground=False, backgroundWindingCorrected=True)
report_path.write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_OPEN_MEADOW_REFINED')
