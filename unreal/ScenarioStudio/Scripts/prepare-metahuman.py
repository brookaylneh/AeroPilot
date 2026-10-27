"""Prepare a project copy of an Epic preset; cloud requests use Epic's own login.

Run the commandlet with -NoMetaHumanAccountPortalLoginFallback for a noninteractive
authentication check. No credentials are accepted or written by this script.
"""
import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
report_path = root / 'evidence/metahuman-preparation.json'
asset_path = '/Game/Scenario/Characters/MH_Adult'
report = {'preset': '/MetaHumanCharacter/Optional/Presets/Kelvin',
          'projectCharacter': asset_path, 'assembled': False, 'stage': 'loading'}


def record():
    report_path.write_text(json.dumps(report, indent=2) + '\n')


record()
character = unreal.load_asset(asset_path)
if character is None:
    preset = unreal.load_asset(report['preset'])
    if preset is None:
        raise RuntimeError('The installed MetaHuman preset is missing.')
    character = unreal.AssetToolsHelpers.get_asset_tools().duplicate_asset(
        'MH_Adult', '/Game/Scenario/Characters', preset)
if character is None:
    raise RuntimeError('Could not create the project MetaHuman asset.')
unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
if not subsystem.try_add_object_to_edit(character):
    raise RuntimeError('Could not open the character for editing.')
try:
    if not character.get_editor_property('has_face_dna_blendshapes'):
        report['stage'] = 'requesting-rig'
        record()
        params = unreal.MetaHumanCharacterAutoRiggingRequestParams()
        params.set_editor_property('rig_type', unreal.MetaHumanRigType.JOINTS_AND_BLEND_SHAPES)
        params.set_editor_property('report_progress', False)
        params.set_editor_property('blocking', True)
        subsystem.request_auto_rigging(character, params)
    report['hasFaceDNABlendshapes'] = character.get_editor_property('has_face_dna_blendshapes')
    if report['hasFaceDNABlendshapes'] and not character.get_editor_property('has_high_resolution_textures'):
        report['stage'] = 'requesting-textures'
        record()
        params = unreal.MetaHumanCharacterTextureRequestParams()
        params.set_editor_property('report_progress', False)
        params.set_editor_property('blocking', True)
        subsystem.request_texture_sources(character, params)
    report['hasHighResolutionTextures'] = character.get_editor_property('has_high_resolution_textures')
    report['canAssemble'] = subsystem.can_build_meta_human(character, False)
    report['stage'] = 'ready-for-assembly' if report['canAssemble'] else 'cloud-preparation-incomplete'
    unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
finally:
    subsystem.remove_object_to_edit(character)
    record()
unreal.log('SNR_METAHUMAN_PREPARATION ' + json.dumps(report))
