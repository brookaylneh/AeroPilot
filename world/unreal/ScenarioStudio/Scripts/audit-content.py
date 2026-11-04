"""Prove that Content is exactly the cook closure: every reference resolves, nothing else is kept."""

import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

import unreal

PROJECT = Path(unreal.Paths.project_dir()).resolve()
CONTENT = PROJECT / "Content"
ROOT = PROJECT.parents[2]
sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
assert len(sites) == 1
sys.path.extend([str(ROOT), str(sites[0])])
from mission_planner.environment import load_env  # noqa: E402
from world.unreal.grass_budget import scene_budget  # noqa: E402

load_env()
report_path = Path(os.environ["SNR_EVIDENCE_DIR"]) / "content-manifest.json"
assert not report_path.exists(), "Archive the previous content manifest before a new run"

config = (PROJECT / "Config/DefaultGame.ini").read_text()
maps = re.findall(r'^\+MapsToCook=\(FilePath="([^"]+)"\)$', config, re.M)
directories = re.findall(r'^\+DirectoriesToAlwaysCook=\(Path="([^"]+)"\)$', config, re.M)
assert maps == ["/Game/OpenMeadow/OpenMeadow"], maps
assert directories == ["/Game/Scenario/Rocketbox", "/AirSim"], directories

files = sorted(p for p in CONTENT.rglob("*") if p.is_file())
assert all(p.suffix in (".uasset", ".umap") for p in files), [
    str(p) for p in files if p.suffix not in (".uasset", ".umap")
]
on_disk = {"/Game/" + p.relative_to(CONTENT).with_suffix("").as_posix(): p for p in files}
assert len(on_disk) == len(files), "A package exists as both .uasset and .umap"

registry = unreal.AssetRegistryHelpers.get_asset_registry()
registry.search_all_assets(True)
options = unreal.AssetRegistryDependencyOptions(
    include_soft_package_references=True,
    include_hard_package_references=True,
    include_searchable_names=False,
    include_soft_management_references=False,
    include_hard_management_references=False,
)


def packages_under(path):
    assets = registry.get_assets_by_path(path, recursive=True, include_only_on_disk_assets=True)
    return {str(a.package_name) for a in assets}


roots = set(maps)
for directory in directories:
    found = packages_under(directory)
    assert found, f"No packages under always-cooked {directory}"
    roots |= found
# The pinned Cosys plugin content has dangling references of its own; they are recorded apart.
reachable, missing, plugin_missing, queue = set(), [], [], sorted(roots)
while queue:
    package = queue.pop()
    if package in reachable:
        continue
    reachable.add(package)
    for dependency in registry.get_dependencies(package, options) or []:
        name = str(dependency)
        if name.startswith("/Script/"):
            continue
        exists = (
            name in on_disk
            if name.startswith("/Game/")
            else bool(registry.get_assets_by_package_name(name, include_only_on_disk_assets=True))
        )
        if not exists:
            (missing if package.startswith("/Game/") else plugin_missing).append(
                {"package": package, "dependency": name}
            )
        elif name not in reachable:
            queue.append(name)

redirectors, empty = [], []
for package in sorted(on_disk):
    assets = registry.get_assets_by_package_name(package, include_only_on_disk_assets=True)
    if not assets:
        empty.append(package)
    if any(str(a.asset_class_path.asset_name) == "ObjectRedirector" for a in assets):
        redirectors.append(package)

grass = json.loads((ROOT / "world/unreal/valley-grass.json").read_text())
for name, entry in grass["types"].items():
    title = "".join(part.title() for part in name.split("-"))
    mask = unreal.load_asset(f"{grass['folder']}/GT_{title}")
    assert mask and not mask.get_editor_property("grass_varieties"), name
    for row in entry["varieties"]:
        mesh = unreal.load_asset(f"/Game/Valley/{row['mesh']}")
        nanite = mesh.get_editor_property("nanite_settings")
        assert nanite.enabled and nanite.shape_preservation == getattr(
            unreal.NaniteShapePreservation, grass["shapePreservation"]
        ), (row["mesh"], nanite.enabled, nanite.shape_preservation)
        assert mesh.get_num_nanite_triangles() > 0, row["mesh"]
        for index, slot in enumerate(mesh.get_editor_property("static_materials")):
            instance = unreal.load_asset(
                f"{grass['folder']}/MI_{title}_{row['mesh'].split('/')[-1].removeprefix('SM_')}_{index}"
            )
            assert instance and not instance.get_editor_property("scalar_parameter_values")
            material = instance.get_base_material()
            assert (
                material.get_editor_property("shading_model")
                == unreal.MaterialShadingModel.MSM_TWO_SIDED_FOLIAGE
            )
            assert material.get_editor_property("two_sided")
            for usage in (
                "used_with_nanite",
                "used_with_voxels",
                "used_with_instanced_static_meshes",
            ):
                assert material.get_editor_property(usage), (material, usage)
            assert unreal.MaterialEditingLibrary.get_material_property_input_node(
                material, unreal.MaterialProperty.MP_SUBSURFACE_COLOR
            ), material
            for parameter in ("BC", "N", "ORM"):
                assert unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(
                    instance, parameter
                ) == unreal.MaterialEditingLibrary.get_material_instance_texture_parameter_value(
                    slot.material_interface, parameter
                ), (instance, parameter)
