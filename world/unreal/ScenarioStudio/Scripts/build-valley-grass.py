"""Author hierarchical GPU runtime grass and its named landscape masks from valley-grass.json."""

import json
import math
import os
import sys
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
assert len(sites) == 1
sys.path.extend([str(ROOT), str(sites[0])])
from mission_planner.environment import load_env  # noqa: E402

load_env()
grass = json.loads((ROOT / "world/unreal/valley-grass.json").read_text())
world = json.loads((ROOT / "world/cosys/world.json").read_text())
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
ml = unreal.MaterialEditingLibrary
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
low_m, high_m = grass["heightM"]
assert 0 < low_m < high_m and 1 <= grass["cleanup"] <= 1.2
report = Path(os.environ["SNR_EVIDENCE_DIR"]) / "grass.json"
assert not report.exists(), report


def set_properties(obj, **properties):
    for key, value in properties.items():
        obj.set_editor_property(key, value)
    return obj


def rebuilt(name, cls, factory):
    path = f"{grass['folder']}/{name}"
    asset = (
        unreal.load_asset(path)
        if lib.does_asset_exist(path)
        else tools.create_asset(name, grass["folder"], cls, factory)
    )
    assert isinstance(asset, cls), path
    return asset


meshes = {}
for name, entry in grass["types"].items():
    title = "".join(part.title() for part in name.split("-"))
    for row in entry["varieties"]:
        mesh, density, (small, large) = row["mesh"], row["per100m2"], row["scale"]
        assert density > 0 and 0 < small <= large, row
        asset = unreal.load_asset(f"/Game/Valley/{mesh}")
        assert isinstance(asset, unreal.StaticMesh), mesh
        top_m = asset.get_bounding_box().max.z / 100
        assert low_m <= top_m and top_m * large <= high_m, (mesh, top_m, large)
        nanite = asset.get_editor_property("nanite_settings").copy()
        nanite.enabled = True
        nanite.shape_preservation = getattr(
            unreal.NaniteShapePreservation, grass["shapePreservation"]
        )
        asset.set_editor_property("nanite_settings", nanite)
        assert asset.get_editor_property("nanite_settings").shape_preservation == getattr(
            unreal.NaniteShapePreservation, grass["shapePreservation"]
        ), mesh
        assert lib.save_loaded_asset(asset, only_if_is_dirty=False), mesh
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
            assert (
                abs(
                    ml.get_material_instance_scalar_parameter_value(instance, "Brightness")
                    - entry["brightness"]
                )
                < 1e-6
            )
            assert lib.save_loaded_asset(instance), mesh
            overrides.append(instance)
        meshes[name, mesh] = asset, overrides
    # UE's landscape exporter requires a non-null type for each named output. Empty varieties
    # retain the GPU mask while making Landscape Grass incapable of spawning any instances.
    mask = rebuilt(f"GT_{title}", unreal.LandscapeGrassType, unreal.LandscapeGrassTypeFactory())
    mask.set_editor_property("grass_varieties", [])
    assert len(mask.get_editor_property("grass_varieties")) == 0
    assert lib.save_loaded_asset(mask)


template_path = "/PCG/GraphTemplates/TPL_Showcase_RuntimeGrassGPU"
template = unreal.load_asset(template_path)
assert isinstance(template, unreal.PCGGraph)
template_nodes = list(template.get_editor_property("nodes"))
(hlsl_template,) = [
    n.get_settings()
    for n in template_nodes
    if isinstance(n.get_settings(), unreal.PCGCustomHLSLSettings)
]
(landscape_template,) = [
    n.get_settings()
    for n in template_nodes
    if isinstance(n.get_settings(), unreal.PCGGetLandscapeSettings)
]
assert template.get_editor_property("use_hierarchical_generation")


