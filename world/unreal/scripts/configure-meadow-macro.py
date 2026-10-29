"""Author landscape colour variation at three metre scales without changing sensor exposure."""

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
source_package = source.get_path_name().split(".")[0]
source_file = root / "Content" / (source_package.removeprefix("/Game/") + ".uasset")
source_hash = hashlib.sha256(source_file.read_bytes()).hexdigest()
destination = "/Game/OpenMeadow/Quality/M_MeadowMacro"
assert not lib.does_asset_exist(destination), destination
material = lib.duplicate_asset(source_package, destination)
assert material
base = ml.get_material_property_input_node(material, unreal.MaterialProperty.MP_BASE_COLOR)
base_output = ml.get_material_property_input_node_output_name(
    material, unreal.MaterialProperty.MP_BASE_COLOR
)
assert base
texture = unreal.load_asset("/Engine/EngineMaterials/T_Default_MacroVariation")
assert texture


def node(kind, **properties):
    result = ml.create_material_expression(material, kind)
    for key, value in properties.items():
        result.set_editor_property(key, value)
    return result


def connect(source, target, pin, output_name=""):
    assert ml.connect_material_expressions(source, output_name, target, pin)


position = node(unreal.MaterialExpressionWorldPosition)
xy = node(unreal.MaterialExpressionComponentMask, r=True, g=True)
connect(position, xy, "")
scales = [1700, 6700, 23100]
samples = []
for scale in scales:
    uv = node(unreal.MaterialExpressionMultiply, const_b=1 / scale)
    connect(xy, uv, "A")
    sample = node(unreal.MaterialExpressionTextureSample, texture=texture)
    connect(uv, sample, "UVs")
    samples.append(sample)
first = node(unreal.MaterialExpressionAdd)
connect(samples[0], first, "A", "R")
connect(samples[1], first, "B", "R")
second = node(unreal.MaterialExpressionAdd)
connect(first, second, "A")
connect(samples[2], second, "B", "R")
mean = node(unreal.MaterialExpressionMultiply, const_b=1 / len(scales))
connect(second, mean, "A")
variation = node(unreal.MaterialExpressionLinearInterpolate, const_a=0.78, const_b=1.22)
connect(mean, variation, "Alpha")
colour = node(unreal.MaterialExpressionMultiply)
connect(base, colour, "A", base_output)
connect(variation, colour, "B")
clamped = node(unreal.MaterialExpressionSaturate)
connect(colour, clamped, "")
assert ml.connect_material_property(clamped, "", unreal.MaterialProperty.MP_BASE_COLOR)
ml.recompile_material(material)
assert lib.save_loaded_asset(material)
landscape.set_editor_property("landscape_material", material)
assert levels.save_current_level()
assert hashlib.sha256(source_file.read_bytes()).hexdigest() == source_hash
report = {
    "sourceMaterial": source.get_path_name(),
    "sourceSha256": source_hash,
    "material": material.get_path_name(),
    "texture": texture.get_path_name(),
    "scalesCm": scales,
    "albedoMultiplierRange": [0.78, 1.22],
    "mapBeforeSha256": hashlib.sha256((output / "OpenMeadow-before.umap").read_bytes()).hexdigest(),
    "mapAfterSha256": hashlib.sha256(level_file.read_bytes()).hexdigest(),
}
(output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
unreal.log("SNR_MEADOW_MACRO_AUTHORED")
