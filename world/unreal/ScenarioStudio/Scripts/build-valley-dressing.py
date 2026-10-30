"""Place the valley dressing from $SNR_EVIDENCE_DIR/placements.json (world.unreal.valley_dressing)
as one foliage type per mesh in OpenMeadow, then run the ground-trace and capsule stand tests."""

import hashlib
import json
import math
import os
from collections import Counter
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
FOLDER = "/Game/Valley/Dressing"
LABEL = "valley.dressing"
# Walker-sized capsule (radius, half height m), lifted clear of the ground it stands on.
CAPSULE_M = (0.3, 0.9, 0.05)
STATION_M = 5.0
STANDS = ("trail", "road")
# Every 25th instance of each row gets a scenario ground trace.
SAMPLE = 25

evidence = Path(os.environ["SNR_EVIDENCE_DIR"])
report_path = evidence / "dressing.json"
assert not report_path.exists(), report_path
placements = json.loads((evidence / "placements.json").read_text())
sources = {
    "dressingSha256": "world/unreal/valley-dressing.json",
    "landmarksSha256": "world/unreal/landmarks.json",
    "heightfieldSha256": ".local/world/valley/terrain/height.r16",
}
for key, path in sources.items():
    assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == placements[key], key
document = json.loads((ROOT / sources["landmarksSha256"]).read_text())
world_config = json.loads((ROOT / "world/cosys/world.json").read_text())
BOUNDS = world_config["searchBoundsCm"]
SPACING_CM = world_config["heightfield"]["sampleSpacingMetres"] * 100
meshes = placements["meshes"]

lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
MAP = "/Game/OpenMeadow/OpenMeadow"
assert levels.load_level(MAP)
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
for actor in actors.get_all_level_actors():
    if isinstance(actor, unreal.InstancedFoliageActor):
        assert actor.get_actor_label() == LABEL, actor.get_actor_label()
        assert actors.destroy_actor(actor)
# Every asset below is rebuilt from the placements, so nothing stale survives a run.
for path in lib.list_assets(FOLDER):
    assert lib.delete_asset(path), path


def trace(north, east):
    # A trace whose ray meets the surface on a heightfield sample line misses the landscape
    # (5450 of 9002 vertical lab rays; leaning rays miss where they cross a line at the surface),
    # so test traces run through the centre of the cell holding the point.
    i = math.floor((north * 100 - BOUNDS["min"]["x"]) / SPACING_CM) + 0.5
    j = math.floor((east * 100 - BOUNDS["min"]["y"]) / SPACING_CM) + 0.5
    x, y = BOUNDS["min"]["x"] + i * SPACING_CM, BOUNDS["min"]["y"] + j * SPACING_CM
    hit = unreal.SystemLibrary.line_trace_single(
        world,
        unreal.Vector(x, y, 50000),
        unreal.Vector(x, y, -50000),
        unreal.TraceTypeQuery.ECC_SCENARIO_GROUND,
        True,
        [],
        unreal.DrawDebugTrace.NONE,
        True,
    )
    assert hit, (north, east)
    # HitResult.to_tuple() follows BreakHitResult: ImpactPoint 5, HitActor 9, HitComponent 10.
    return hit.to_tuple()


transforms = {}
for row in placements["rows"]:
    for index, north, east, z, yaw, scale, normal in zip(
        *(row[k] for k in ("mesh", "north", "east", "z", "yaw", "scale", "normal")), strict=True
    ):
        mesh = row["meshes"][index]
        forward = unreal.Vector(math.cos(math.radians(yaw)), math.sin(math.radians(yaw)), 0)
        transforms.setdefault(mesh, []).append(
            unreal.Transform(
                location=unreal.Vector(north * 100, east * 100, z * 100),
                rotation=unreal.MathLibrary.make_rot_from_zx(unreal.Vector(*normal), forward),
                scale=unreal.Vector(scale, scale, scale),
            )
        )
