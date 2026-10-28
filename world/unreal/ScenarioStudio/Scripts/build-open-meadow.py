"""Replace the search landscape from world.json, preserving the authored world."""

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
world_file = ROOT / "world/cosys/world.json"
world = json.loads(world_file.read_text())
grid = world["heightfield"]
bounds = world["searchBoundsCm"]
lo, hi = bounds["min"], bounds["max"]
span_x, span_y = hi["x"] - lo["x"], hi["y"] - lo["y"]
heightfile = ROOT / grid["path"]
assert hashlib.sha256(heightfile.read_bytes()).hexdigest() == world["heightfieldSha256"]
report_path = Path(os.environ["SNR_EVIDENCE_DIR"]) / "landscape-authoring.json"
assert not report_path.exists(), "Archive the previous authoring evidence before a new run"

lib = unreal.EditorAssetLibrary
ml = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
landscapes = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.Landscape)]
assert len(landscapes) == 1
previous = landscapes[0]
preserved = {
    a.get_path_name(): a.get_actor_transform()
    for a in actors.get_all_level_actors()
    if a != previous
}
folder = "/Game/OpenMeadow/WorldTerrain"
material_path = folder + "/M_Landscape"
if lib.does_asset_exist(material_path):
    material = unreal.load_asset(material_path)
else:
    material = lib.duplicate_asset(
        previous.get_editor_property("landscape_material").get_path_name(), material_path
    )
assert material
expressions = ml.get_material_expressions(material)
ground_masks = [
    e
    for e in expressions
    if isinstance(e, unreal.MaterialExpressionTextureSample)
    and e.get_editor_property("texture").get_name() in ("ground_mask_020", "T_GroundMask")
]
vegetation_masks = [
    e
    for e in expressions
    if isinstance(e, unreal.MaterialExpressionTextureSample)
    and e.get_editor_property("texture").get_name() == "T_VegetationMask"
]
assert len(ground_masks) == len(vegetation_masks) == 1
ground_mask, vegetation_mask = ground_masks[0], vegetation_masks[0]
mask_uv = ml.get_inputs_for_material_expression(material, ground_mask)[0]
assert isinstance(mask_uv, unreal.MaterialExpressionAdd)
assert ml.get_inputs_for_material_expression(material, vegetation_mask)[0] == mask_uv
mask_scale = ml.get_inputs_for_material_expression(material, mask_uv)[0]
assert isinstance(mask_scale, unreal.MaterialExpressionMultiply)

for label, target, values in (
    ("World mask scale", mask_scale, (1 / span_x, 1 / span_y)),
    ("World mask offset", mask_uv, (-lo["x"] / span_x, -lo["y"] / span_y)),
):
    nodes = [e for e in expressions if e.get_editor_property("desc") == label]
    assert len(nodes) <= 1
    vector = (
        nodes[0]
        if nodes
        else ml.create_material_expression(material, unreal.MaterialExpressionConstant2Vector)
    )
    assert isinstance(vector, unreal.MaterialExpressionConstant2Vector)
    vector.set_editor_property("desc", label)
    vector.set_editor_property("r", values[0])
    vector.set_editor_property("g", values[1])
    assert ml.connect_material_expressions(vector, "", target, "B")

for filename, name, expression in (
    ("ground-mask.png", "T_GroundMask", ground_mask),
    ("vegetation-mask.png", "T_VegetationMask", vegetation_mask),
):
    task = unreal.AssetImportTask()
    task.filename = str(heightfile.parent / filename)
    task.destination_path = folder
    task.destination_name = name
    task.automated = True
    task.replace_existing = True
    task.save = True
    tools.import_asset_tasks([task])
    assert len(task.imported_object_paths) == 1
    texture = unreal.load_asset(task.imported_object_paths[0])
    assert texture
    texture.set_editor_property("srgb", False)
    texture.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
    assert lib.save_loaded_asset(texture)
    expression.set_editor_property("texture", texture)
ml.recompile_material(material)
assert lib.save_loaded_asset(material)

assert actors.destroy_actor(previous)
landscape = unreal.ScenarioLandscapeTools.create_landscape(
    str(world_file), str(heightfile), grid["columns"], grid["rows"], material
)
assert landscape
catalog = unreal.load_asset("/Game/OpenMeadow/Catalog")
assert catalog
regions = catalog.get_editor_property("regions")
assert len(regions) == 1
region = regions[0]
region.set_editor_property("min", unreal.Vector(lo["x"], lo["y"], lo["z"]))
region.set_editor_property("max", unreal.Vector(hi["x"], hi["y"], hi["z"]))
region.set_editor_property("display_name", "Open Meadow")
regions[0] = region
catalog.set_editor_property("regions", regions)
catalog.set_editor_property("asset_version", world["environmentAssetVersion"])
assert lib.save_loaded_asset(catalog)
assert levels.save_current_level()

origin, extent = landscape.get_actor_bounds(False)
assert abs(extent.x * 2 - span_x) < 0.1
assert abs(extent.y * 2 - span_y) < 0.1
assert abs(origin.x - (lo["x"] + hi["x"]) / 2) < 0.1
assert abs(origin.y - (lo["y"] + hi["y"]) / 2) < 0.1
component_count = len(landscape.get_components_by_class(unreal.LandscapeComponent))
assert component_count == (grid["columns"] - 1) * (grid["rows"] - 1) // (126 * 126)
remaining = {a.get_path_name(): a for a in actors.get_all_level_actors() if a != landscape}
assert remaining.keys() == preserved.keys()
for name, transform in preserved.items():
    assert remaining[name].get_actor_transform() == transform, name
report_path.write_text(
    json.dumps(
        {
            "environmentAssetVersion": world["environmentAssetVersion"],
            "heightfieldSha256": world["heightfieldSha256"],
            "extentCm": {"northSouth": extent.x * 2, "eastWest": extent.y * 2},
            "landscapeComponents": component_count,
            "preservedActors": sorted(preserved),
            "landscapeMaterial": material.get_path_name(),
        },
        indent=2,
    )
    + "\n"
)
unreal.log("SNR_OPEN_MEADOW_AUTHORED")
