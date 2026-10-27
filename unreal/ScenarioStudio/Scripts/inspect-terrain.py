import json
from pathlib import Path
import unreal
root = Path(unreal.Paths.project_dir()).resolve().parents[1]
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
levels.load_level('/Game/Wilderness/AlderValley')
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_all_level_actors()
report = {'actors': [{'name': a.get_actor_label(), 'class': a.get_class().get_name()} for a in actors], 'hits': []}
for x,y in [(-2200,-400),(-5000,4500),(0,0),(6000,5000)]:
    hit = unreal.SystemLibrary.line_trace_single(world, unreal.Vector(x,y,50000), unreal.Vector(x,y,-50000),
        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, True, [], unreal.DrawDebugTrace.NONE, True)
    report['hits'].append(str(hit))
mesh = unreal.load_asset('/Game/Wilderness/SM_AlderTerrain')
report['material'] = str(mesh.get_material(0))
report['bounds'] = str(mesh.get_bounding_box())
(root / 'evidence/terrain-inspection.json').write_text(json.dumps(report, indent=2)+'\n')
unreal.log('SNR_TERRAIN_INSPECTED')
