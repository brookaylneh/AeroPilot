"""Duplicate the reviewed meshes and import pinned Rocketbox motions for runtime authoring."""

import argparse
import hashlib
import json
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
LIB = unreal.EditorAssetLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
ACTORS = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def duplicate(source, folder, name):
    assert source is not None
    assert not LIB.does_asset_exist(folder + "/" + name)
    asset = TOOLS.duplicate_asset(name, folder, source)
    assert asset is not None
    return asset


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    selection = json.loads((HERE / "rocketbox-selection.json").read_text())
    motion = json.loads((HERE / "rocketbox-motion.lock.json").read_text())
    vendor = ROOT / motion["vendor"]
    for item in motion["files"]:
        data = (vendor / item["path"]).read_bytes()
        assert len(data) == item["bytes"]
        blob = b"blob " + str(len(data)).encode() + b"\0" + data
        assert hashlib.sha1(blob).hexdigest() == item["git_blob_sha1"]
    chosen = {}
    for source in selection["sources"]:
        manifest = json.loads((ROOT / source["manifest"]).read_text())
        for person in manifest["characters"]:
            if person["id"] in source["ids"]:
                chosen[person["id"]] = person
    assert set(chosen) == set(selection["order"])
    report = []
    for identifier in selection["order"]:
        recipe = chosen[identifier]
        name = recipe["name"]
        folder = "/Game/Scenario/Rocketbox/" + name
        assert not LIB.does_directory_exist(folder), folder
        source_path = recipe["asset_folder"] + "/" + name + "_facial"
        source_mesh = unreal.load_asset(source_path)
        assert isinstance(source_mesh, unreal.SkeletalMesh), source_path
        mesh = duplicate(source_mesh, folder, "SK_" + name)
        skeleton = duplicate(source_mesh.get_editor_property("skeleton"), folder, "SKEL_" + name)
        assert unreal.ScenarioHuman.bind_rocketbox_skeleton(mesh, skeleton)
        assert mesh.get_editor_property("skeleton") == skeleton
        actor = ACTORS.spawn_actor_from_class(unreal.SkeletalMeshActor, unreal.Vector())
        body = actor.skeletal_mesh_component
        body.set_skeletal_mesh_asset(mesh)
        center, extent, _ = unreal.SystemLibrary.get_component_bounds(body)
        assert 90 < extent.z * 2 < 230
        bones = [str(body.get_bone_name(i)) for i in range(body.get_num_bones())]
        bones_report = {
            bone: list(
                body.get_socket_transform(
                    bone, unreal.RelativeTransformSpace.RTS_COMPONENT
                ).translation.to_tuple()
            )
            for bone in bones
        }
        ACTORS.destroy_actor(actor)
        gender = "f" if "Female" in name else "m"
        assert "Female" in name or "Male" in name, name
        animations = {}
        for activity, filename in motion["clips"][gender].items():
            options = unreal.FbxImportUI()
            for key, value in {
                "automated_import_should_detect_type": False,
                "mesh_type_to_import": unreal.FBXImportType.FBXIT_ANIMATION,
                "import_mesh": False,
                "import_animations": True,
                "import_materials": False,
                "import_textures": False,
                "skeleton": skeleton,
            }.items():
                options.set_editor_property(key, value)
            options.anim_sequence_import_data.set_editor_property(
                "animation_length", unreal.FBXAnimationLengthImportType.FBXALIT_EXPORTED_TIME
            )
            task = unreal.AssetImportTask()
            for key, value in {
                "filename": str(vendor / filename),
                "destination_path": folder + "/SourceMotion",
                "destination_name": "AS_" + activity,
                "automated": True,
                "replace_existing": False,
                "save": True,
                "options": options,
            }.items():
                task.set_editor_property(key, value)
            TOOLS.import_asset_tasks([task])
            assets = [unreal.load_asset(path) for path in task.imported_object_paths]
            clips = [asset for asset in assets if isinstance(asset, unreal.AnimSequence)]
            assert len(clips) == 1, task.imported_object_paths
            clip = clips[0]
            assert clip.get_editor_property("skeleton") == skeleton
            assert 0 < clip.sequence_length <= 60, clip.sequence_length
            animations[activity] = clip.get_path_name()
        for asset in [skeleton, mesh]:
            assert LIB.save_loaded_asset(asset)
        report.append(
            {
                "id": "rocketbox-" + identifier,
                "selection_id": identifier,
                "name": name,
                "folder": folder,
                "mesh": mesh.get_path_name(),
                "skeleton": skeleton.get_path_name(),
                "source_mesh": source_mesh.get_path_name(),
                "height_cm": extent.z * 2,
                "offset_cm": [-center.x, -center.y, extent.z - center.z],
                "bones_cm": bones_report,
                "source_animations": animations,
            }
        )
        (args.output / "progress.json").write_text(json.dumps(report, indent=2) + "\n")
    unreal.AutomationLibrary.finish_loading_before_screenshot()
    (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
