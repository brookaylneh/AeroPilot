"""Make the terrain sheet render and collide from either side for inspection."""
import unreal
lib = unreal.EditorAssetLibrary
material = unreal.load_asset('/Game/Wilderness/Materials/M_AlderGround')
material.set_editor_property('two_sided', True)
unreal.MaterialEditingLibrary.recompile_material(material)
lib.save_loaded_asset(material)
terrain = unreal.load_asset('/Game/Wilderness/SM_AlderTerrain')
setup = terrain.get_editor_property('body_setup')
setup.set_editor_property('double_sided_geometry', True)
lib.save_loaded_asset(terrain)
unreal.log('SNR_TERRAIN_SURFACE_SAVED')
