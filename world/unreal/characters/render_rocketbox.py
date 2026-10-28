"""Capture Rocketbox characters under fixed lighting at portrait and sensor distances."""

import argparse
import json
import math
import time
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def vector(values):
    return unreal.Vector(*values)


def studio(offset):
    floor = ACTORS.spawn_actor_from_class(unreal.StaticMeshActor, vector((offset, 0, -3)))
    floor.static_mesh_component.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Plane"))
    floor.static_mesh_component.set_material(
        0, unreal.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
    )
    floor.set_actor_scale3d(vector((100, 100, 100)))
    for position, intensity in [
        ((-200, 250, 240), 5000),
        ((180, 160, 180), 2200),
        ((60, -170, 240), 4000),
    ]:
        location = vector((position[0] + offset, position[1], position[2]))
        rotation = unreal.MathLibrary.find_look_at_rotation(location, vector((offset, 0, 110)))
        light = ACTORS.spawn_actor_from_class(unreal.RectLight, location, rotation)
        component = light.get_component_by_class(unreal.RectLightComponent)
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        component.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
        component.set_intensity(intensity)
        component.set_editor_property("source_width", 180)
        component.set_editor_property("source_height", 180)
        component.set_editor_property("attenuation_radius", 1500)


def subject(item, offset):
    mesh = unreal.load_asset(item["mesh"])
    assert isinstance(mesh, unreal.SkeletalMesh)
    assert mesh.get_editor_property("skeleton") is not None
    assigned = mesh.get_editor_property("materials")
    assert all(slot.material_interface is not None for slot in assigned)
    assert [slot.material_interface.get_path_name() for slot in assigned] == [
        record["material"] for record in item["materials"]
    ]
    actor = ACTORS.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector())
    component = actor.skeletal_mesh_component
    component.set_skeletal_mesh_asset(mesh)
    component.set_forced_lod(1)
    center, extent, _ = unreal.SystemLibrary.get_component_bounds(component)
    assert 90 < extent.z * 2 < 230, (item["id"], center, extent)
    actor.set_actor_location(
        vector((offset - center.x, -center.y, extent.z - center.z)), False, False
    )
    bones = [str(component.get_bone_name(i)) for i in range(component.get_num_bones())]
    head_names = [name for name in bones if name == "Bip01-Head"]
    assert len(head_names) == 1, bones
    head = component.get_socket_location(head_names[0])
    height = extent.z * 2
    assert height * 0.7 < head.z < height
    return {
        "id": item["id"],
        "name": item["name"],
        "mesh": item["mesh"],
        "height_cm": height,
        "bone_count": len(bones),
        "head_cm": [head.x, head.y, head.z],
        "offset_cm": offset,
        "bounds_extent_cm": [extent.x, extent.y, extent.z],
    }


def views(subjects):
    result = []
    for person in subjects:
        x, height = person["offset_cm"], person["height_cm"]
        head = person["head_cm"]
        full_distance = height * 0.6 / math.tan(math.radians(35 / 2))
        for name, position, target, fov, width, height_px in [
            ("face", (x, 85, head[2] + 5), (x, 0, head[2] + 5), 35, 4096, 4096),
            ("front", (x, full_distance, height / 2), (x, 0, height / 2), 35, 4096, 4096),
            (
                "three-quarter",
                (x + full_distance * 0.7, full_distance * 0.7, height / 2),
                (x, 0, height / 2),
                35,
                4096,
                4096,
            ),
            ("drone-nadir", (x, 0, 3000), (x, 0, 0), 70, 3840, 2160),
            ("drone-oblique", (x, 1500, 3000), (x, 0, 0), 70, 3840, 2160),
        ]:
            result.append(
                {
                    "name": person["id"] + "-" + name,
                    "position_cm": position,
                    "target_cm": target,
                    "horizontal_fov_degrees": fov,
                    "size_px": [width, height_px],
                }
            )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    items = json.loads(args.inventory.read_text())
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for command in [
        "t.MaxFPS 15",
        "r.ScreenPercentage 100",
        "r.MotionBlurQuality 0",
        "r.BloomQuality 0",
        "r.Streaming.PoolSize 8192",
        "r.Streaming.FullyLoadUsedTextures 1",
    ]:
        unreal.SystemLibrary.execute_console_command(world, command)
    profile = json.loads((HERE / "review-cameras.json").read_text())
    for name, expected in profile["console_variables"].items():
        assert unreal.SystemLibrary.get_console_variable_int_value(name) == expected, name
    subjects = []
    for index, item in enumerate(items):
        offset = index * 20000
        subjects.append(subject(item, offset))
        studio(offset)
    post = ACTORS.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector())
    post.set_editor_property("unbound", True)
    settings = post.get_editor_property("settings")
    for name, value in [
        ("auto_exposure_min_brightness", 5),
        ("auto_exposure_max_brightness", 5),
        ("auto_exposure_bias", 0),
        ("motion_blur_amount", 0),
        ("bloom_intensity", 0),
        ("white_temp", 6500),
    ]:
        settings.set_editor_property("override_" + name, True)
        settings.set_editor_property(name, value)
    post.set_editor_property("settings", settings)
    camera = ACTORS.spawn_actor_from_class(unreal.CameraActor, unreal.Vector())
    component = camera.get_component_by_class(unreal.CameraComponent)
    shots = views(subjects)
    report = {
        "subjects": subjects,
        "views": shots,
        "renderer_cvars": profile["console_variables"],
        "exposure_ev100": 5,
        "white_balance_kelvin": 6500,
        "images": [],
    }
    (args.output / "scene.json").write_text(json.dumps(report, indent=2) + "\n")
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)
    state = {"started": time.monotonic(), "index": 0, "task": None}

    def tick(delta):
        elapsed = time.monotonic() - state["started"]
        if elapsed > 900:
            unreal.unregister_slate_post_tick_callback(handle)
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            raise TimeoutError("Rocketbox capture exceeded 900 seconds")
        if elapsed < 60:
            return
        if state["task"] is not None:
            if not state["task"].is_task_done():
                return
            path = args.output / (shots[state["index"]]["name"] + ".png")
            assert path.is_file(), path
            report["images"].append(str(path))
            state["index"] += 1
            state["task"] = None
        if state["index"] == len(shots):
            report["elapsed_seconds"] = elapsed
            (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            unreal.unregister_slate_post_tick_callback(handle)
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            return
        shot = shots[state["index"]]
        position = vector(shot["position_cm"])
        rotation = unreal.MathLibrary.find_look_at_rotation(position, vector(shot["target_cm"]))
        camera.set_actor_location_and_rotation(position, rotation, False, False)
        component.set_field_of_view(shot["horizontal_fov_degrees"])
        width, height = shot["size_px"]
        component.set_aspect_ratio(width / height)
        state["task"] = unreal.AutomationLibrary.take_high_res_screenshot(
            width, height, str(args.output / (shot["name"] + ".png")), camera, delay=8.0
        )
        assert state["task"].is_valid_task()

    handle = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    main()