for profile in grass["profiles"]:
    graph = unreal.load_asset(f"{grass['folder']}/PCG_{profile.title()}")
    assert graph.get_editor_property("use_hierarchical_generation") and graph.get_editor_property(
        "use2d_grid"
    )
    nodes = [node.get_settings() for node in graph.get_editor_property("nodes")]
    spawners = [node for node in nodes if isinstance(node, unreal.PCGStaticMeshSpawnerSettings)]
    assert spawners and all(node.get_editor_property("execute_on_gpu") for node in spawners)
    bands = grass["profiles"][profile]
    radii = graph.get_editor_property("generation_radii")
    for band in bands:
        assert (
            radii.get_editor_property(f"radius{band['gridM'] * 100}").get_editor_property("default")
            == band["reachM"] * 100
        ), profile
    expected_distances = [
        (
            (bands[index - 1]["reachM"] - bands[index - 1]["gridM"]) * 100 if index else 0,
            band["reachM"] * 100,
        )
        for index, band in enumerate(bands)
        for entry in grass["types"].values()
        for _ in entry["varieties"]
    ]
    for spawner, (minimum, maximum) in zip(spawners, expected_distances, strict=True):
        (choice,) = spawner.get_editor_property("mesh_selector_parameters").get_editor_property(
            "mesh_entries"
        )
        descriptor = choice.get_editor_property("descriptor")
        assert descriptor.get_editor_property("instance_min_draw_distance") == minimum, profile
        assert descriptor.get_editor_property("instance_end_cull_distance") == maximum, profile
    assert sorted(
        node.get_editor_property("num_elements")
        for node in nodes
        if isinstance(node, unreal.PCGCustomHLSLSettings)
    ) == sorted(
        math.ceil(band["gridM"] ** 2 * row["per100m2"] / 100 * band["density"])
        for band in grass["profiles"][profile]
        for entry in grass["types"].values()
        for row in entry["varieties"]
    ), profile

world = json.loads((ROOT / "world/cosys/world.json").read_text())
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
assert levels.load_level(maps[0])
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
lighting = json.loads((ROOT / "world/unreal/valley-lighting.json").read_text())
level_actors = actors.get_all_level_actors()
(sun,) = [actor for actor in level_actors if isinstance(actor, unreal.DirectionalLight)]
(sky,) = [actor for actor in level_actors if isinstance(actor, unreal.SkyLight)]
(cloud,) = [actor for actor in level_actors if isinstance(actor, unreal.VolumetricCloud)]
assert cloud.get_actor_label() == "sky.clouds"
for component, expected in (
    (sun.get_component_by_class(unreal.DirectionalLightComponent), lighting["sunIntensityLux"]),
    (sky.get_component_by_class(unreal.SkyLightComponent), lighting["skyLightIntensity"]),
):
    assert math.isclose(component.get_editor_property("intensity"), expected, rel_tol=1e-6)
(look,) = [actor for actor in level_actors if actor.get_actor_label() == "look.daylight"]
settings = look.get_editor_property("settings")
for key, expected in {
    "auto_exposure_bias": -lighting["exposureEv100"],
    "white_temp": lighting["whiteBalanceKelvin"],
    "local_exposure_highlight_contrast_scale": lighting["localExposure"]["highlightContrast"],
    "local_exposure_shadow_contrast_scale": lighting["localExposure"]["shadowContrast"],
}.items():
    assert settings.get_editor_property(f"override_{key}"), key
    assert math.isclose(settings.get_editor_property(key), expected, rel_tol=1e-6), key
