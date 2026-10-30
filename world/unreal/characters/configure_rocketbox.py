"""Build runtime Rocketbox poses and actor Blueprints from the imported meshes."""

import argparse
import json
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LIB = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()


def blueprint(folder, name, parent):
    path = folder + "/" + name
    assert not LIB.does_asset_exist(path), path
    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", parent)
    asset = TOOLS.create_asset(name, folder, unreal.Blueprint, factory)
    assert asset is not None
    return asset, unreal.get_default_object(LIB.load_blueprint_class(path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    inventory = json.loads(args.inventory.read_text())
    people = json.loads((HERE.parent / "creator-people.json").read_text())
    assert [p["id"] for p in people] == [p["id"] for p in inventory]
    report = []
    for item, person in zip(inventory, people, strict=True):
        folder = item["folder"]
        animations = {}
        for activity in ["lost", "waiting", "walking", "injured"]:
            source = unreal.load_asset(
                item["source_animations"]["lost" if activity == "injured" else activity]
            )
            name = "AS_" + activity
            assert not LIB.does_asset_exist(folder + "/" + name)
            clip = TOOLS.duplicate_asset(name, folder, source)
            assert clip is not None
            assert unreal.ScenarioHuman.prepare_rocketbox_animation(clip, source, activity), (
                item["id"],
                activity,
            )
            assert LIB.save_loaded_asset(clip)
            animations[activity] = clip
        character, default = blueprint(folder, "BP_Character", unreal.SkeletalMeshActor)
        body = default.get_editor_property("skeletal_mesh_component")
        body.set_skeletal_mesh_asset(unreal.load_asset(item["mesh"]))
        body.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        body.set_editor_property("relative_location", unreal.Vector(*item["offset_cm"]))
        unreal.BlueprintEditorLibrary.compile_blueprint(character)
        assert LIB.save_loaded_asset(character)
        wrapper, default = blueprint(folder, "BP_Person", unreal.ScenarioHuman)
        default.set_editor_property(
            "character_class", LIB.load_blueprint_class(folder + "/BP_Character")
        )
        default.set_editor_property("body_component_name", body.get_name())
        default.get_editor_property("character").set_editor_property(
            "relative_rotation", unreal.Rotator(yaw=-90)
        )
        default.set_editor_property("tint_bindings", [])
        default.set_editor_property("color_palette", {})
        default.set_editor_property("standing_pose", animations["lost"])
        default.set_editor_property("situation_animations", animations)
        unreal.BlueprintEditorLibrary.compile_blueprint(wrapper)
        assert LIB.save_loaded_asset(wrapper)
        report.append(
            {
                **item,
                "actor_class": folder + "/BP_Person.BP_Person_C",
                "animations": {key: value.get_path_name() for key, value in animations.items()},
                "duration_seconds": {
                    key: value.sequence_length for key, value in animations.items()
                },
            }
        )
        (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
