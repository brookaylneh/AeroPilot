"""Assemble installed Epic presets through the authenticated MetaHuman pipeline."""

import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[2]
(root / ".local/evidence/phase3").mkdir(parents=True, exist_ok=True)
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
subsystem = unreal.get_editor_subsystem(unreal.MetaHumanCharacterEditorSubsystem)
report = []
for name, preset in [("Walter", "Walter"), ("Etta", "Etta"), ("Sookja", "Sook-ja")]:
    path = "/Game/Scenario/Characters/MH_" + name
    character = (
        unreal.load_asset(path)
        if lib.does_asset_exist(path)
        else tools.duplicate_asset(
            "MH_" + name,
            "/Game/Scenario/Characters",
            unreal.load_asset("/MetaHumanCharacter/Optional/Presets/" + preset),
        )
    )
    assert character and subsystem.try_add_object_to_edit(character), "Cannot open preset " + name
    try:
        if not character.get_editor_property("has_face_dna_blendshapes"):
            request = unreal.MetaHumanCharacterAutoRiggingRequestParams()
            request.set_editor_property("rig_type", unreal.MetaHumanRigType.JOINTS_AND_BLEND_SHAPES)
            request.set_editor_property("report_progress", False)
            request.set_editor_property("blocking", True)
            subsystem.request_auto_rigging(character, request)
            assert character.get_editor_property("has_face_dna_blendshapes"), (
                "Epic rig service unavailable for " + name
            )
        if not character.get_editor_property("has_high_resolution_textures"):
            request = unreal.MetaHumanCharacterTextureRequestParams()
            request.set_editor_property("report_progress", False)
            request.set_editor_property("blocking", True)
            subsystem.request_texture_sources(character, request)
            assert character.get_editor_property("has_high_resolution_textures"), (
                "Epic texture service unavailable for " + name
            )
        lib.save_loaded_asset(character, only_if_is_dirty=False)
        assert subsystem.can_build_meta_human(character, True), "Preset is not ready to assemble"
        params = unreal.MetaHumanCharacterEditorBuildParameters()
        params.set_editor_property("pipeline_type", unreal.MetaHumanDefaultPipelineType.OPTIMIZED)
        params.set_editor_property("pipeline_quality", unreal.MetaHumanQualityLevel.HIGH)
        params.set_editor_property("absolute_build_path", "/Game/Scenario/MetaHumans")
        params.set_editor_property("name_override", name)
        params.set_editor_property("common_folder_path", "/Game/Scenario/MetaHumans/Common")
        params.set_editor_property("enable_wardrobe_item_validation", True)
        subsystem.build_meta_human(character, params)
        assert lib.does_asset_exist("/Game/Scenario/MetaHumans/" + name + "/BP_" + name), (
            "No assembled Blueprint"
        )
        lib.save_directory("/Game/Scenario/MetaHumans", only_if_is_dirty=True, recursive=True)
        report.append(name)
        (root / ".local/evidence/phase3/assembled-people.json").write_text(json.dumps(report))
    finally:
        subsystem.remove_object_to_edit(character)
unreal.log("SNR_CREATOR_PEOPLE_ASSEMBLED")
