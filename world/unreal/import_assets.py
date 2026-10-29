"""Import a built Blender asset into Unreal as a Nanite Foliage assembly with its materials.

UnrealEditor-Cmd ScenarioStudio.uproject -run=pythonscript -EnablePlugins=PythonScriptPlugin,
EditorScriptingUtilities,NaniteAssemblyEditorUtils
-script="world/unreal/import_assets.py <family> <asset> --build <dir> --report <new .json>"
Reads the family spec and build.json, verifies every input digest and writes
/Game/Valley/<Category>/SM_<Name>_<Variant>, its parts under Parts/SM_<Name>_<Variant>,
MI_<Material> and T_<Material>_<BC|N|ORM>. Meshes come from the GLBs through Interchange
(metres, glTF +Y up); dimensions are asserted within 5 % of the spec. Nanite Foliage must be
enabled for the project (r.Nanite.Foliage in DefaultEngine.ini).
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from world.unreal.blender.spec import digest, load  # noqa: E402

CATEGORIES = {"conifers": "Vegetation"}
MATERIALS = "/Game/Valley/Materials"
TOLERANCE = 0.05
# Blender build frame (metres, right-handed, +Z up, front +X) to Unreal (cm, left-handed).
FLIP = (1, -1, 1)
KINDS = ("BC", "N", "ORM")


def names(asset):
    """`lodgepole-mature` is mesh SM_Lodgepole_Mature."""
    name, variant = asset.split("-", 1)
    return f"SM_{name.title()}_{variant.title().replace('-', '')}"


def ue_matrix(matrix, translation, shift):
    """Row-major Unreal matrix (rows are the images of the local axes) for a build-frame
    instance whose 3x3 `matrix` columns are its scaled local axes."""
    assert len(matrix) == 3 and all(len(row) == 3 for row in matrix), matrix
    (a, b, c), (d, e, f), (g, h, i) = matrix
    assert a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g) > 0, matrix
    axes = [[FLIP[r] * matrix[r][k] * FLIP[k] for r in range(3)] + [0.0] for k in range(3)]
    location = [100 * FLIP[r] * (translation[r] + shift[r]) for r in range(3)]
    return axes + [location + [1.0]]


def check_dimensions(box_cm, spec):
    """Unreal bounding-box extents in cm against the spec's metres, axis by axis."""
    extents = [(high - low) / 100 for low, high in zip(*box_cm)]
    for got, expected in zip(extents, spec["dimensionsM"], strict=True):
        assert abs(got - expected) <= TOLERANCE * expected, (extents, spec["dimensionsM"])
    return extents


def textures(build, report):
    """Material name -> {kind: texture path} from the T_<Material>_<Kind> images Blender made.

    Materials carry the asset's name (MI_LodgepoleMatureBark), so variants never collide."""
    prefix = names(report["asset"])[3:].replace("_", "")
    groups = {}
    for name in report["textures"]:
        material, kind = name[2:].rsplit("_", 1)
        assert material.startswith(prefix), (name, prefix)
        if kind in KINDS:
            groups.setdefault(material, {})[kind] = build / f"{name}.png"
    assert all({"BC", "ORM"} <= set(g) for g in groups.values()), groups
    return groups


