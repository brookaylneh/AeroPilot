"""Publish the verified runtime actors and reviewed appearance descriptions to Open Meadow."""

import argparse
import json
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
LIB = unreal.EditorAssetLibrary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    inventory = json.loads(args.inventory.read_text())
    people = json.loads((HERE.parent / "creator-people.json").read_text())
    assert [p["id"] for p in people] == [p["id"] for p in inventory]
    catalog = unreal.load_asset("/Game/OpenMeadow/Catalog")
    assert catalog is not None
    presets = []
    for item, person in zip(inventory, people, strict=True):
        actor_class = LIB.load_blueprint_class(item["folder"] + "/BP_Person")
        assert actor_class is not None
        assert all(unreal.load_asset(path) for path in item["animations"].values())
        presets.append(
            unreal.ScenarioPersonPreset(
                id=person["id"],
                display_name=person["name"],
                age_category=person["ageCategory"],
                description=person["description"],
                actor_class=actor_class,
                height_cm=item["height_cm"],
                top_id=person["topId"],
                bottom_id=person["bottomId"],
                supports_backpack=person["supportsBackpack"],
                fixed_accessories=person["fixedAccessories"],
                supported_situations=person["supportedSituations"],
                can_walk=True,
                can_wave=True,
                capsule_radius_cm=35 * item["height_cm"] / 170,
                capsule_half_height_cm=95 * item["height_cm"] / 170,
            )
        )
    options = {
        "topId": sorted({p["topId"] for p in people}),
        "topColor": ["original"],
        "bottomId": sorted({p["bottomId"] for p in people}),
        "bottomColor": ["original"],
        "backpackId": ["none"],
        "backpackColor": ["original"],
    }
    catalog.set_editor_property("people", presets)
    catalog.set_editor_property(
        "appearance_options",
        [
            unreal.ScenarioOptionGroup(key=key, allowed_values=values)
            for key, values in options.items()
        ],
    )
    assert LIB.save_loaded_asset(catalog)
    result = {
        "asset_version": str(catalog.get_editor_property("asset_version")),
        "people": [
            {**p, "heightCm": i["height_cm"]} for p, i in zip(people, inventory, strict=True)
        ],
        "options": options,
    }
    (args.output / "result.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
