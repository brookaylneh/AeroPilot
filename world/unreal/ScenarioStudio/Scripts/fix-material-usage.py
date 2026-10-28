"""Persist shader usage required by the real instanced foliage renderer."""
import unreal
lib = unreal.EditorAssetLibrary
for path in lib.list_assets('/Game/Wilderness/Materials'):
    material = unreal.load_asset(path)
    if isinstance(material, unreal.Material) and '/M_AlderGround.' not in path:
        unreal.MaterialEditingLibrary.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
        unreal.MaterialEditingLibrary.recompile_material(material)
        lib.save_loaded_asset(material)
unreal.log('SNR_MATERIAL_USAGE_SAVED')
