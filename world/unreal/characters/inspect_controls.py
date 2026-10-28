"""Inspect a saved character's editable body constraints and face landmarks without saving it."""

import argparse
import json
from pathlib import Path

import unreal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recipe", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    assert not args.output.exists()
    recipe = json.loads(args.recipe.read_text())
    character = unreal.load_asset(recipe["asset_folder"] + "/" + recipe["asset_name"])
    assert character is not None
    subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
    assert subsystem.try_add_object_to_edit(character)
    try:
        constraints = {}
        for name, scaled in (("absolute", False), ("scaled_with_height", True)):
            constraints[name] = [
                {
                    "name": str(item.name),
                    "is_active": item.is_active,
                    "target_measurement": item.target_measurement,
                    "min_measurement": item.min_measurement,
                    "max_measurement": item.max_measurement,
                }
                for item in subsystem.get_body_constraints(character, scaled)
            ]
        landmarks = subsystem.get_face_landmarks(character)
        assert landmarks
        report = {
            "character": character.get_path_name(),
            "constraints": constraints,
            "face_landmarks": [[point.x, point.y, point.z] for point in landmarks],
        }
        with args.output.open("x") as stream:
            json.dump(report, stream, indent=2)
    finally:
        subsystem.remove_object_to_edit(character)


if __name__ == "__main__":
    main()
