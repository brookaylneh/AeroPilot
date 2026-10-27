"""Run with Unreal's Python commandlet to author the Cosys camera Blueprint."""
import json
from pathlib import Path
import unreal

destination = '/Game/Scenario/BP_ScenarioCamera'
camera = unreal.load_asset(destination)
if camera is None:
    source = unreal.load_asset('/AirSim/Blueprints/BP_ComputerVisionPawn')
    if source is None:
        raise RuntimeError('Cosys ComputerVision camera Blueprint is missing.')
    camera = unreal.AssetToolsHelpers.get_asset_tools().duplicate_asset(
        'BP_ScenarioCamera', '/Game/Scenario', source)
camera_class = unreal.EditorAssetLibrary.load_blueprint_class(destination)
defaults = unreal.get_default_object(camera_class)
# Cosys binds manual inputs during initializeForBeginPlay. Unreal requires the
# first player to possess this camera pawn before those bindings are enabled.
defaults.set_editor_property('auto_possess_player', unreal.AutoReceiveInput.PLAYER0)
defaults.set_editor_property('tags', [unreal.Name('scenario.camera')])
unreal.BlueprintEditorLibrary.compile_blueprint(camera)
if not unreal.EditorAssetLibrary.save_loaded_asset(camera, only_if_is_dirty=False):
    raise RuntimeError('Could not save the authored camera Blueprint.')
verified = unreal.get_default_object(unreal.EditorAssetLibrary.load_blueprint_class(destination))
if verified.get_editor_property('auto_possess_player') != unreal.AutoReceiveInput.PLAYER0:
    raise RuntimeError('Camera possession did not persist.')
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
report = {'cameraBlueprint': destination, 'autoPossessPlayer': 'Player0',
          'cosysGameModePreserved': True, 'runtimeTested': False}
(root / 'evidence/unreal-foundation-assets.json').write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_FOUNDATION_CAMERA_SAVED')
