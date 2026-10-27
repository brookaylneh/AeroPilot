"""Import the five acquired scans, their authored skeletons and animations.

Run scripts/rig-children.py in Blender first. Licensed meshes and textures remain
private dependencies; this script records the reproducible Unreal import setup.
"""

import json
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
source = root / "vendor/children-fab/rigged"
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
ml = unreal.MaterialEditingLibrary
report = []


def import_asset(filename, folder, options=None):
    task = unreal.AssetImportTask()
    for key, value in dict(
        filename=str(filename),
        destination_path=folder,
        automated=True,
        replace_existing=True,
        save=True,
    ).items():
        task.set_editor_property(key, value)
    if options:
        task.set_editor_property("options", options)
    tools.import_asset_tasks([task])
    assert task.imported_object_paths, "Import failed: " + str(filename)
    return [unreal.load_asset(path) for path in task.imported_object_paths]


def blueprint(path, parent):
    if lib.does_asset_exist(path):
        return unreal.load_asset(path)
    factory = unreal.BlueprintFactory()
    factory.set_editor_property("parent_class", parent)
    folder, name = path.rsplit("/", 1)
    return tools.create_asset(name, folder, unreal.Blueprint, factory)


for index in range(1, 6):
    name = f"Child_{index:02d}"
    folder = "/Game/Scenario/Children/" + name
    options = unreal.FbxImportUI()
    options.set_editor_property("automated_import_should_detect_type", False)
    options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)
    options.set_editor_property("import_as_skeletal", True)
    options.set_editor_property("import_mesh", True)
    options.set_editor_property("import_animations", False)
    options.set_editor_property("import_materials", False)
    options.set_editor_property("import_textures", False)
    options.set_editor_property("create_physics_asset", False)
    options.skeletal_mesh_import_data.set_editor_property(
        "vertex_color_import_option", unreal.VertexColorImportOption.REPLACE
    )
    imported = import_asset(source / (name + ".fbx"), folder, options)
    mesh = next(asset for asset in imported if isinstance(asset, unreal.SkeletalMesh))
    skeleton = mesh.get_editor_property("skeleton")
    assert skeleton, "Missing skeleton: " + name
    animations = {}
    supported = ["lost", "waiting"] if index == 5 else ["lost", "waiting", "injured"]
    for situation in supported:
        options = unreal.FbxImportUI()
        options.set_editor_property("automated_import_should_detect_type", False)
        options.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_ANIMATION)
        options.set_editor_property("import_mesh", False)
        options.set_editor_property("import_animations", True)
        options.set_editor_property("skeleton", skeleton)
        options.anim_sequence_import_data.set_editor_property(
            "animation_length", unreal.FBXAnimationLengthImportType.FBXALIT_EXPORTED_TIME
        )
        imported = import_asset(source / f"{name}_{situation}.fbx", folder, options)
        animations[situation] = next(
            asset for asset in imported if isinstance(asset, unreal.AnimSequence)
        )
    texture = import_asset(source / (name + "_Albedo.png"), folder)[0]
    material_name = "M_" + name
    material_path = folder + "/" + material_name
    material = (
        unreal.load_asset(material_path)
        if lib.does_asset_exist(material_path)
        else tools.create_asset(material_name, folder, unreal.Material, unreal.MaterialFactoryNew())
    )
    ml.delete_all_material_expressions(material)

    def node(kind, **properties):
        result = ml.create_material_expression(material, kind)
        for key, value in properties.items():
            result.set_editor_property(key, value)
        return result

    def wire(origin, output, destination, input_name):
        assert ml.connect_material_expressions(origin, output, destination, input_name)

    sample = node(unreal.MaterialExpressionTextureSample, texture=texture)
    mask = node(unreal.MaterialExpressionVertexColor)
    luminance = node(unreal.MaterialExpressionDesaturation)
    wire(node(unreal.MaterialExpressionConstant, r=1.0), "", luminance, "Fraction")
    wire(sample, "RGB", luminance, "")
    # Preserve fabric shading while replacing its hue. Skin and fixed equipment
    # use the unmodified scan outside the authored clothing masks.
    result = sample
    result_output = "RGB"
    for parameter, channel in [("UpperTint", "R"), ("LowerTint", "G")]:
        tint = node(
            unreal.MaterialExpressionVectorParameter,
            parameter_name=parameter,
            default_value=unreal.LinearColor(0.4, 0.4, 0.4, 1),
        )
        colored = node(unreal.MaterialExpressionMultiply)
        wire(luminance, "", colored, "A")
        wire(tint, "", colored, "B")
        mixed = node(unreal.MaterialExpressionLinearInterpolate)
        wire(result, result_output, mixed, "A")
        wire(colored, "", mixed, "B")
        wire(mask, channel, mixed, "Alpha")
        result, result_output = mixed, ""
    ml.connect_material_property(result, "", unreal.MaterialProperty.MP_BASE_COLOR)
    roughness = node(unreal.MaterialExpressionConstant, r=0.72)
    ml.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)
    ml.set_material_usage(material, unreal.MaterialUsage.MATUSAGE_SKELETAL_MESH)
    ml.recompile_material(material)
    lib.save_loaded_asset(material)
    materials = mesh.get_editor_property("materials")
    assert len(materials) == 1, "Unexpected scan material slots"
    mesh.set_editor_property(
        "materials",
        [unreal.SkeletalMaterial(material_interface=material, material_slot_name="Scan")],
    )
    assert mesh.get_editor_property("materials")[0].material_interface == material
    lib.save_loaded_asset(mesh)
    bp_path = folder + "/BP_" + name
    bp = blueprint(bp_path, unreal.SkeletalMeshActor)
    default = unreal.get_default_object(lib.load_blueprint_class(bp_path))
    body = default.get_editor_property("skeletal_mesh_component")
    body.set_skeletal_mesh_asset(mesh)
    body.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    lib.save_loaded_asset(bp)
    report.append(
        dict(
            id=f"child-{index:02d}",
            blueprint=bp_path,
            bodyComponent=body.get_name(),
            mesh=mesh.get_path_name(),
            animations={key: value.get_path_name() for key, value in animations.items()},
        )
    )
(root / "unreal/children-runtime.json").write_text(json.dumps(report, indent=2) + "\n")
unreal.log("SNR_CHILDREN_IMPORTED")
