"""Fit a SAM body to an Epic preset in the Unreal editor, then prepare its rig."""

import argparse
import hashlib
import json
import math
from pathlib import Path

import unreal


def conform(recipe, subsystem):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    source = Path(recipe["body_glb"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == recipe["body_sha256"]
    folder = recipe["asset_folder"]
    assert not unreal.EditorAssetLibrary.does_directory_exist(folder), folder
    task = unreal.AssetImportTask()
    task.filename = str(source)
    task.destination_path = folder
    task.destination_name = "SAM_Body"
    task.automated = True
    task.save = True
    task.replace_existing = False
    tools.import_asset_tasks([task])
    meshes = [asset for asset in task.get_objects() if isinstance(asset, unreal.StaticMesh)]
    assert len(meshes) == 1, task.imported_object_paths
    target = meshes[0]
    preset = unreal.load_asset(recipe["preset"])
    assert preset is not None, recipe["preset"]
    character = tools.duplicate_asset(recipe["asset_name"], folder, preset)
    assert character is not None
    assert subsystem.try_add_object_to_edit(character)
    try:
        vertices, indices = subsystem.get_mesh_data_for_conforming(target)
        assert len(vertices) == 18439 and len(indices) == 110622
        params = unreal.ConformTargetParams()
        params.conform_target_mesh.target_parts_type = unreal.TargetPartsType.BODY_ONLY
        params.conform_target_mesh.body_vertices = vertices
        params.conform_target_mesh.body_vertex_indices = indices
        params.estimate_body_joints_from_mesh = True
        params.auto_solve = True
        params.body_conform_solve_settings.pipeline_name = "body_only"
        key = unreal.MetaHumanCharacterTargetMeshKey()
        key.body_mesh = target
        assert subsystem.conform_to_target_meshes(character, key, params), "Body fitting failed"
        subsystem.commit_posed_state_as_a_pose(character, key)
        assert unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
        return {
            "character": character.get_path_name(),
            "preset": recipe["preset"],
            "body_mesh": target.get_path_name(),
            "input_vertices": len(vertices),
            "input_triangles": len(indices) // 3,
            "conformed_body_only": True,
            "skin_settings": str(character.get_editor_property("skin_settings")),
        }
    finally:
        subsystem.remove_object_to_edit(character)


def cloud(recipe, subsystem):
    character = unreal.load_asset(recipe["asset_folder"] + "/" + recipe["asset_name"])
    assert character is not None
    assert subsystem.try_add_object_to_edit(character)
    try:
        settings = character.get_editor_property("skin_settings")
        resolutions = settings.desired_texture_sources_resolutions
        for name in (
            "face_albedo",
            "face_normal",
            "face_cavity",
            "face_animated_maps",
            "body_albedo",
            "body_normal",
            "body_cavity",
            "body_masks",
        ):
            resolutions.set_editor_property(name, unreal.RequestTextureResolution.RES8K)
        settings.desired_texture_sources_resolutions = resolutions
        subsystem.commit_skin_settings(character, settings)
        request = unreal.MetaHumanCharacterAutoRiggingRequestParams()
        request.rig_type = unreal.MetaHumanRigType.JOINTS_AND_BLEND_SHAPES
        request.blocking = True
        request.report_progress = False
        subsystem.request_auto_rigging(character, request)
        assert character.get_editor_property("has_face_dna_blendshapes"), "Epic rig request failed"
        request = unreal.MetaHumanCharacterTextureRequestParams()
        request.blocking = True
        request.report_progress = False
        subsystem.request_texture_sources(character, request)
        assert character.get_editor_property("has_high_resolution_textures"), "Epic textures failed"
        assert unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
        return {"rigged": True, "high_resolution_textures": True}
    finally:
        subsystem.remove_object_to_edit(character)


def wardrobe(recipe, subsystem):
    character = unreal.load_asset(recipe["asset_folder"] + "/" + recipe["asset_name"])
    assert character is not None and subsystem.try_add_object_to_edit(character)
    try:
        collection = subsystem.get_preview_collection(character)
        for slot, asset in recipe["wardrobe"].items():
            item = unreal.load_asset(asset)
            assert item is not None, asset
            key = collection.try_add_item_from_wardrobe_item(slot, item)
            collection.default_instance.set_single_slot_selection(slot, key)
            selections = collection.default_instance.get_slot_selection_data()
            assert sum(str(data.selection.slot_name) == slot for data in selections) == 1
        subsystem.on_edit_preview_collection(character)
        assert unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
        return {"wardrobe": recipe["wardrobe"]}
    finally:
        subsystem.remove_object_to_edit(character)


def assemble(recipe, subsystem):
    character = unreal.load_asset(recipe["asset_folder"] + "/" + recipe["asset_name"])
    assert character is not None and subsystem.try_add_object_to_edit(character)
    try:
        assert subsystem.can_build_meta_human(character, True)
        target = recipe["assembly_folder"]
        assert not unreal.EditorAssetLibrary.does_directory_exist(target)
        params = unreal.MetaHumanCharacterEditorBuildParameters()
        params.pipeline_type = unreal.MetaHumanDefaultPipelineType.CINEMATIC
        params.pipeline_quality = unreal.MetaHumanQualityLevel.CINEMATIC
        params.absolute_build_path = target
        params.common_folder_path = target + "/Common"
        params.name_override = recipe["asset_name"]
        params.enable_wardrobe_item_validation = True
        subsystem.build_meta_human(character, params)
        assets = list(unreal.EditorAssetLibrary.list_assets(target, recursive=True))
        assert assets, "MetaHuman assembly produced no assets"
        assert unreal.EditorAssetLibrary.save_loaded_assets(
            [unreal.load_asset(path) for path in assets], only_if_is_dirty=False
        )
        report = inventory(recipe)
        report["pipeline"] = "Cinematic"
        report["wardrobe_validation"] = True
        return report
    finally:
        subsystem.remove_object_to_edit(character)


def inventory(recipe):
    target = recipe["assembly_folder"]
    assets = list(unreal.EditorAssetLibrary.list_assets(target, recursive=True))
    assert assets, "MetaHuman assembly produced no assets"
    blueprints = []
    textures = []
    material_parameters = []
    for path in assets:
        data = unreal.EditorAssetLibrary.find_asset_data(path)
        kind = str(data.asset_class_path.asset_name)
        if kind == "Blueprint":
            blueprints.append(path)
        if kind == "MaterialInstanceConstant":
            material = unreal.load_asset(path)
            library = unreal.MaterialEditingLibrary
            material_parameters.append(
                {
                    "asset": path,
                    "scalars": [str(name) for name in library.get_scalar_parameter_names(material)],
                    "vectors": [str(name) for name in library.get_vector_parameter_names(material)],
                }
            )
        if kind == "Texture2D":
            texture = unreal.load_asset(path)
            textures.append(
                {
                    "asset": path,
                    "width": texture.blueprint_get_size_x(),
                    "height": texture.blueprint_get_size_y(),
                }
            )
    assert blueprints and textures, "Incomplete MetaHuman assembly"
    return {
        "assembly_folder": target,
        "assets": assets,
        "blueprints": blueprints,
        "textures": textures,
        "materials": material_parameters,
    }


def materials(recipe):
    report = {}
    library = unreal.MaterialEditingLibrary
    for path, values in recipe["material_parameters"].items():
        assert path.startswith(recipe["assembly_folder"] + "/"), path
        material = unreal.load_asset(path)
        assert isinstance(material, unreal.MaterialInstanceConstant), path
        report[path] = {}
        scalar_names = {str(name) for name in library.get_scalar_parameter_names(material)}
        vector_names = {str(name) for name in library.get_vector_parameter_names(material)}
        # UE 5.8.2 setters return false even after writing; verify the values.
        for name, value in values.items():
            if isinstance(value, list):
                assert name in vector_names and len(value) == 4, name
                library.set_material_instance_vector_parameter_value(
                    material, name, unreal.LinearColor(*value)
                )
                actual = library.get_material_instance_vector_parameter_value(material, name)
                report[path][name] = [actual.r, actual.g, actual.b, actual.a]
                assert all(
                    math.isclose(a, b, abs_tol=1e-6) for a, b in zip(report[path][name], value)
                )
            else:
                assert name in scalar_names, name
                library.set_material_instance_scalar_parameter_value(material, name, value)
                report[path][name] = library.get_material_instance_scalar_parameter_value(
                    material, name
                )
                assert math.isclose(report[path][name], value, abs_tol=1e-6)
        library.update_material_instance(material)
        assert unreal.EditorAssetLibrary.save_loaded_asset(material, only_if_is_dirty=False)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument(
        "stage", choices=("conform", "cloud", "wardrobe", "assemble", "materials", "inventory")
    )
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    assert not args.output.exists()
    recipe = json.loads(args.recipe.read_text())
    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    if args.stage in ("materials", "inventory"):
        report = {"materials": materials, "inventory": inventory}[args.stage](recipe)
    else:
        operation = {
            "conform": conform,
            "cloud": cloud,
            "wardrobe": wardrobe,
            "assemble": assemble,
        }[args.stage]
        report = operation(recipe, subsystem)
    encoded = json.dumps(report, indent=2) + "\n"
    with args.output.open("x") as stream:
        stream.write(encoded)
    unreal.log("SNR_METAHUMAN_DONE " + str(args.output))


if __name__ == "__main__":
    main()