authored_instances = sum(
    component.get_instance_count()
    if isinstance(component, unreal.InstancedStaticMeshComponent)
    else 1
    for actor in actors.get_all_level_actors()
    for component in actor.get_components_by_class(unreal.PrimitiveComponent)
)
(generating_components,) = re.findall(
    r"^pcg.RuntimeGeneration.NumGeneratingComponents=(\d+)$",
    (PROJECT / "Config/DefaultEngine.ini").read_text(),
    re.M,
)
instance_budget = scene_budget(
    grass, len(world["vehicles"]), authored_instances, int(generating_components)
)
unreal.log("SNR_GPU_SCENE_BUDGET " + json.dumps(instance_budget))
people = json.loads((ROOT / "world/unreal/creator-people.json").read_text())
catalog = unreal.load_asset("/Game/OpenMeadow/Catalog")
assert catalog, "Missing /Game/OpenMeadow/Catalog"
catalog_people = []
for preset in catalog.get_editor_property("people"):
    actor_class = preset.get_editor_property("actor_class")
    catalog_people.append(
        {
            "id": str(preset.get_editor_property("id")),
            "actorClass": actor_class.get_path_name() if actor_class else None,
        }
    )
catalog_regions = []
for region in catalog.get_editor_property("regions"):
    lo, hi = region.get_editor_property("min"), region.get_editor_property("max")
    catalog_regions.append(
        {
            "id": str(region.get_editor_property("id")),
            "minCm": {"x": lo.x, "y": lo.y, "z": lo.z},
            "maxCm": {"x": hi.x, "y": hi.y, "z": hi.z},
        }
    )
bounds = world["searchBoundsCm"]
catalog_errors = []
if [p["id"] for p in catalog_people] != [p["id"] for p in people]:
    catalog_errors.append("Catalog people differ from world/unreal/creator-people.json")
for person in catalog_people:
    if not (person["actorClass"] or "").startswith("/Game/Scenario/Rocketbox/"):
        catalog_errors.append(f"{person['id']} actor is not a Rocketbox runtime class")
if str(catalog.get_editor_property("asset_version")) != world["environmentAssetVersion"]:
    catalog_errors.append("Catalog asset version differs from environmentAssetVersion")
if len(catalog_regions) != 1:
    catalog_errors.append(f"Expected one region, found {len(catalog_regions)}")
for region in catalog_regions:
    for key, bound in (("minCm", bounds["min"]), ("maxCm", bounds["max"])):
        if any(abs(region[key][axis] - bound[axis]) >= 0.1 for axis in "xyz"):
            catalog_errors.append(f"Region {region['id']} {key} differs from searchBoundsCm")

kept = [p for p in sorted(on_disk) if p in reachable]
unreferenced = [p for p in sorted(on_disk) if p not in reachable]
report = {
    "gpuSceneBudget": instance_budget,
    "content": str(CONTENT.relative_to(ROOT)),
    "maps": maps,
    "alwaysCookDirectories": directories,
    "rootPackages": len(roots),
    "reachablePackages": len(reachable),
    "missing": missing,
    "pluginMissing": plugin_missing,
    "redirectors": redirectors,
    "emptyPackages": empty,
    "unreferenced": [{"package": p, "bytes": on_disk[p].stat().st_size} for p in unreferenced],
    "unreferencedBytes": sum(on_disk[p].stat().st_size for p in unreferenced),
    "catalog": {
        "assetVersion": str(catalog.get_editor_property("asset_version")),
        "people": catalog_people,
        "regions": catalog_regions,
        "errors": catalog_errors,
    },
    "packages": [
        {
            "package": p,
            "file": on_disk[p].relative_to(CONTENT).as_posix(),
            "bytes": on_disk[p].stat().st_size,
            "sha256": hashlib.sha256(on_disk[p].read_bytes()).hexdigest(),
        }
        for p in kept
    ],
}
report["packageCount"] = len(kept)
report["totalBytes"] = sum(p["bytes"] for p in report["packages"])
report["passed"] = not (missing or redirectors or empty or unreferenced or catalog_errors)
report_path.write_text(json.dumps(report, indent=2) + "\n")
unreal.log(
    "SNR_CONTENT_AUDIT "
    + json.dumps({k: v for k, v in report.items() if k not in ("packages", "unreferenced")})
)
assert report["passed"], f"Content audit failed; see {report_path}"
