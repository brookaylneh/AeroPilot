"""Duplicate the active meadow vegetation for a measured Nanite variant."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import unreal

output = Path(sys.argv[1])
output.mkdir()
root = Path(unreal.Paths.project_dir()).resolve()
level_file = root / "Content/OpenMeadow/OpenMeadow.umap"
shutil.copy2(level_file, output / "OpenMeadow-before.umap")
lib = unreal.EditorAssetLibrary
ml = unreal.MaterialEditingLibrary
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
unreal.load_module("StaticMeshEditor")
meshes = unreal.new_object(unreal.StaticMeshEditorSubsystem)
folder = "/Game/OpenMeadow/Nanite"
assert not lib.does_directory_exist(folder), folder
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
landscape = next(a for a in actors.get_all_level_actors() if isinstance(a, unreal.Landscape))
material = landscape.get_editor_property("landscape_material")
report = {"originalMaterial": material.get_path_name(), "meshes": [], "originals": {}}


def duplicate(asset, name):
    package = asset.get_path_name().split(".")[0]
    path = root / "Content" / (package.removeprefix("/Game/") + ".uasset")
    assert path.is_file(), path
    report["originals"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    destination = folder + "/" + name
    assert not lib.does_asset_exist(destination), destination
    result = lib.duplicate_asset(package, destination)
    assert result, destination
    return result


ground = duplicate(material, "M_MeadowNanite")
outputs = [
    e
    for e in ml.get_material_expressions(ground)
    if isinstance(e, unreal.MaterialExpressionLandscapeGrassOutput)
]
assert len(outputs) == 1
entries = list(outputs[0].get_editor_property("grass_types"))
copies = {}
for entry in entries:
    original_type = entry.get_editor_property("grass_type")
    grass = duplicate(original_type, original_type.get_name())
    varieties = list(grass.get_editor_property("grass_varieties"))
    for variety in varieties:
        original = variety.get_editor_property("grass_mesh")
        path = original.get_path_name()
        if path not in copies:
            mesh = duplicate(original, original.get_name())
            settings = mesh.get_editor_property("nanite_settings")
            settings.set_editor_property("enabled", True)
            settings.set_editor_property(
                "shape_preservation", unreal.NaniteShapePreservation.PRESERVE_AREA
            )
            settings.set_editor_property("fallback_relative_error", 0.0)
            meshes.set_nanite_settings(mesh, settings, True)
            assert lib.save_loaded_asset(mesh)
            copies[path] = mesh
            report["meshes"].append(
                {
                    "source": path,
                    "nanite": mesh.get_path_name(),
                    "shapePreservation": "PreserveArea",
                    "fallbackRelativeError": 0,
                }
            )
        variety.set_editor_property("grass_mesh", copies[path])
    grass.set_editor_property("grass_varieties", varieties)
    assert lib.save_loaded_asset(grass)
    entry.set_editor_property("grass_type", grass)
outputs[0].set_editor_property("grass_types", entries)
ml.recompile_material(ground)
assert lib.save_loaded_asset(ground)
landscape.set_editor_property("landscape_material", ground)
assert levels.save_current_level()
for path, digest in report["originals"].items():
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
report["material"] = ground.get_path_name()
report["mapBeforeSha256"] = hashlib.sha256(
    (output / "OpenMeadow-before.umap").read_bytes()
).hexdigest()
report["mapAfterSha256"] = hashlib.sha256(level_file.read_bytes()).hexdigest()
(output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
unreal.log("SNR_MEADOW_NANITE_AUTHORED")
