"""Prepare one assembled MetaHuman's real wardrobe and standing animation."""
import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[2]
lib = unreal.EditorAssetLibrary
source = '/MetaHumanCharacter/Optional/Animation/UEFNAnimPreset/Locomotion/AS_MH_Neutral_Stand_Idle_Loop'
destination = '/Game/Scenario/Runtime/AS_Standing'
original = unreal.load_asset(source)
if not original:
    raise RuntimeError('Source standing animation could not load')
animation = unreal.load_asset(destination) if lib.does_asset_exist(destination) else unreal.AssetToolsHelpers.get_asset_tools().duplicate_asset('AS_Standing', '/Game/Scenario/Runtime', original)
if not animation:
    raise RuntimeError('Standing animation unavailable')
body = unreal.load_asset('/Game/Scenario/MetaHumans/Adult/Body/SKM_MH_Adult_BodyMesh')
skeleton = body.get_editor_property('skeleton')
if not unreal.ScenarioHuman.prepare_standing_animation(animation, skeleton):
    raise RuntimeError('The standing animation could not bind to the runtime skeleton')
lib.save_loaded_asset(animation)

inspection = json.loads((root / 'evidence/metahuman-runtime-assets.json').read_text())
clothes = next(c for c in inspection['blueprints'][0]['components'] if c['name'] == 'SkeletalMesh')
bindings = []
for material in clothes['materials']:
    key = 'topColor' if 'Shirt' in material['path'] else 'bottomColor'
    for parameter in ('diffuse_color_1', 'diffuse_color_2', 'B_diffuse_color_1'):
        if parameter in material['vectorParameters']:
            bindings.append({'key': key, 'component': 'SkeletalMesh', 'slot': material['slot'], 'parameter': parameter})
configuration = {
    'people': [{'id': 'adult-kelvin', 'name': 'Kelvin · Adult',
        'blueprint': '/Game/Scenario/MetaHumans/Adult/BP_Adult', 'tints': bindings, 'characterYaw': -90,
        'standingPose': destination, 'backpackMesh': '/Game/Scenario/Equipment/SM_Daypack'}],
    'palette': {'rust': [.45,.075,.025], 'blue': [.035,.12,.3], 'forest': [.06,.16,.08],
                'charcoal': [.045,.05,.055], 'sand': [.38,.29,.18]},
    'options': {'topId': ['crew-neck-shirt'], 'topColor': ['rust','blue','forest','sand'],
                'bottomId': ['shorts'], 'bottomColor': ['charcoal','sand','forest'],
                'backpackId': ['none','daypack'], 'backpackColor': ['rust','blue','charcoal'],
                'poseId': ['standing']}}
(root / 'world/unreal/character-runtime.json').write_text(json.dumps(configuration, indent=2) + '\n')
unreal.log('SNR_RUNTIME_CHARACTER_PREPARED')
