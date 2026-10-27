"""Check a supplied MetaHuman preset using Epic's actual editor subsystem."""
import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
report = {'preset': '/MetaHumanCharacter/Optional/Presets/Kelvin',
          'pluginLoaded': hasattr(unreal, 'MetaHumanCharacterEditorSubsystem'),
          'assembled': False}
path = root / 'evidence/metahuman-readiness.json'
path.write_text(json.dumps(report, indent=2) + '\n')
preset = unreal.load_asset(report['preset'])
if preset is None:
    raise RuntimeError('Installed MetaHuman preset could not load.')
report['assetClass'] = preset.get_class().get_name()
report['hasHighResolutionTextures'] = preset.get_editor_property('has_high_resolution_textures')
report['hasFaceDNABlendshapes'] = preset.get_editor_property('has_face_dna_blendshapes')
path.write_text(json.dumps(report, indent=2) + '\n')
subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
report['editable'] = subsystem.try_add_object_to_edit(preset)
if report['editable']:
    report['canAssemble'] = subsystem.can_build_meta_human(preset, False)
    subsystem.remove_object_to_edit(preset)
path.write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_METAHUMAN_READINESS ' + json.dumps(report))
