"""Conform a verified MetaHuman template head and apply its supplied skin maps."""

import argparse
import hashlib
import json
from pathlib import Path

import unreal


def import_asset(source, folder, name, factory, options):
    path = Path(__file__).resolve().parents[3] / source["path"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"], path
    assert not unreal.EditorAssetLibrary.does_asset_exist(folder + "/" + name)
    task = unreal.AssetImportTask()
    task.filename = str(path.resolve())
    task.destination_path = folder
    task.destination_name = name
    task.automated = True
    task.save = True
    task.replace_existing = False
    task.factory = factory
    task.options = options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    objects = task.get_objects()
    assert len(objects) == 1, task.imported_object_paths
    return objects[0]


def conform(recipe):
    folder = recipe["asset_folder"]
    assert not unreal.EditorAssetLibrary.does_directory_exist(folder), folder
    source = unreal.load_asset(recipe["source_character"])
    assert isinstance(source, unreal.MetaHumanCharacter)
    character = unreal.AssetToolsHelpers.get_asset_tools().duplicate_asset(
        recipe["asset_name"], folder, source
    )
    assert character is not None
    options = unreal.FbxImportUI()
    options.automated_import_should_detect_type = False
    options.mesh_type_to_import = unreal.FBXImportType.FBXIT_STATIC_MESH
    options.import_mesh = True
    options.import_as_skeletal = False
    options.import_materials = False
    options.import_textures = False
    options.static_mesh_import_data.combine_meshes = True
    options.static_mesh_import_data.generate_lightmap_u_vs = False
    options.static_mesh_import_data.auto_generate_collision = False
    mesh = import_asset(
        recipe["head"]["mesh"], folder, "HeadTemplate", unreal.FbxFactory(), options
    )
    assert isinstance(mesh, unreal.StaticMesh)
    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    assert subsystem.try_add_object_to_edit(character)
    try:
        params = unreal.ImportFromTemplateParams()
        params.match_vertices_by_u_vs = True
        params.use_eye_meshes = False
        params.use_teeth_mesh = False
        params.isolate_head_from_body = False
        params.alignment_options = unreal.MetaHumanAlignmentOptions.SCALING_ROTATION_TRANSLATION
        result = subsystem.import_from_template(character, mesh, None, None, None, params)
        assert result == unreal.ImportErrorCode.SUCCESS, str(result)
        settings = character.get_editor_property("skin_settings")
        settings.skin.u = recipe["skin"]["lightness"]
        settings.skin.v = recipe["skin"]["redness"]
        subsystem.commit_skin_settings(character, settings)
        assert unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
        return {
            "character": character.get_path_name(),
            "template": mesh.get_path_name(),
            "result": str(result),
            "match_vertices_by_uvs": True,
            "adapt_neck_to_body": True,
            "skin": recipe["skin"],
        }
    finally:
        subsystem.remove_object_to_edit(character)


def textures(recipe, output):
    textures = {}
    texture_settings = {}
    for name, source in recipe["head"]["textures"].items():
        texture = import_asset(
            source, recipe["asset_folder"], "Head_" + name, unreal.TextureFactory(), None
        )
        assert isinstance(texture, unreal.Texture2D)
        assert (texture.blueprint_get_size_x(), texture.blueprint_get_size_y()) == (4096, 4096)
        texture.set_editor_property("srgb", name == "Basecolor")
        texture.set_editor_property("flip_green_channel", False)
        texture.set_editor_property(
            "compression_settings",
            getattr(unreal.TextureCompressionSettings, source["compression"]),
        )
        assert unreal.EditorAssetLibrary.save_loaded_asset(texture, only_if_is_dirty=False)
        texture_settings[name] = {
            "asset": texture.get_path_name(),
            "pixels": [texture.blueprint_get_size_x(), texture.blueprint_get_size_y()],
            "srgb": texture.get_editor_property("srgb"),
            "flip_green_channel": texture.get_editor_property("flip_green_channel"),
            "compression": str(texture.get_editor_property("compression_settings")),
        }
        assert texture_settings[name]["srgb"] == (name == "Basecolor")
        assert not texture_settings[name]["flip_green_channel"]
        textures[name + " Baked"] = texture
    library = unreal.MaterialEditingLibrary
    report = {}
    assets = unreal.EditorAssetLibrary.list_assets(recipe["assembly_folder"], recursive=True)
    materials = [path for path in assets if "/Face/Materials/MI_Face_Skin_Baked_LOD" in path]
    assert len(materials) == 6, materials
    for path in materials:
        material = unreal.load_asset(path)
        names = {str(n) for n in library.get_texture_parameter_names(material)}
        assert textures.keys() <= names, (path, names)
        report[path] = {}
        for name, texture in textures.items():
            library.set_material_instance_texture_parameter_value(material, name, texture)
            actual = library.get_material_instance_texture_parameter_value(material, name)
            assert actual == texture, (path, name)
            report[path][name] = actual.get_path_name()
        library.update_material_instance(material)
        assert unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    exports = {}
    for part, name in (("Body", "T_Body_BC"), ("Face", "T_Head_BC")):
        path = (
            recipe["assembly_folder"] + "/" + recipe["asset_name"] + "/" + part + "/Baked/" + name
        )
        texture = unreal.load_asset(path)
        assert isinstance(texture, unreal.Texture2D)
        assert texture.get_editor_property("srgb"), path
        exported = output.parent / (part.lower() + "-basecolor.tga")
        assert not exported.exists()
        task = unreal.AssetExportTask()
        task.object = texture
        task.filename = str(exported)
        task.automated = True
        task.prompt = False
        task.exporter = unreal.TextureExporterTGA()
        assert unreal.Exporter.run_asset_export_task(task), path
        assert exported.is_file()
        exports[part] = str(exported)
    return {
        "material_textures": report,
        "texture_settings": texture_settings,
        "reference_exports": exports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("stage", choices=("conform", "textures"))
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    assert not args.output.exists()
    recipe = json.loads(args.recipe.read_text())
    if args.stage == "conform":
        report = conform(recipe)
    else:
        report = textures(recipe, args.output)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    main()
