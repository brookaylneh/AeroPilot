"""Prove that Content is exactly the cook closure: every reference resolves, nothing else is kept."""

import hashlib
import json
import os
import re
from pathlib import Path

import unreal

PROJECT = Path(unreal.Paths.project_dir()).resolve()
CONTENT = PROJECT / "Content"
ROOT = PROJECT.parents[2]
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

world = json.loads((ROOT / "world/cosys/world.json").read_text())
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
