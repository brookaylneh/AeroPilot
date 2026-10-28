"""Keep foliage silhouette detail at useful ground and observation distances."""
import unreal
unreal.load_module('StaticMeshEditor')
subsystem = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem) or unreal.new_object(unreal.StaticMeshEditorSubsystem)
for index in range(3):
    mesh = unreal.load_asset(f'/Game/Wilderness/Runtime/SM_Fir_{index}')
    if not subsystem.set_lod_screen_sizes(mesh, [1., .16, .065, .022]):
        raise RuntimeError('Could not set foliage LOD distances')
    unreal.EditorAssetLibrary.save_loaded_asset(mesh)
unreal.log('SNR_FOLIAGE_DISTANCES_SAVED')