def shader(row, width, seed):
    small, large = row["scale"]
    asset, _ = meshes[name, row["mesh"]]
    box = asset.get_bounding_box()
    return f"""
float3 Min = GetComponentBoundsMin();
float3 Max = GetComponentBoundsMax();
float3 Position = CreateGrid2D(ElementIndex, NumElements, Min, Max);
uint Seed = ComputeSeedFromPosition(Position) + {seed}u;
Position.xy = lerp(Min.xy, Max.xy, float2(FRand(Seed), FRand(Seed)));
Position.z = Landscape_GetHeight(Position);
float Weight = GrassMaps_SampleWorldPos(0, Position.xy).x;
if (FRand(Seed) >= Weight) {{
    Out_RemovePoint(Out_DataIndex, ElementIndex);
    return;
}}
float Scale = lerp({small}, {large}, FRand(Seed));
float3 Normal = Landscape_GetNormal(Position);
// A landscape heightfield has upward normals, so the up-to-normal quaternion is nonsingular.
FQuat Align = normalize(FQuat(-Normal.y, Normal.x, 0, 1 + Normal.z));
FQuat Rotation = QuatMultiply(Align,
    QuatFromAxisAngle(float3(0, 0, 1), FRand(Seed) * 6.283185307));
Out_SetPosition(Out_DataIndex, ElementIndex, Position);
Out_SetRotation(Out_DataIndex, ElementIndex, Rotation);
Out_SetScale(Out_DataIndex, ElementIndex, Scale * float3({width}, {width}, 1));
Out_SetBoundsMin(Out_DataIndex, ElementIndex, float3({box.min.x}, {box.min.y}, {box.min.z}));
Out_SetBoundsMax(Out_DataIndex, ElementIndex, float3({box.max.x}, {box.max.y}, {box.max.z}));
Out_SetSeed(Out_DataIndex, ElementIndex, Seed);
"""


graphs = {}
counts = {}
for profile, bands in grass["profiles"].items():
    path = f"{grass['folder']}/PCG_{profile.title()}"
    graph = (
        unreal.load_asset(path)
        if lib.does_asset_exist(path)
        else lib.duplicate_asset(template_path, path)
    )
    assert isinstance(graph, unreal.PCGGraph)
    for node in list(graph.get_editor_property("nodes")):
        graph.remove_node(node)
    set_properties(
        graph,
        is_template=False,
        user_parameters=unreal.InstancedPropertyBag(),
        use_hierarchical_generation=True,
        use2d_grid=True,
        hi_gen_grid_size=getattr(unreal.PCGHiGenGrid, f"GRID{max(b['gridM'] for b in bands)}"),
    )
    radii = graph.get_editor_property("generation_radii")
    radii.set_editor_property(
        "cleanup_radius_scalar", unreal.PerQualityLevelFloat(default=grass["cleanup"])
    )
    radii.set_editor_property("radius_unbounded", unreal.PerQualityLevelFloat(default=1000000))
    landscape, settings = graph.add_node_copy(landscape_template)
    sampling = settings.get_editor_property("sampling_properties")
    set_properties(sampling, get_layer_weights=False, sample_virtual_textures=False)
    settings.set_editor_property("sampling_properties", sampling)
    counts[profile] = []
    for band_index, band in enumerate(bands):
        grid_m, reach_m = band["gridM"], band["reachM"]
        assert grid_m in (16, 32, 64, 128) and reach_m >= grid_m
        assert 0 < band["density"] <= 4 and band["width"] >= 1
        radii.set_editor_property(
            f"radius{grid_m * 100}", unreal.PerQualityLevelFloat(default=reach_m * 100)
        )
        grid, settings = graph.add_node_of_type(unreal.PCGHiGenGridSizeSettings)
        settings.set_editor_property(
            "hi_gen_grid_size", getattr(unreal.PCGHiGenGrid, f"GRID{grid_m}")
        )
        assert graph.add_edge(landscape, "Out", grid, "In")
        for name, entry in grass["types"].items():
            masks, settings = graph.add_node_of_type(unreal.PCGGenerateLandscapeTexturesSettings)
            set_properties(
                settings,
                selected_grass_types=[name],
                exclude_selected_grass_types=False,
                generate_height_map=False,
                output_texture_array=False,
                num_landscape_components_generated_per_frame=16,
            )
            assert graph.add_edge(grid, "Out", masks, "Landscape")
            for index, row in enumerate(entry["varieties"]):
                generator, settings = graph.add_node_copy(hlsl_template)
                count = math.ceil(grid_m * grid_m * row["per100m2"] / 100 * band["density"])
                pins = [
                    set_properties(
                        unreal.PCGPinProperties(),
                        label=label,
                        allow_multiple_data=False,
                        allowed_types=unreal.PCGDataTypeIdentifierHelpers.get_identifier_from_class(
                            kind
                        ),
                    )
                    for label, kind in (
                        ("Landscape", unreal.PCGLandscapeData),
                        ("GrassMaps", unreal.PCGTextureData),
                    )
                ]
                set_properties(
                    settings,
                    input_pins=pins,
                    num_elements=count,
                )
                unreal.ScenarioGrass.set_kernel_source(
                    settings, shader(row, band["width"], 7919 * (index + 1) + band_index)
                )
                assert graph.add_edge(grid, "Out", generator, "Landscape")
                assert graph.add_edge(masks, "GrassMaps", generator, "GrassMaps")
                spawner, settings = graph.add_node_of_type(unreal.PCGStaticMeshSpawnerSettings)
                settings.set_editor_property("execute_on_gpu", True)
                settings.set_mesh_selector_type(unreal.PCGMeshSelectorWeighted)
                asset, overrides = meshes[name, row["mesh"]]
                descriptor = set_properties(
                    unreal.PCGSoftISMComponentDescriptor(),
                    static_mesh=asset,
                    override_materials=overrides,
                    cast_shadow=False,
                    cast_dynamic_shadow=False,
                    affect_distance_field_lighting=False,
                    visible_in_ray_tracing=False,
                    instance_min_draw_distance=(
                        (bands[band_index - 1]["reachM"] - bands[band_index - 1]["gridM"]) * 100
                        if band_index
                        else 0
                    ),
                    instance_start_cull_distance=0,
                    instance_end_cull_distance=reach_m * 100,
                )
                body = descriptor.get_editor_property("body_instance")
                body.set_editor_property("collision_profile_name", "NoCollision")
                descriptor.set_editor_property("body_instance", body)
                choice = set_properties(
                    unreal.PCGMeshSelectorWeightedEntry(), descriptor=descriptor, weight=1
                )
                settings.get_editor_property("mesh_selector_parameters").set_editor_property(
                    "mesh_entries", [choice]
                )
                assert graph.add_edge(generator, "Out", spawner, "In")
                counts[profile].append(
                    {"gridM": grid_m, "type": name, "mesh": row["mesh"], "pointsPerCell": count}
                )
    graph.set_editor_property("generation_radii", radii)
    assert lib.save_loaded_asset(graph)
    graphs[profile] = graph

