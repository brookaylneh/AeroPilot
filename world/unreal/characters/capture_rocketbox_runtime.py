"""Capture fixed runtime animation poses and measure the selected characters' bone positions."""

import argparse
import json
import math
import runpy
import time
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def xyz(vector):
    return [vector.x, vector.y, vector.z]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    inventory = json.loads(args.inventory.read_text())
    review = runpy.run_path(str(HERE / "render_rocketbox.py"))
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for command in ["t.MaxFPS 15", "r.Streaming.FullyLoadUsedTextures 1", "r.ScreenPercentage 100"]:
        unreal.SystemLibrary.execute_console_command(world, command)
    profile = json.loads((HERE / "review-cameras.json").read_text())
    for name, expected in profile["console_variables"].items():
        assert unreal.SystemLibrary.get_console_variable_int_value(name) == expected, name
    post = ACTORS.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector())
    post.set_editor_property("unbound", True)
    settings = post.get_editor_property("settings")
    for name, value in [
        ("auto_exposure_min_brightness", review["EXPOSURE_EV100"]),
        ("auto_exposure_max_brightness", review["EXPOSURE_EV100"]),
        ("auto_exposure_bias", 0),
        ("motion_blur_amount", 0),
        ("bloom_intensity", 0),
        ("white_temp", 6500),
    ]:
        settings.set_editor_property("override_" + name, True)
        settings.set_editor_property(name, value)
    post.set_editor_property("settings", settings)
    shots = []
    for index, person in enumerate(inventory):
        x = index * 20000
        review["studio"](x)
        actor = ACTORS.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector())
        body = actor.skeletal_mesh_component
        body.set_skeletal_mesh_asset(unreal.load_asset(person["mesh"]))
        body.set_forced_lod(1)
        offset = person["offset_cm"]
        actor.set_actor_location(unreal.Vector(x + offset[0], offset[1], offset[2]), False, False)
        for activity, phase in [
            ("lost", 0.25),
            ("waiting", 0.4),
            ("walking", 0.25),
            ("injured", 0.25),
        ]:
            animation = unreal.load_asset(person["animations"][activity])
            shots.append(
                {
                    "person": person,
                    "body": body,
                    "x": x,
                    "activity": activity,
                    "animation": animation,
                    "time": animation.sequence_length * phase,
                }
            )
    camera = ACTORS.spawn_actor_from_class(unreal.CameraActor, unreal.Vector())
    component = camera.get_component_by_class(unreal.CameraComponent)
    component.set_aspect_ratio(1)
    component.set_field_of_view(35)
    report = {"renderer_cvars": profile["console_variables"], "poses": []}
    state = {"started": time.monotonic(), "index": 0, "task": None}
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    unreal.EditorPythonScripting.set_keep_python_script_alive(True)

    def tick(delta):
        elapsed = time.monotonic() - state["started"]
        if elapsed > 900:
            unreal.unregister_slate_post_tick_callback(handle)
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            raise TimeoutError("Runtime pose capture exceeded 900 seconds")
        if elapsed < 30:
            return
        if state["task"] is not None:
            if not state["task"].is_task_done():
                return
            assert Path(report["poses"][-1]["image"]).is_file()
            state["index"] += 1
            state["task"] = None
        if state["index"] == len(shots):
            report["seconds"] = elapsed
            (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
            unreal.unregister_slate_post_tick_callback(handle)
            unreal.EditorPythonScripting.set_keep_python_script_alive(False)
            return
        shot = shots[state["index"]]
        person, body = shot["person"], shot["body"]
        assert unreal.ScenarioHuman.preview_rocketbox_pose(body, shot["animation"], shot["time"])
        height = person["height_cm"]
        distance = height * 0.7 / math.tan(math.radians(35 / 2))
        target = unreal.Vector(shot["x"], 0, height / 2)
        position = unreal.Vector(shot["x"] + distance * 0.35, distance, height / 2)
        camera.set_actor_location_and_rotation(
            position, unreal.MathLibrary.find_look_at_rotation(position, target), False, False
        )
        image = args.output / (person["id"] + "-" + shot["activity"] + ".png")
        bones = {
            name: xyz(body.get_socket_location(name) - unreal.Vector(shot["x"], 0, 0))
            for name in [
                "Bip01",
                "Bip01-Head",
                "Bip01-L-Foot",
                "Bip01-R-Foot",
                "Bip01-L-Hand",
                "Bip01-R-Hand",
                "Bip01-L-Calf",
                "Bip01-R-Calf",
            ]
        }
        report["poses"].append(
            {
                "id": person["id"],
                "activity": shot["activity"],
                "time_seconds": shot["time"],
                "bones_cm": bones,
                "image": str(image),
            }
        )
        (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
        state["task"] = unreal.AutomationLibrary.take_high_res_screenshot(
            1600, 1600, str(image), camera, delay=3
        )
        assert state["task"].is_valid_task()

    handle = unreal.register_slate_post_tick_callback(tick)


if __name__ == "__main__":
    main()
