"""Import the pinned Rocketbox facial meshes and author explicit Unreal materials."""

import argparse
import hashlib
import json
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LIB = unreal.EditorAssetLibrary
MATERIALS = unreal.MaterialEditingLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()


def import_file(path, folder, options):
    task = unreal.AssetImportTask()
    for key, value in {
        "filename": str(path),
        "destination_path": folder,
        "automated": True,
        "replace_existing": False,
        "save": False,
    }.items():
        task.set_editor_property(key, value)
    if options is not None:
        task.set_editor_property("options", options)
        task.set_editor_property("factory", unreal.FbxFactory())
    TOOLS.import_asset_tasks([task])
    assert task.imported_object_paths, path
    return [unreal.load_asset(p) for p in task.imported_object_paths]


def material(folder, name, textures, roughness):
    asset = TOOLS.create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    assert asset is not None, name

    def node(kind, **values):
        result = MATERIALS.create_material_expression(asset, kind)
        for key, value in values.items():
            result.set_editor_property(key, value)
        return result

    def connect(expression, output, prop):
        assert MATERIALS.connect_material_property(expression, output, prop)

    glasses = name.endswith("_glasses")
    color_name = name + ("_opacity_color" if glasses else "_color")
    color = node(unreal.MaterialExpressionTextureSample, texture=textures[color_name])
    connect(color, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    connect(
        node(unreal.MaterialExpressionConstant, r=roughness),
        "",
        unreal.MaterialProperty.MP_ROUGHNESS,
    )
    if glasses:
        asset.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        asset.set_editor_property(
            "translucency_lighting_mode",
            unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING,
        )
        asset.set_editor_property("two_sided", True)
        connect(color, "A", unreal.MaterialProperty.MP_OPACITY)
    if name.endswith("_opacity"):
        asset.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
        asset.set_editor_property("two_sided", True)
        asset.set_editor_property("opacity_mask_clip_value", 0.333)
        connect(color, "A", unreal.MaterialProperty.MP_OPACITY_MASK)
    else:
        normal = node(
            unreal.MaterialExpressionTextureSample,
            texture=textures[name + "_normal"],
            sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL,
        )
        connect(normal, "RGB", unreal.MaterialProperty.MP_NORMAL)
        specular = node(
            unreal.MaterialExpressionTextureSample,
            texture=textures[name + "_specular"],
            sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR,
        )
        connect(specular, "R", unreal.MaterialProperty.MP_SPECULAR)
    MATERIALS.set_material_usage(asset, unreal.MaterialUsage.MATUSAGE_SKELETAL_MESH)
    MATERIALS.set_material_usage(asset, unreal.MaterialUsage.MATUSAGE_MORPH_TARGETS)
    MATERIALS.recompile_material(asset)
    assert LIB.save_loaded_asset(asset)
    return asset


def character(recipe, vendor):
    folder = recipe["asset_folder"]
    assert not LIB.does_directory_exist(folder), folder
    textures = {}
    for path in sorted((vendor / recipe["textures"]).glob("*.tga")):
        imported = import_file(path, folder + "/Textures", None)
        assert len(imported) == 1 and isinstance(imported[0], unreal.Texture2D), path
        texture = imported[0]
        is_normal = path.stem.endswith("_normal")
        texture.set_editor_property("srgb", path.stem.endswith("_color"))
        texture.set_editor_property("never_stream", True)
        texture.set_editor_property(
            "compression_settings",
            unreal.TextureCompressionSettings.TC_NORMALMAP
            if is_normal
            else unreal.TextureCompressionSettings.TC_DEFAULT,
        )
        textures[path.stem] = texture
        assert LIB.save_loaded_asset(texture)
    options = unreal.FbxImportUI()
    for key, value in {
        "automated_import_should_detect_type": False,
        "mesh_type_to_import": unreal.FBXImportType.FBXIT_SKELETAL_MESH,
        "import_as_skeletal": True,
        "import_mesh": True,
        "import_animations": False,
        "import_materials": False,
        "import_textures": False,
        "create_physics_asset": False,
    }.items():
        options.set_editor_property(key, value)
    for key, value in {
        "import_morph_targets": True,
        "import_mesh_lods": False,
        "convert_scene": True,
        "convert_scene_unit": True,
        "force_front_x_axis": False,
        "normal_import_method": unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS_AND_TANGENTS,
    }.items():
        options.skeletal_mesh_import_data.set_editor_property(key, value)
    imported = import_file(vendor / recipe["fbx"], folder, options)
    meshes = [asset for asset in imported if isinstance(asset, unreal.SkeletalMesh)]
    assert len(meshes) == 1, [asset.get_path_name() for asset in imported]
    mesh = meshes[0]
    skeleton = mesh.get_editor_property("skeleton")
    assert skeleton is not None
    assert LIB.save_loaded_asset(skeleton)
    package = skeleton.get_path_name().split(".")[0].removeprefix("/Game/")
    assert (ROOT / "world/unreal/ScenarioStudio/Content" / (package + ".uasset")).is_file()
    slots = mesh.get_editor_property("materials")
    records = []
    for index, slot in enumerate(slots):
        name = str(slot.material_slot_name)
        assert name.startswith(recipe["texture_prefix"] + "_"), name
        roughness = 0.1 if name.endswith("_glasses") else 0.45 if name.endswith("_head") else 0.7
        slot.material_interface = material(folder, name, textures, roughness)
        slots[index] = slot
        records.append(
            {
                "name": name,
                "roughness": roughness,
                "material": slot.material_interface.get_path_name(),
            }
        )
    mesh.set_editor_property("materials", slots)
    assigned = mesh.get_editor_property("materials")
    assert [slot.material_interface.get_path_name() for slot in assigned] == [
        item["material"] for item in records
    ]
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    for asset in imported:
        assert LIB.save_loaded_asset(asset)
    editor = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
    assert editor.get_lod_count(mesh) == 1
    texture_info = [
        {"name": name, "size": [texture.blueprint_get_size_x(), texture.blueprint_get_size_y()]}
        for name, texture in textures.items()
    ]
    for item in texture_info:
        minimum = 256 if "_glasses_" in item["name"] else 1024
        assert min(item["size"]) >= minimum, item
    return {
        "id": recipe["id"],
        "name": recipe["name"],
        "mesh": mesh.get_path_name(),
        "skeleton": skeleton.get_path_name(),
        "vertices": editor.get_num_verts(mesh, 0),
        "sections": editor.get_num_sections(mesh, 0),
        "materials": records,
        "textures": texture_info,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(args.manifest.read_text())
    vendor = ROOT / manifest["vendor"]
    for item in manifest["files"]:
        data = (vendor / item["path"]).read_bytes()
        assert len(data) == item["bytes"]
        blob = b"blob " + str(len(data)).encode() + b"\0" + data
        assert hashlib.sha1(blob).hexdigest() == item["git_blob_sha1"], item["path"]
    records = []
    for recipe in manifest["characters"]:
        result = character(recipe, vendor)
        records.append(result)
        (args.output / (recipe["id"] + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    (args.output / "result.json").write_text(json.dumps(records, indent=2) + "\n")
    unreal.log("SNR_ROCKETBOX_IMPORT_COMPLETE")


if __name__ == "__main__":
    main()