existing = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.ScenarioGrass)]
assert len(existing) <= 1
for actor in existing:
    assert actors.destroy_actor(actor)
lo, hi = world["searchBoundsCm"]["min"], world["searchBoundsCm"]["max"]
center = unreal.Vector((lo["x"] + hi["x"]) / 2, (lo["y"] + hi["y"]) / 2, (lo["z"] + hi["z"]) / 2)
actor = actors.spawn_actor_from_class(unreal.ScenarioGrass, center)
actor.set_actor_label("valley.grass")
actor.get_editor_property("bounds").set_box_extent(
    unreal.Vector((hi["x"] - lo["x"]) / 2, (hi["y"] - lo["y"]) / 2, (hi["z"] - lo["z"]) / 2), False
)
set_properties(actor, studio_graph=graphs["studio"], mission_graph=graphs["mission"])
actor.configure()
for graph in graphs.values():
    assert lib.save_loaded_asset(graph)
(pcg_world,) = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.PCGWorldActor)]
pcg_world.set_actor_label("valley.pcg")
pcg_world.set_editor_property("treat_editor_viewport_as_generation_source", False)
assert not pcg_world.get_editor_property("treat_player_controllers_as_generation_sources")
assert levels.save_current_level()
report.write_text(
    json.dumps(
        {
            "template": template_path,
            "profiles": grass["profiles"],
            "shapePreservation": grass["shapePreservation"],
            "allocations": counts,
        },
        indent=2,
    )
    + "\n"
)
unreal.log("SNR_VALLEY_GPU_GRASS_BUILT")
