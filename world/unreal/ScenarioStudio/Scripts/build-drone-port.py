"""Rebuild the launch slab from world.json; the valley apron of build-open-meadow.py carries it."""

import hashlib
import json
import os
import sys
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
assert len(sites) == 1
sys.path.extend([str(ROOT), str(sites[0])])
from mission_planner.environment import load_env

load_env()
world = json.loads((ROOT / "world/cosys/world.json").read_text())
report_path = Path(os.environ["SNR_EVIDENCE_DIR"]) / "drone-port-authoring.json"
assert not report_path.exists(), "Archive the previous authoring evidence before a new run"
raw = (ROOT / world["heightfield"]["path"]).read_bytes()
assert hashlib.sha256(raw).hexdigest() == world["heightfieldSha256"]
assert world["port"]["boundary"] == "south" and not world["port"]["searchable"]
lib = unreal.EditorAssetLibrary
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
catalog = unreal.load_asset("/Game/OpenMeadow/Catalog")
assert catalog
regions = catalog.get_editor_property("regions")
assert len(regions) == 1
region = regions[0]
for property_name, bound_name in (("min", "min"), ("max", "max")):
    bound = region.get_editor_property(property_name)
    expected = world["searchBoundsCm"][bound_name]
    assert all(abs(getattr(bound, axis) - expected[axis]) < 0.1 for axis in ("x", "y", "z"))
starts = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.PlayerStart)]
assert len(starts) == 1
start = starts[0].get_actor_location()
assert all(
    abs(getattr(start, axis) - world["playerStartUeCm"][axis]) < 0.01 for axis in ("x", "y", "z")
)
assert catalog.get_editor_property("cosys_origin_cm") == start
(old_slab,) = [a for a in actors.get_all_level_actors() if a.get_actor_label() == "drone.port"]
slab_material = old_slab.static_mesh_component.get_material(0)
assert slab_material
center = world["port"]["centerUeCm"]
north_size, east_size, thickness = world["port"]["dimensionsMetres"]
position = unreal.Vector(center["x"], center["y"], center["z"] - thickness * 50)
scale = unreal.Vector(north_size, east_size, thickness)
assert old_slab.get_actor_location().is_near_equal(position, 0.01)
assert old_slab.get_actor_scale3d().is_near_equal(scale, 0.00001)
gap = (world["searchBoundsCm"]["min"]["x"] - center["x"] - north_size * 50) / 100
assert abs(gap - world["port"]["nearestBoundaryGapMetres"]) < 1e-9
for vehicle in world["vehicles"].values():
    spawn = vehicle["spawnUeCm"]
    assert abs(spawn["x"] - center["x"]) < north_size * 50
    assert abs(spawn["y"] - center["y"]) < east_size * 50
    assert spawn["z"] > center["z"]
assert actors.destroy_actor(old_slab)
slab = actors.spawn_actor_from_class(unreal.StaticMeshActor, position)
assert slab
slab.set_actor_label("drone.port")
slab.set_editor_property("tags", [unreal.Name("drone.port")])
slab.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Cube"))
slab.set_actor_scale3d(scale)
slab.static_mesh_component.set_material(0, slab_material)
slab.static_mesh_component.set_collision_profile_name("ScenarioObstacle")
assert levels.save_current_level()
assert slab.get_actor_location().is_near_equal(position, 0.01)
report_path.write_text(
    json.dumps(
        {
            "environmentAssetVersion": world["environmentAssetVersion"],
            "infrastructureVersion": world["infrastructureVersion"],
            "heightfieldSha256": world["heightfieldSha256"],
            "playerStartUeCm": world["playerStartUeCm"],
            "port": world["port"],
            "vehicles": world["vehicles"],
            "retainedPadMaterial": slab_material.get_path_name(),
        },
        indent=2,
    )
    + "\n"
)
unreal.log("SNR_DRONE_PORT_AUTHORED")
