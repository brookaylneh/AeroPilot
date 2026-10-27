"""Configure existing assembled people and bake the three scenario animations.

Updates only the character catalog and wrappers. The landscape and navigation
configuration stay in the existing Open Meadow catalog.
"""

import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
(root / "evidence/phase3").mkdir(parents=True, exist_ok=True)
people = json.loads((root / "unreal/creator-people.json").read_text())
scans = json.loads((root / "unreal/children-runtime.json").read_text())
config = json.loads((root / "unreal/character-runtime.json").read_text())
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
catalog = unreal.load_asset("/Game/OpenMeadow/Catalog")
assert catalog, "Open Meadow catalog is missing"
base = "/MetaHumanCharacter/Optional/Animation/"
sources = {
    "lost": base + "UEFNAnimPreset/Locomotion/AS_MH_Neutral_Stand_Idle_Loop",
    "waiting": base + "UEFNAnimPreset/Locomotion/AS_MH_Neutral_Stand_Idle_Loop",
    "injured": base + "UEFNAnimPreset/Locomotion/AS_MH_Neutral_Stand_Idle_Loop",
}
config["palette"]["cream"] = [0.7, 0.65, 0.52]
config["options"]["topColor"] = ["rust", "blue", "forest", "sand", "charcoal", "cream"]
config["options"]["topId"] = sorted({p["topId"] for p in people})
config["options"]["bottomId"] = sorted({p["bottomId"] for p in people})
config["options"].pop("poseId")
presets = []
report = []
for person in people:
    if person["kind"] == "metahuman":
        folder = "/Game/Scenario/MetaHumans/" + person["folder"]
        character_class = lib.load_blueprint_class(folder + "/BP_" + person["folder"])
        assert character_class, "Missing assembled character " + folder
        actor = actors.spawn_actor_from_class(character_class, unreal.Vector())
        meshes = [
            mesh
            for owner in [actor] + list(actor.get_all_child_actors(True))
            for mesh in owner.get_components_by_class(unreal.MeshComponent)
        ]
        body = next(mesh for mesh in meshes if mesh.get_name() == "Body")
        skeleton = body.get_skinned_asset().get_editor_property("skeleton")
        bindings = []
        for mesh in meshes:
            for slot in range(mesh.get_num_materials()):
                material = mesh.get_material(slot)
                name = material.get_name()
                if "Shirt" not in name and "Short" not in name:
                    continue
                key = "topColor" if "Shirt" in name else "bottomColor"
                parameters = set(
                    str(p)
                    for p in unreal.MaterialEditingLibrary.get_vector_parameter_names(material)
                )
                for parameter in ["diffuse_color_1", "diffuse_color_2", "B_diffuse_color_1"]:
                    assert parameter in parameters, "Missing garment tint parameter " + name
                    bindings.append(
                        unreal.ScenarioTintBinding(
                            appearance_key=key,
                            component_name=mesh.get_name(),
                            material_slot=slot,
                            parameter_name=parameter,
                        )
                    )
        assert {str(b.appearance_key) for b in bindings} == {"topColor", "bottomColor"}, (
            "Missing compatible wardrobe"
        )
        animations = {}
        for situation, source in sources.items():
            name = "AS_" + person["folder"] + "_" + situation
            path = "/Game/Scenario/Runtime/" + name
            animation = (
                unreal.load_asset(path)
                if lib.does_asset_exist(path)
                else tools.duplicate_asset(
                    name, "/Game/Scenario/Runtime", unreal.load_asset(source)
                )
            )
            assert animation, "Missing animation " + path
            if animation.get_editor_property("skeleton") != skeleton:
                assert unreal.ScenarioHuman.prepare_standing_animation(animation, skeleton), (
                    "Cannot bind animation " + path
                )
            if situation in ["lost", "injured"]:
                assert unreal.ScenarioHuman.prepare_situation_animation(
                    animation, unreal.load_asset(source), situation
                ), "Cannot author resting pose"
            lib.save_loaded_asset(animation)
            animations[situation] = animation
        body_name = "Body"
        actors.destroy_actor(actor)
    else:
        assert person["kind"] == "scan", "Unknown character pipeline"
        entry = next(entry for entry in scans if entry["id"] == person["id"])
        character_class = lib.load_blueprint_class(entry["blueprint"])
        assert character_class, "Missing imported child Blueprint"
        body_name = entry["bodyComponent"]
        bindings = [
            unreal.ScenarioTintBinding(
                appearance_key=key,
                component_name=body_name,
                material_slot=0,
                parameter_name=parameter,
            )
            for key, parameter in [("topColor", "UpperTint"), ("bottomColor", "LowerTint")]
        ]
        animations = {key: unreal.load_asset(path) for key, path in entry["animations"].items()}
        assert all(animations.values()), "Missing child animations"
    assert set(person["supportedSituations"]) == set(animations), "Animation capability mismatch"
    # Bake new activities into separate assets, retaining the source idle/rest
    # clips for repeatable authoring and the child's own bone proportions.
    activities = {"waiting": animations["waiting"]}
    if "injured" in animations:
        activities["injured"] = animations["injured"]
    can_walk = person["id"] != "child-05"  # Skateboard is fused into this scan.
    if can_walk:
        activities["walking"] = (
            unreal.load_asset(base + "UEFNAnimPreset/Locomotion/AS_MH_Neutral_Walk_Loop_F")
            if person["kind"] == "metahuman" else animations["waiting"]
        )
    for activity, source in activities.items():
        assert source, "Missing activity source"
        name = "AS_" + person["folder"] + "_activity_" + activity
        path = "/Game/Scenario/Runtime/" + name
        clip = unreal.load_asset(path) if lib.does_asset_exist(path) else tools.duplicate_asset(name, "/Game/Scenario/Runtime", source)
        assert clip, "Cannot create activity clip " + path
        if person["kind"] == "metahuman" and clip.get_editor_property("skeleton") != skeleton:
            assert unreal.ScenarioHuman.prepare_standing_animation(clip, skeleton)
        mode = "stock-walk" if activity == "walking" and person["kind"] == "metahuman" else activity
        if person["kind"] == "scan" and activity == "waiting":
            mode = "signaling"  # Arms and accessories meet the torso in these scans.
        assert unreal.ScenarioHuman.prepare_activity_animation(clip, source, mode), path
        lib.save_loaded_asset(clip)
        animations[activity] = clip
    assert "lost" in animations and "waiting" in animations, "Missing standing situations"
    path = "/Game/Scenario/Runtime/BP_Person_" + person["id"].replace("-", "_")
    if lib.does_asset_exist(path):
        bp = unreal.load_asset(path)
    else:
        factory = unreal.BlueprintFactory()
        factory.set_editor_property("parent_class", unreal.ScenarioHuman)
        bp = tools.create_asset(
            path.rsplit("/", 1)[1], "/Game/Scenario/Runtime", unreal.Blueprint, factory
        )
    cls = lib.load_blueprint_class(path)
    default = unreal.get_default_object(cls)
    default.set_editor_property("character_class", character_class)
    default.set_editor_property("body_component_name", body_name)
    default.get_editor_property("character").set_editor_property(
        "relative_rotation", unreal.Rotator(yaw=-90)
    )
    default.set_editor_property("tint_bindings", bindings)
    default.set_editor_property(
        "color_palette",
        {key: unreal.LinearColor(*value, 1) for key, value in config["palette"].items()},
    )
    default.set_editor_property("standing_pose", animations["waiting"])
    default.set_editor_property("situation_animations", animations)
    default.get_editor_property("backpack").set_static_mesh(
        unreal.load_asset("/Game/Scenario/Equipment/SM_Daypack")
    )
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    lib.save_loaded_asset(bp)
    presets.append(
        unreal.ScenarioPersonPreset(
            id=person["id"],
            display_name=person["name"],
            age_category=person["ageCategory"],
            description=person["description"],
            actor_class=lib.load_blueprint_class(path),
            height_cm=person["heightCm"],
            top_id=person["topId"],
            bottom_id=person["bottomId"],
            supports_backpack=person["supportsBackpack"],
            fixed_accessories=person["fixedAccessories"],
            supported_situations=person["supportedSituations"],
            can_walk=can_walk,
            can_wave=person["kind"] == "metahuman",
            capsule_radius_cm=person["capsuleRadiusCm"],
            capsule_half_height_cm=95 * person["heightCm"] / 170,
        )
    )
    report.append(
        {
            **person,
            "animations": {key: value.get_path_name() for key, value in animations.items()},
            "tintBindings": len(bindings),
        }
    )
catalog.set_editor_property("people", presets)
catalog.set_editor_property(
    "appearance_options",
    [
        unreal.ScenarioOptionGroup(key=key, allowed_values=values)
        for key, values in config["options"].items()
    ],
)
lib.save_loaded_asset(catalog)
(root / "evidence/phase3/creator-inventory.json").write_text(json.dumps(report, indent=2))
unreal.log("SNR_CREATOR_CONFIGURED")