for mesh, items in transforms.items():
    asset = unreal.load_asset(f"/Game/Valley/{mesh}")
    box = asset.get_bounding_box()
    found = (box.min.x, box.max.x, box.min.y, box.max.y, box.min.z, box.max.z)
    assert all(abs(a / 100 - b) < 1e-3 for a, b in zip(found, meshes[mesh]["boundsM"])), mesh
    foliage = tools.create_asset(
        "FT_" + mesh.split("/")[-1].removeprefix("SM_"),
        FOLDER,
        unreal.FoliageType_InstancedStaticMesh,
        unreal.FoliageType_InstancedStaticMeshFactory(),
    )
    foliage.set_editor_property("mesh", asset)
    body = foliage.get_editor_property("body_instance")
    body.set_editor_property("collision_profile_name", meshes[mesh]["collision"])
    foliage.set_editor_property("body_instance", body)
    assert lib.save_loaded_asset(foliage), mesh
    unreal.InstancedFoliageActor.add_instances(world, foliage, items)
(holder,) = [
    a for a in actors.get_all_level_actors() if isinstance(a, unreal.InstancedFoliageActor)
]
holder.set_actor_label(LABEL)
assert levels.save_current_level()
# A body takes its profile's collision when loaded (FBodyInstance::LoadProfileData), so the tests
# run on the reloaded level, as packaged.
assert levels.load_level(MAP)
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
(landscape,) = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.Landscape)]
(holder,) = [
    a for a in actors.get_all_level_actors() if isinstance(a, unreal.InstancedFoliageActor)
]


def mesh_of(component):
    return (
        component.get_editor_property("static_mesh")
        .get_path_name()
        .split(".")[0][len("/Game/Valley/") :]
    )


counts = Counter()
for component in holder.get_components_by_class(unreal.InstancedStaticMeshComponent):
    counts[mesh_of(component)] += component.get_instance_count()
    profile = meshes[mesh_of(component)]["collision"]
    assert component.get_collision_profile_name() == profile
    assert (component.get_collision_enabled() == unreal.CollisionEnabled.NO_COLLISION) == (
        profile == "NoCollision"
    )
assert counts == Counter({m: len(t) for m, t in transforms.items()}), counts

# Scenario placement traces reach terrain, the bridge deck, rocks or logs, never canopies.
terrain = {m for m, entry in meshes.items() if entry["collision"] == "ScenarioTerrain"}
traces = Counter()
for row in [r for r in placements["rows"] if r["ground"] == "terrain"]:
    for index, north, east in list(zip(row["mesh"], row["north"], row["east"]))[::SAMPLE]:
        fields = trace(north, east)
        hit = "landscape" if fields[9] == landscape else mesh_of(fields[10])
        assert hit == "landscape" or (fields[9] == holder and hit in terrain), (
            row["name"],
            row["meshes"][index],
            north,
            east,
            hit,
        )
        traces[hit] += 1
# Capsule stand tests every STATION_M along each trail and road: standing on the scenario ground,
# a walker-sized capsule overlaps no dressing or structure.
radius, half, lift = CAPSULE_M
solid = [unreal.ObjectTypeQuery.OBJECT_TYPE_QUERY1, unreal.ObjectTypeQuery.OBJECT_TYPE_QUERY2]
stands = Counter()
for item in document["landmarks"]:
    if item["kind"] not in STANDS:
        continue
    route = [(p["x"] / 100, p["y"] / 100) for p in item["geometry"]["points"]]
    for (n0, e0), (n1, e1) in zip(route, route[1:]):
        steps = max(1, math.ceil(math.dist((n0, e0), (n1, e1)) / STATION_M))
        for k in range(steps):
            north, east = n0 + (n1 - n0) * k / steps, e0 + (e1 - e0) * k / steps
            fields = trace(north, east)
            centre = unreal.Vector(north * 100, east * 100, fields[5].z + (half + lift) * 100)
            overlaps = unreal.SystemLibrary.capsule_overlap_actors(
                world, centre, radius * 100, half * 100, solid, None, [landscape]
            )
            assert not overlaps, (item["id"], north, east, [a.get_actor_label() for a in overlaps])
            stands[item["id"]] += 1
report = {
    "instances": sum(counts.values()),
    "perMesh": dict(sorted(counts.items())),
    "measuredHeightM": placements["measuredHeightM"],
    "groundTraces": dict(traces),
    "capsuleStands": dict(stands),
}
report_path.write_text(json.dumps(report, indent=2) + "\n")
unreal.log(
    "SNR_VALLEY_DRESSED " + json.dumps({k: report[k] for k in ("instances", "groundTraces")})
)
