"""Author a MetaHuman from an Epic preset and explicit body and appearance parameters."""

import argparse
import json
import math
from pathlib import Path

import unreal


def sculpt_face(character, settings, subsystem):
    before = list(subsystem.get_face_landmarks(character))
    assert len(before) == settings["landmark_count"]
    boundary = settings["boundary_indices"]
    assert len(boundary) == 2 and all(0 <= index < len(before) for index in boundary)
    plane = sum(before[index].z for index in boundary) / len(boundary)
    front = before[settings["neck_front_index"]].y + settings["front_margin_cm"]
    scale = settings["vertical_scale"]
    assert 0.75 <= scale <= 1
    indices = [i for i, point in enumerate(before) if point.z < plane and point.y > front]
    assert 20 <= len(indices) <= 70
    depth = max(plane - before[i].z for i in indices)
    recession = settings["recession_cm"]
    assert 0 <= recession <= 1
    deltas = [
        unreal.Vector(
            0, -recession * (plane - before[i].z) / depth, (1 - scale) * (plane - before[i].z)
        )
        for i in indices
    ]
    assert all(delta.z <= 3 for delta in deltas)
    subsystem.translate_face_landmarks(character, indices, deltas)
    subsystem.commit_face_state(character)
    after = list(subsystem.get_face_landmarks(character))
    assert len(after) == len(before)
    assert sum(after[i].z - before[i].z for i in indices) > 0
    return {
        "settings": settings,
        "indices": indices,
        "before_cm": [[p.x, p.y, p.z] for p in before],
        "requested_deltas_cm": [[p.x, p.y, p.z] for p in deltas],
        "after_cm": [[p.x, p.y, p.z] for p in after],
    }


def create(recipe, subsystem):
    from metahuman_toolset.metahuman import BodyShape, EyeColor, MetaHumanToolset, SkinTone

    folder = recipe["asset_folder"]
    assert not unreal.EditorAssetLibrary.does_directory_exist(folder), folder
    preset = unreal.load_asset(recipe["preset"])
    assert isinstance(preset, unreal.MetaHumanCharacter), recipe["preset"]
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    character = tools.duplicate_asset(recipe["asset_name"], folder, preset)
    assert character is not None
    if "face" in recipe:
        settings = character.get_editor_property("face_evaluation_settings")
        limits = {"head_scale": (0.8, 1.3), "high_frequency_delta": (0, 1)}
        assert recipe["face"].keys() == limits.keys()
        for name, value in recipe["face"].items():
            low, high = limits[name]
            assert math.isfinite(value) and low <= value <= high
            settings.set_editor_property(name, value)
    session = MetaHumanToolset.begin_edit(character.get_path_name())
    try:
        shape = BodyShape()
        ranges = {str(c.name): c for c in subsystem.get_body_constraints(character, False)}
        height = recipe["body"]["height_cm"]
        assert ranges["Height"].min_measurement <= height <= ranges["Height"].max_measurement, (
            height,
            ranges["Height"].min_measurement,
            ranges["Height"].max_measurement,
        )
        for name in ("height_cm", "masculine_feminine", "fat", "muscularity"):
            value = recipe["body"][name]
            assert math.isfinite(value)
            if name != "height_cm":
                assert 0 <= value <= 1, (name, value)
            setattr(shape, name, value)
        for constraint in ranges.values():
            constraint.is_active = False
        subsystem.set_body_constraints(character, list(ranges.values()))
        MetaHumanToolset.set_body_shape(session, shape)
        active = {
            str(constraint.name)
            for constraint in subsystem.get_body_constraints(character, False)
            if constraint.is_active
        }
        assert active == {"Height", "Masculine/Feminine", "Fat", "Muscularity"}, active
        actual = MetaHumanToolset.get_body_shape(session)
        body = {name: getattr(actual, name) for name in recipe["body"]}
        assert all(math.isclose(body[n], v, abs_tol=1e-5) for n, v in recipe["body"].items())
        skin = SkinTone()
        eyes = EyeColor()
        for properties, values in ((skin, recipe["skin"]), (eyes, recipe["eyes"])):
            for name, value in values.items():
                assert math.isfinite(value) and 0 <= value <= 1, (name, value)
                setattr(properties, name, value)
        MetaHumanToolset.set_skin_tone(session, skin)
        MetaHumanToolset.set_eye_color(session, eyes)
        actual_skin = MetaHumanToolset.get_skin_tone(session)
        actual_eyes = MetaHumanToolset.get_eye_color(session)
        for actual, values in ((actual_skin, recipe["skin"]), (actual_eyes, recipe["eyes"])):
            assert all(math.isclose(getattr(actual, n), v, abs_tol=1e-5) for n, v in values.items())
        sculpt = None
        if "face_sculpt" in recipe:
            sculpt = sculpt_face(character, recipe["face_sculpt"], subsystem)
        assert unreal.EditorAssetLibrary.save_loaded_asset(character, only_if_is_dirty=False)
        return {
            "face_sculpt": sculpt,
            "character": character.get_path_name(),
            "preset": recipe["preset"],
            "body_control_readback": body,
            "active_body_constraints": sorted(active),
            "skin": recipe["skin"],
            "eyes": recipe["eyes"],
            "source": "Epic preset and MetaHuman Generator tools",
            "body_values_are_control_targets": True,
        }
    finally:
        MetaHumanToolset.end_edit(session)


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
    loaded = [unreal.load_asset(path) for path in assets]
    assert all(asset is not None for asset in loaded)
    # Async texture compilation exposes 32-pixel placeholders until this barrier.
    unreal.AutomationLibrary.finish_loading_before_screenshot()
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
                    "textures": [
                        str(name) for name in library.get_texture_parameter_names(material)
                    ],
                }
            )
        if kind == "Texture2D":
            texture = unreal.load_asset(path)
            textures.append(
                {
                    "asset": path,
                    "width": texture.blueprint_get_size_x(),
                    "height": texture.blueprint_get_size_y(),
                    "srgb": texture.get_editor_property("srgb"),
                }
            )
    assert blueprints and textures, "Incomplete MetaHuman assembly"
    blueprint = unreal.load_asset(
        target + "/" + recipe["asset_name"] + "/BP_" + recipe["asset_name"]
    )
    assert blueprint is not None
    export_quality = unreal.EditorAssetLibrary.get_metadata_tag(blueprint, "MHExportQuality")
    assert export_quality == "Cinematic", export_quality
    return {
        "export_quality": export_quality,
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
        "stage", choices=("create", "cloud", "wardrobe", "assemble", "materials", "inventory")
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
            "create": create,
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