def main():
    import unreal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family")
    parser.add_argument("asset")
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(sys.argv[1:])
    spec = load(args.family)[args.asset]
    build = (ROOT / args.build).resolve(strict=True)
    report = json.loads((build / "build.json").read_text())
    assert report["spec"] == spec, "build.json was made from a different spec"
    assembly = report["details"]["assembly"]
    glb = build / f"{args.asset}.glb"
    assert digest(glb) == report["glb"]["sha256"], glb
    assert digest(build / "tufts.glb") == assembly["glb"], "tufts.glb"
    assert digest(build / "tufts.json") == assembly["json"], "tufts.json"
    for name, entry in report["textures"].items():
        assert digest(build / f"{name}.png") == entry["sha256"], name
    output = (ROOT / args.report).resolve()
    assert output.suffix == ".json" and not output.exists(), output
    assert unreal.SystemLibrary.get_console_variable_int_value("r.Nanite.Foliage") == 1
    library = unreal.EditorAssetLibrary
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    editing = unreal.MaterialEditingLibrary
    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    content = f"/Game/Valley/{CATEGORIES[args.family]}"
    destination = f"{content}/{names(args.asset)}"
    parts = f"{content}/Parts/{names(args.asset)}"
    assert not library.does_asset_exist(destination), destination
    assert not library.does_directory_exist(parts), parts

    def interchange(source, folder):
        pipeline = unreal.InterchangeGenericAssetsPipeline()
        mesh = pipeline.mesh_pipeline
        mesh.combine_static_meshes_behavior = (
            unreal.InterchangeCombineStaticMeshesBehavior.DO_NOT_COMBINE
        )
        mesh.build_nanite = True
        mesh.collision = spec["collision"]
        pipeline.material_pipeline.import_materials = False
        pipeline.material_pipeline.texture_pipeline.import_textures = source.suffix == ".png"
        parameters = unreal.ImportAssetParameters()
        parameters.is_automated = True
        parameters.replace_existing = False
        parameters.override_pipelines = [unreal.SoftObjectPath(pipeline.get_path_name())]
        data = unreal.InterchangeManager.create_source_data(str(source))
        imported = manager.import_asset(folder, data, parameters)
        assert imported, source
        return {a.get_name(): a for a in imported}

    # Sampler, parameter default and texture settings per kind. The platform shader compile
    # fails when a default's compression does not match its sampler.
    S, C = unreal.MaterialSamplerType, unreal.TextureCompressionSettings
    kinds = {
        "BC": (S.SAMPLERTYPE_COLOR, "/Engine/EngineResources/DefaultTexture", True, C.TC_DEFAULT),
        "ORM": (
            S.SAMPLERTYPE_LINEAR_COLOR,
            "/Engine/EngineMaterials/BaseFlattenLinearColor",
            False,
            C.TC_DEFAULT,
        ),
        "N": (
            S.SAMPLERTYPE_NORMAL,
            "/Engine/EngineMaterials/BaseFlattenNormalMap",
            False,
            C.TC_NORMALMAP,
        ),
    }

    def master(name, normal):
        material = tools.create_asset(name, MATERIALS, unreal.Material, unreal.MaterialFactoryNew())
        # Cooked sections fall back to the default material for any usage not declared here
        # (NaniteResources.cpp:1010 for voxels); headless imports never set them on demand.
        for usage in ("used_with_nanite", "used_with_voxels", "used_with_instanced_static_meshes"):
            material.set_editor_property(usage, True)
        nodes = {}
        for i, parameter in enumerate(("BC", "ORM", "N")[: 2 + normal]):
            sampler, default = kinds[parameter][:2]
            node = editing.create_material_expression(
                material, unreal.MaterialExpressionTextureSampleParameter2D, -400, 260 * i
            )
            node.set_editor_property("parameter_name", parameter)
            node.set_editor_property("sampler_type", sampler)
            node.set_editor_property("texture", unreal.load_asset(default))
            nodes[parameter] = node
        P = unreal.MaterialProperty
        links = [("BC", "RGB", P.MP_BASE_COLOR), ("ORM", "R", P.MP_AMBIENT_OCCLUSION)]
        links += [("ORM", "G", P.MP_ROUGHNESS), ("ORM", "B", P.MP_METALLIC)]
        links += [("N", "RGB", P.MP_NORMAL)] * normal
        for parameter, pin, prop in links:
            assert editing.connect_material_property(nodes[parameter], pin, prop), parameter
        editing.recompile_material(material)
        assert library.save_loaded_asset(material), name
        return material

    def parent(name, normal):
        path = f"{MATERIALS}/{name}"
        return unreal.load_asset(path) if library.does_asset_exist(path) else master(name, normal)

    parents = {True: parent("M_ValleyPBR", True), False: parent("M_ValleyFoliage", False)}
    instances, record = {}, {"textures": {}, "materials": {}}
    for material, files in textures(build, report).items():
        mi = tools.create_asset(
            f"MI_{material}",
            content,
            unreal.MaterialInstanceConstant,
            unreal.MaterialInstanceConstantFactoryNew(),
        )
        assert mi, material
        editing.set_material_instance_parent(mi, parents["N" in files])
        for kind, source in files.items():
            (texture,) = interchange(source, content).values()
            assert texture.get_name() == source.stem, texture.get_name()
            srgb, compression = kinds[kind][2:]
            texture.set_editor_property("srgb", srgb)
            texture.set_editor_property("compression_settings", compression)
            assert library.save_loaded_asset(texture), source.stem
            # The setter always returns False in UE 5.8.2 (MaterialEditingLibrary.cpp:1515).
            editing.set_material_instance_texture_parameter_value(mi, kind, texture)
            assert editing.get_material_instance_texture_parameter_value(mi, kind) == texture
            record["textures"][source.stem] = texture.get_path_name()
        assert library.save_loaded_asset(mi), material
        instances[f"MI_{material}"] = mi
        record["materials"][mi.get_name()] = mi.get_path_name()

    def collisions(mesh):
        shapes = mesh.get_editor_property("body_setup").get_editor_property("agg_geom")
        kinds = ("sphere_elems", "box_elems", "sphyl_elems", "convex_elems")
        return sum(len(shapes.get_editor_property(k)) for k in kinds)

    def finish(mesh):
        for index, slot in enumerate(mesh.get_editor_property("static_materials")):
            mesh.set_material(index, instances[str(slot.material_slot_name)])
        nanite = mesh.get_editor_property("nanite_settings")
        nanite.enabled = True
        nanite.shape_preservation = unreal.NaniteShapePreservation.VOXELIZE
        mesh.set_editor_property("nanite_settings", nanite)
        assert (collisions(mesh) > 0) == spec["collision"], mesh
        assert library.save_loaded_asset(mesh), mesh.get_name()
        return mesh

    meshes = interchange(glb, parts)
    roles = [assembly["base"], assembly["realized"], *assembly["whole"]]
    assert sorted(meshes) == sorted(roles), sorted(meshes)
    assert library.delete_asset(meshes.pop(assembly["realized"]).get_outermost().get_name())
    meshes |= interchange(build / "tufts.glb", parts)
    assert sorted(meshes) == sorted(roles[:1] + roles[2:] + assembly["parts"]), sorted(meshes)
    nodes = json.loads((build / "tufts.json").read_text())["instances"]
    assert len(nodes) == assembly["instances"], len(nodes)
    mesh = finish(meshes[assembly["base"]])
    builder = unreal.NaniteAssemblyStaticMeshBuilder.begin_edit_static_mesh_assembly_build(mesh)
    for name in assembly["whole"]:
        # Whole parts share the base's frame: one instance at the origin.
        assert builder.add_assembly_parts(finish(meshes[name]), [unreal.Transform()]), name
    shift = report["originShiftM"]
    for index, name in enumerate(assembly["parts"]):
        rows = [
            ue_matrix(n["matrix"], n["translationM"], shift) for n in nodes if n["part"] == index
        ]
        transforms = [
            unreal.MathLibrary.conv_matrix_to_transform(
                unreal.Matrix(*(unreal.Plane(*row) for row in matrix))
            )
            for matrix in rows
        ]
        assert transforms and builder.add_assembly_parts(finish(meshes[name]), transforms), name
    mesh = builder.finish_assembly_build()
    assert mesh, "Nanite assembly build failed"
    assert library.rename_asset(mesh.get_outermost().get_name(), destination), destination
    nanite = mesh.get_editor_property("nanite_settings")
    assert nanite.enabled and nanite.shape_preservation == unreal.NaniteShapePreservation.VOXELIZE
    box = mesh.get_bounding_box()
    record |= {
        "family": args.family,
        "asset": args.asset,
        "mesh": mesh.get_path_name(),
        "parts": parts,
        "instances": len(nodes),
        "build": {"path": str(build.relative_to(ROOT)), "sha256": digest(build / "build.json")},
        "glbSha256": report["glb"]["sha256"],
        "boundsCm": [[box.min.x, box.min.y, box.min.z], [box.max.x, box.max.y, box.max.z]],
        "naniteTriangles": mesh.get_num_nanite_triangles(),
        "collisionPrimitives": collisions(mesh),
    }
    record["extentsM"] = check_dimensions(record["boundsCm"], spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n")
    unreal.log("IMPORT " + json.dumps({k: record[k] for k in ("mesh", "extentsM", "instances")}))


if __name__ == "__main__":
    main()
