"""Measure the assembled character's local axes before fitting equipment."""
import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
actor = actors.spawn_actor_from_class(unreal.EditorAssetLibrary.load_blueprint_class('/Game/Scenario/MetaHumans/Adult/BP_Adult'), unreal.Vector())
report = []
for mesh in actor.get_components_by_class(unreal.SkeletalMeshComponent):
    if mesh.get_name() != 'Body':
        continue
    bones = {}
    for bone in ['root', 'pelvis', 'head', 'foot_l', 'ball_l', 'foot_r', 'ball_r', 'spine_05']:
        point = mesh.get_socket_location(bone)
        bones[bone] = [point.x, point.y, point.z]
    report.append({'mesh': mesh.get_name(), 'transform': str(mesh.get_world_transform()), 'bones': bones})
actors.destroy_actor(actor)
(root / 'evidence/character-fit.json').write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_CHARACTER_FIT_INSPECTED')
