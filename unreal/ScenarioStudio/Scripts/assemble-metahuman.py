"""Assemble the authenticated, prepared character using Epic's runtime pipeline."""
import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
report = {'character': '/Game/Scenario/Characters/MH_Adult', 'pipeline': 'UE Optimized',
          'quality': 'High', 'assembled': False, 'rendered': False}
path = root / 'evidence/metahuman-assembly.json'


def record():
    path.write_text(json.dumps(report, indent=2) + '\n')


record()
character = unreal.load_asset(report['character'])
subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
if not character or not subsystem.try_add_object_to_edit(character):
    raise RuntimeError('Prepared MetaHuman could not be opened.')
try:
    if not subsystem.can_build_meta_human(character, True):
        raise RuntimeError('Character rig or high-resolution textures are missing.')
    params = unreal.MetaHumanCharacterEditorBuildParameters()
    params.set_editor_property('pipeline_type', unreal.MetaHumanDefaultPipelineType.OPTIMIZED)
    params.set_editor_property('pipeline_quality', unreal.MetaHumanQualityLevel.HIGH)
    params.set_editor_property('absolute_build_path', '/Game/Scenario/MetaHumans')
    params.set_editor_property('name_override', 'Adult')
    params.set_editor_property('common_folder_path', '/Game/Scenario/MetaHumans/Common')
    params.set_editor_property('enable_wardrobe_item_validation', True)
    subsystem.build_meta_human(character, params)
    assets = list(unreal.EditorAssetLibrary.list_assets('/Game/Scenario/MetaHumans/Adult', recursive=True))
    report['assets'] = assets
    report['blueprints'] = [p for p in assets if '/BP_' in p]
    report['assembled'] = bool(report['blueprints'])
    if not report['assembled']:
        raise RuntimeError('MetaHuman assembly produced no character Blueprint.')
    unreal.EditorAssetLibrary.save_directory('/Game/Scenario/MetaHumans', only_if_is_dirty=True, recursive=True)
finally:
    subsystem.remove_object_to_edit(character)
    record()
unreal.log('SNR_METAHUMAN_ASSEMBLED ' + json.dumps({'blueprints': report.get('blueprints', [])}))
