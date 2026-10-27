import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level('/Game/OpenMeadow/OpenMeadow')
def vec(v): return [v.x, v.y, v.z]
report = {'actors': []}
for a in actors.get_all_level_actors():
    p, e = a.get_actor_bounds(False)
    report['actors'].append({'name': a.get_actor_label(), 'class': a.get_class().get_name(),
        'positionCm': vec(a.get_actor_location()), 'boundsCenterCm': vec(p), 'boundsHalfExtentCm': vec(e)})
bp = unreal.load_class(None, '/AirSim/Blueprints/BP_FlyingPawn.BP_FlyingPawn_C')
assert bp
pawn = actors.spawn_actor_from_class(bp, unreal.Vector(0, 0, 10000))
center, extent = pawn.get_actor_bounds(True)
report['defaultQuadrotor'] = {'class': bp.get_path_name(), 'collisionCenterOffsetCm': vec(center - pawn.get_actor_location()), 'collisionHalfExtentCm': vec(extent)}
actors.destroy_actor(pawn)
(root / 'evidence/phase2a-level-inspection.json').write_text(json.dumps(report, indent=2))
unreal.log('SNR_PHASE2A_LEVEL_INSPECTED')
