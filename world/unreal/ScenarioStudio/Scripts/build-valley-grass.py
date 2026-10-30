"""Landscape grass types from valley-grass.json: one LandscapeGrassType per landscape grass name,
spawned at runtime by the M_ValleyGround LandscapeGrassOutput (build-open-meadow.py)."""

import json
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
grass = json.loads((ROOT / "world/unreal/valley-grass.json").read_text())
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
start_m, end_m = grass["cullM"]
assert 0 < start_m < end_m, grass["cullM"]
low_m, high_m = grass["heightM"]
ml = unreal.MaterialEditingLibrary


def rebuilt(name, cls, factory):
    """The asset at grass.folder/name, rewritten in place: M_ValleyGround and the grass types
    reference these assets, so a rerun cannot delete them."""
    path = f"{grass['folder']}/{name}"
    asset = (
        unreal.load_asset(path)
        if lib.does_asset_exist(path)
        else tools.create_asset(name, grass["folder"], cls, factory)
    )
    assert isinstance(asset, cls), path
    return asset


for name, entry in grass["types"].items():
    title = "".join(part.title() for part in name.split("-"))
    varieties = []
    for row in entry["varieties"]:
        mesh, per_100m2, (small, large) = row["mesh"], row["per100m2"], row["scale"]
        # GrassDensity is clamped to 1000 per 10 m x 10 m (LandscapeGrassType.h).
        assert 0 < per_100m2 <= 1000 and 0 < small <= large, row
        asset = unreal.load_asset(f"/Game/Valley/{mesh}")
        assert asset, mesh
        top_m = asset.get_bounding_box().max.z / 100
        assert low_m <= top_m and top_m * large <= high_m, (mesh, top_m, large)
        # One instance per material slot lifts the blades' base colour by the type's brightness.
        overrides = []
        for index, slot in enumerate(asset.get_editor_property("static_materials")):
            instance = rebuilt(
                f"MI_{title}_{mesh.split('/')[-1].removeprefix('SM_')}_{index}",
                unreal.MaterialInstanceConstant,
                unreal.MaterialInstanceConstantFactoryNew(),
            )
            ml.set_material_instance_parent(instance, slot.material_interface)
            ml.set_material_instance_scalar_parameter_value(
                instance, "Brightness", entry["brightness"]
            )
            stored = ml.get_material_instance_scalar_parameter_value(instance, "Brightness")
            assert abs(stored - entry["brightness"]) < 1e-6, (mesh, index, stored)
            assert lib.save_loaded_asset(instance), mesh
            overrides.append(instance)
        variety = unreal.GrassVariety()
        for key, value in {
            "grass_mesh": asset,
            "override_materials": overrides,
            "grass_density": unreal.PerPlatformFloat(default=per_100m2),
            "start_cull_distance": unreal.PerPlatformInt(default=start_m * 100),
            "end_cull_distance": unreal.PerPlatformInt(default=end_m * 100),
            "scaling": unreal.GrassScaling.UNIFORM,
            "scale_x": unreal.FloatInterval(min=small, max=large),
            "use_grid": False,
            "random_rotation": True,
            "align_to_surface": True,
            "cast_dynamic_shadow": False,
            "cast_contact_shadow": False,
        }.items():
            variety.set_editor_property(key, value)
        varieties.append(variety)
    grass_type = rebuilt(
        f"GT_{title}", unreal.LandscapeGrassType, unreal.LandscapeGrassTypeFactory()
    )
    grass_type.set_editor_property("grass_varieties", varieties)
    # Scalability's grass.DensityScale may not thin the ground cover under the drones.
    grass_type.set_editor_property("enable_density_scaling", False)
    stored = grass_type.get_editor_property("grass_varieties")
    # Interval and density values come back as float32.
    assert [
        (
            v.get_editor_property("grass_mesh").get_path_name(),
            round(v.get_editor_property("grass_density").get_editor_property("default"), 3),
            v.get_editor_property("end_cull_distance").get_editor_property("default"),
            round(v.get_editor_property("scale_x").get_editor_property("max"), 3),
            v.get_editor_property("cast_dynamic_shadow"),
        )
        for v in stored
    ] == [
        (
            f"/Game/Valley/{r['mesh']}.{r['mesh'].split('/')[-1]}",
            r["per100m2"],
            end_m * 100,
            r["scale"][1],
            False,
        )
        for r in entry["varieties"]
    ], name
    assert lib.save_loaded_asset(grass_type), name
    unreal.log(
        f"SNR_GRASS_TYPE {name} per100m2={sum(r['per100m2'] for r in entry['varieties'])} "
        f"brightness={entry['brightness']} cull_cm={start_m * 100},{end_m * 100}"
    )
unreal.log("SNR_VALLEY_GRASS_BUILT")
