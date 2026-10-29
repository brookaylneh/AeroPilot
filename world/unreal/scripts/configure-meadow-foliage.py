"""Duplicate active ground cover for a density and visibility-distance comparison."""

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
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
landscape = next(a for a in actors.get_all_level_actors() if isinstance(a, unreal.Landscape))
source = landscape.get_editor_property("landscape_material")
folder = "/Game/OpenMeadow/Quality/Dense"
assert not lib.does_directory_exist(folder), folder
material = lib.duplicate_asset(source.get_path_name().split(".")[0], folder + "/M_MeadowDense")
assert material
outputs = [
    e
    for e in ml.get_material_expressions(material)
    if isinstance(e, unreal.MaterialExpressionLandscapeGrassOutput)
]
assert len(outputs) == 1
entries = list(outputs[0].get_editor_property("grass_types"))
changes = []
for entry in entries:
    source_type = entry.get_editor_property("grass_type")
    grass = lib.duplicate_asset(
        source_type.get_path_name().split(".")[0], folder + "/" + source_type.get_name()
    )
    assert grass
    varieties = list(grass.get_editor_property("grass_varieties"))
    for variety in varieties:
        values = {}
        for key, multiplier in [
            ("grass_density", 1.25),
            ("start_cull_distance", 1.5),
            ("end_cull_distance", 1.5),
        ]:
            field = variety.get_editor_property(key)
            before = field.get_editor_property("default")
            after = before * multiplier if key == "grass_density" else round(before * multiplier)
            assert after > before > 0, (key, before, after)
            field.set_editor_property("default", after)
            variety.set_editor_property(key, field)
            values[key] = {"before": before, "after": after}
        changes.append({"type": grass.get_path_name(), "values": values})
    grass.set_editor_property("grass_varieties", varieties)
    assert lib.save_loaded_asset(grass)
    entry.set_editor_property("grass_type", grass)
outputs[0].set_editor_property("grass_types", entries)
ml.recompile_material(material)
assert lib.save_loaded_asset(material)
landscape.set_editor_property("landscape_material", material)
assert levels.save_current_level()
report = {
    "sourceMaterial": source.get_path_name(),
    "material": material.get_path_name(),
    "changes": changes,
    "mapBeforeSha256": hashlib.sha256((output / "OpenMeadow-before.umap").read_bytes()).hexdigest(),
    "mapAfterSha256": hashlib.sha256(level_file.read_bytes()).hexdigest(),
}
(output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
unreal.log("SNR_MEADOW_FOLIAGE_AUTHORED")
