"""Import the licensed bear into a self-contained wildlife level."""

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
from mission_planner.environment import load_env  # noqa: E402
from mission_planner.gcs.geodesy import point_in_polygon  # noqa: E402
from world.cosys.landmarks import load  # noqa: E402
from world.cosys.terrain import terrain_height  # noqa: E402

load_env()
spec = json.loads((ROOT / "world/unreal/wildlife.json").read_text())
report = Path(os.environ["SNR_EVIDENCE_DIR"]) / "wildlife.json"
assert not report.exists()
for source in spec["files"].values():
    assert hashlib.sha256((ROOT / source["path"]).read_bytes()).hexdigest() == source["sha256"]
assert len(spec["baseColorLinear"]) == 3
assert all(0 <= value <= 1 for value in spec["baseColorLinear"])

folder = "/Wildlife/Bear"
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
assert not lib.does_directory_exist(folder)
options = unreal.FbxImportUI()
for key, value in {
    "automated_import_should_detect_type": False,
    "mesh_type_to_import": unreal.FBXImportType.FBXIT_STATIC_MESH,
    "import_as_skeletal": False,
    "import_mesh": True,
    "import_animations": False,
    "import_materials": False,
    "import_textures": False,
}.items():
    options.set_editor_property(key, value)
for key, value in {
    "combine_meshes": True,
    "auto_generate_collision": True,
    "convert_scene": True,
    "convert_scene_unit": True,
    "transform_vertex_to_absolute": True,
    "normal_import_method": unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS,
}.items():
    options.static_mesh_import_data.set_editor_property(key, value)
task = unreal.AssetImportTask()
task.filename = str(ROOT / spec["files"]["mesh"]["path"])
task.destination_path = folder
task.destination_name = "SM_Bear"
task.automated = True
task.replace_existing = False
task.save = True
task.options = options
task.factory = unreal.FbxFactory()
tools.import_asset_tasks([task])
assert len(task.imported_object_paths) == 1, task.imported_object_paths
mesh = unreal.load_asset(task.imported_object_paths[0])
assert isinstance(mesh, unreal.StaticMesh)
material = tools.create_asset("M_Bear", folder, unreal.Material, unreal.MaterialFactoryNew())
assert material
edit = unreal.MaterialEditingLibrary
color = edit.create_material_expression(material, unreal.MaterialExpressionConstant3Vector)
color.constant = unreal.LinearColor(*spec["baseColorLinear"], 1)
assert edit.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
roughness = edit.create_material_expression(material, unreal.MaterialExpressionConstant)
roughness.r = 0.85
assert edit.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)
edit.recompile_material(material)
mesh.set_material(0, material)
assert mesh.get_material(0) == material
for asset in (mesh, material):
    assert lib.save_loaded_asset(asset)

levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.new_level("/Wildlife/BearHabitat")
actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector())
assert actor
actor.set_actor_label("wildlife.bear")
actor.set_editor_property("tags", [unreal.Name("wildlife.bear")])
actor.static_mesh_component.set_static_mesh(mesh)
actor.static_mesh_component.set_collision_profile_name("ScenarioObstacle")
box = mesh.get_bounding_box()
scale = spec["lengthM"] * 100 / max(box.max.x - box.min.x, box.max.y - box.min.y)
assert 0 < scale < 10
actor.set_actor_scale3d(unreal.Vector(scale, scale, scale))
actor.set_actor_rotation(unreal.Rotator(yaw=spec["yawDegrees"]), False)
center, extent = actor.get_actor_bounds(False)
north, east = (spec["positionMetres"][axis] for axis in ("north", "east"))
assert abs(north) > 400 or abs(east) > 400, "Bear must be away from the map centre"
corners = [
    {"north_m": north + dn * extent.x / 100, "east_m": east + de * extent.y / 100}
    for dn in (-1, 1)
    for de in (-1, 1)
]
for landmark in load():
    if landmark["kind"] != "forest":
        continue
    polygon = [
        {"north_m": p["x"] / 100, "east_m": p["y"] / 100} for p in landmark["geometry"]["points"]
    ]
    assert not any(point_in_polygon(p, polygon) for p in corners), landmark["name"]
ground = [terrain_height(p["north_m"], p["east_m"]) for p in corners]
assert max(ground) - min(ground) < 0.05, "Bear placement requires nearly level ground"
actor.set_actor_location(
    unreal.Vector(
        north * 100 - center.x, east * 100 - center.y, max(ground) * 100 - center.z + extent.z
    ),
    False,
    False,
)
assert levels.save_current_level()
center, extent = actor.get_actor_bounds(False)
registry = unreal.AssetRegistryHelpers.get_asset_registry()
registry.scan_paths_synchronous(["/Wildlife"], True)
dependencies = {}
for asset in registry.get_assets_by_path("/Wildlife", recursive=True):
    package = str(asset.package_name)
    dependencies[package] = [
        str(p) for p in registry.get_dependencies(package, unreal.AssetRegistryDependencyOptions())
    ]
    assert all(
        p.startswith(("/Wildlife/", "/Engine/", "/Script/")) for p in dependencies[package]
    ), dependencies[package]
report.write_text(
    json.dumps(
        {
            "spec": spec,
            "level": "/Wildlife/BearHabitat",
            "assets": {"mesh": mesh.get_path_name()},
            "scale": scale,
            "boundsCm": {
                "center": [center.x, center.y, center.z],
                "extent": [extent.x, extent.y, extent.z],
            },
            "groundRangeM": [min(ground), max(ground)],
            "animated": False,
            "dependencies": dependencies,
        },
        indent=2,
    )
    + "\n"
)
unreal.log("SNR_WILDLIFE_AUTHORED")
