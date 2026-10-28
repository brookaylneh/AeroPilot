"""Render a saved MetaHuman Blueprint from fixed cameras in the Unreal editor."""

import argparse
import json
import time
from pathlib import Path

import unreal


def setup(recipe):
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    blueprint = unreal.EditorAssetLibrary.load_blueprint_class(recipe["blueprint"])
    assert blueprint is not None, recipe["blueprint"]
    actor = actors.spawn_actor_from_class(blueprint, unreal.Vector())
    assert actor is not None
    lod_components = actor.get_components_by_class(unreal.LODSyncComponent)
    assert len(lod_components) == 1
    lod_components[0].set_editor_property("forced_lod", 0)
    body = next(
        component
        for component in actor.get_components_by_class(unreal.SkeletalMeshComponent)
        if component.get_name() == "Body"
    )
    center, extent, radius = unreal.SystemLibrary.get_component_bounds(body)
    actor.set_actor_location(unreal.Vector(-center.x, -center.y, 0), False, False)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    commands = (
        "t.MaxFPS 30",
        "r.ScreenPercentage 100",
        "r.MotionBlurQuality 0",
        "r.BloomQuality 0",
        "r.SSS.Quality 1",
        "r.SSS.HalfRes 0",
        "r.Streaming.PoolSize 8192",
        "r.Streaming.FullyLoadUsedTextures 1",
    )
    for command in commands:
        unreal.SystemLibrary.execute_console_command(world, command)
    for shape, position, scale in (
        ("Cube", (0, -700, 150), (12, 0.1, 6)),
        ("Plane", (0, 0, -2), (15, 15, 15)),
    ):
        mesh = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(*position))
        mesh.static_mesh_component.set_static_mesh(
            unreal.load_asset("/Engine/BasicShapes/" + shape)
        )
        mesh.static_mesh_component.set_material(
            0, unreal.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
        )
        mesh.set_actor_scale3d(unreal.Vector(*scale))
    for position, intensity in (
        ((-200, 250, 240), 5000),
        ((180, 160, 180), 2200),
        ((60, -170, 240), 4000),
    ):
        location = unreal.Vector(*position)
        rotation = unreal.MathLibrary.find_look_at_rotation(location, unreal.Vector(0, 0, 110))
        light = actors.spawn_actor_from_class(unreal.RectLight, location, rotation)
        component = light.get_component_by_class(unreal.RectLightComponent)
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        component.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
        component.set_intensity(intensity)
        component.set_editor_property("source_width", 180)
        component.set_editor_property("source_height", 180)
        component.set_editor_property("attenuation_radius", 1500)
    post = actors.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector())
    post.set_editor_property("unbound", True)
    settings = post.get_editor_property("settings")
    for name, value in (
        ("auto_exposure_min_brightness", 5),
        ("auto_exposure_max_brightness", 5),
        ("auto_exposure_bias", 0),
        ("motion_blur_amount", 0),
        ("bloom_intensity", 0),
    ):
        settings.set_editor_property("override_" + name, True)
        settings.set_editor_property(name, value)
    post.set_editor_property("settings", settings)
    camera = actors.spawn_actor_from_class(unreal.CameraActor, unreal.Vector())
    component = camera.get_component_by_class(unreal.CameraComponent)
    component.set_field_of_view(recipe["field_of_view_degrees"])
    component.set_aspect_ratio(1)
    components = []
    for mesh in actor.get_components_by_class(unreal.SkeletalMeshComponent):
        components.append(
            {
                "name": mesh.get_name(),
                "materials": [material.get_path_name() for material in mesh.get_materials()],
            }
        )
    outfits = [item for item in components if item["name"].startswith("SkeletalMesh")]
    assert len(outfits) == 1, "Expected exactly one outfit component"
    grooms = []
    for component in actor.get_components_by_class(unreal.GroomComponent):
        asset = component.get_editor_property("groom_asset")
        if asset is not None:
            binding = component.get_editor_property("binding_asset")
            assert binding is not None, component.get_name()
            grooms.append(
                {
                    "name": component.get_name(),
                    "asset": asset.get_path_name(),
                    "binding": binding.get_path_name(),
                    "visible": component.is_visible(),
                    "use_cards": component.get_editor_property("use_cards"),
                }
            )
    assert len(grooms) >= 3 and all(item["visible"] for item in grooms), grooms
    assert not any(item["use_cards"] for item in grooms), grooms
    geometry = {
        "body_bounds_center_cm": [center.x, center.y, center.z],
        "body_bounds_extent_cm": [extent.x, extent.y, extent.z],
        "grooms": grooms,
    }
    return camera, components, geometry


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("cameras", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    character = json.loads(args.recipe.read_text())
    recipe = json.loads(args.cameras.read_text())
    name = character["asset_name"]
    recipe["blueprint"] = character["assembly_folder"] + "/" + name + "/BP_" + name
    args.output.mkdir(exist_ok=False)
    camera, components, geometry = setup(recipe)
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    state = {"started": time.monotonic(), "index": 0, "task": None}
    report = {"recipe": recipe, "images": [], "components": components, "geometry": geometry}
    for name, expected in recipe["console_variables"].items():
        actual = unreal.SystemLibrary.get_console_variable_int_value(name)
        assert actual == expected, (name, actual, expected)
    report["console_variables_verified"] = True

    def tick(delta):
        elapsed = time.monotonic() - state["started"]
        if elapsed > 600:
            unreal.unregister_slate_post_tick_callback(handle)
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            raise TimeoutError("MetaHuman capture exceeded 600 seconds")
        if elapsed < 60:
            return
        if state["task"] is not None:
            if not state["task"].is_task_done():
                return
            path = args.output / (recipe["views"][state["index"]]["name"] + ".png")
            assert path.is_file(), path
            report["images"].append(str(path))
            state["index"] += 1
            state["task"] = None
        if state["index"] == len(recipe["views"]):
            report["elapsed_seconds"] = elapsed
            with (args.output / "result.json").open("x") as stream:
                json.dump(report, stream, indent=2)
            unreal.unregister_slate_post_tick_callback(handle)
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            return
        view = recipe["views"][state["index"]]
        position = unreal.Vector(*view["position_cm"])
        rotation = unreal.MathLibrary.find_look_at_rotation(
            position, unreal.Vector(*view["target_cm"])
        )
        camera.set_actor_location_and_rotation(position, rotation, False, False)
        size = recipe["image_size_px"]
        state["task"] = unreal.AutomationLibrary.take_high_res_screenshot(
            size, size, str(args.output / (view["name"] + ".png")), camera, delay=3.0
        )
        assert state["task"].is_valid_task()

    handle = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    main()
