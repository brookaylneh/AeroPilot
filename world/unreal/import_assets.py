"""Import a built Blender asset into Unreal with its materials.

UnrealEditor-Cmd ScenarioStudio.uproject -run=pythonscript -EnablePlugins=PythonScriptPlugin,
EditorScriptingUtilities,NaniteAssemblyEditorUtils
-script="world/unreal/import_assets.py <family> <asset> --build <dir> --report <new .json>"
Reads the family spec and build.json, verifies every input digest and writes
/Game/Valley/<Category>/SM_<Name>_<Variant>, MI_<Material> and T_<Material>_<BC|N|ORM>.
A conifer is a Nanite Foliage assembly with its parts under Parts/SM_<Name>_<Variant>; Nanite
Foliage must be enabled for the project (r.Nanite.Foliage in DefaultEngine.ini). Family
`external` (blender/external.py builds) is one rigid Nanite mesh with its UCX_ collision, whose
material instance of M_ValleyDetail tiles the shared T_Detail<Surface>_{D,N} from
/Game/Valley/Materials. Meshes come from the GLBs through Interchange (metres, glTF +Y up);
dimensions are asserted within 5 % of the spec.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from world.unreal.blender.spec import digest, load  # noqa: E402

CATEGORIES = {"conifers": "Vegetation"}
EXTERNAL = ROOT / "world/unreal/blender/external.json"
MATERIALS = "/Game/Valley/Materials"
TOLERANCE = 0.05
# Blender build frame (metres, right-handed, +Z up, front +X) to Unreal (cm, left-handed).
FLIP = (1, -1, 1)
KINDS = ("BC", "N", "ORM")
DETAIL_KINDS = {"D": "DetailD", "N": "DetailN"}
BLEND_NORMALS = "/Engine/Functions/Engine_MaterialFunctions02/Utility/BlendAngleCorrectedNormals"


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


def textures(build, asset, images):
    """Material name -> {kind: texture path} from the T_<Material>_<Kind> images Blender made.

    Materials carry the asset's name (MI_LodgepoleMatureBark), so variants never collide."""
    prefix = names(asset)[3:].replace("_", "")
    groups = {}
    for name in images:
        material, kind = name[2:].rsplit("_", 1)
        assert material.startswith(prefix), (name, prefix)
        if kind in KINDS:
            groups.setdefault(material, {})[kind] = build / f"{name}.png"
    assert all({"BC", "ORM"} <= set(g) for g in groups.values()), groups
    return groups


def family_spec(family, asset, report):
    """The spec build.json must have been made from, and the content folder of the asset."""
    if family == "external":
        entry = json.loads(EXTERNAL.read_text())["assets"][asset]
        assert report["details"]["external"] == entry, "build.json was made from another entry"
        return report["spec"], f"/Game/Valley/{entry['category']}"
    assert report["spec"] == load(family)[asset], "build.json was made from a different spec"
    return report["spec"], f"/Game/Valley/{CATEGORIES[family]}"


def main():
    import unreal

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("family")
    parser.add_argument("asset")
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(sys.argv[1:])
    build = (ROOT / args.build).resolve(strict=True)
    report = json.loads((build / "build.json").read_text())
    spec, content = family_spec(args.family, args.asset, report)
    rigid = args.family == "external"
    glb = build / f"{args.asset}.glb"
    assert digest(glb) == report["glb"]["sha256"], glb
    if not rigid:
        assembly = report["details"]["assembly"]
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
        # A collision mesh without its UCX_ hull must fail the import, not get a generated one.
        mesh.fallback_collision_type = unreal.InterchangeMeshCollision.NONE
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
    kinds["D"] = kinds["ORM"]

    def master(name, normal, detail=False):
        material = tools.create_asset(name, MATERIALS, unreal.Material, unreal.MaterialFactoryNew())
        # Cooked sections fall back to the default material for any usage not declared here
        # (NaniteResources.cpp:1010 for voxels); headless imports never set them on demand.
        for usage in ("used_with_nanite", "used_with_voxels", "used_with_instanced_static_meshes"):
            material.set_editor_property(usage, True)

        def expression(cls, y, **properties):
            node = editing.create_material_expression(material, cls, -400, y)
            for key, value in properties.items():
                node.set_editor_property(key, value)
            return node

        def connect(source, pin, target, socket):
            assert editing.connect_material_expressions(source, pin, target, socket), socket

        parameters = list(("BC", "ORM", "N")[: 2 + normal]) + list(DETAIL_KINDS.values()) * detail
        nodes = {}
        for i, parameter in enumerate(parameters):
            sampler, default = kinds[parameter.removeprefix("Detail")][:2]
            nodes[parameter] = expression(
                unreal.MaterialExpressionTextureSampleParameter2D,
                260 * i,
                parameter_name=parameter,
                sampler_type=sampler,
                texture=unreal.load_asset(default),
            )
        P = unreal.MaterialProperty
        base, normals = (nodes["BC"], "RGB"), (nodes["N"], "RGB") if normal else None
        if detail:
            tiled = expression(unreal.MaterialExpressionMultiply, -260)
            connect(expression(unreal.MaterialExpressionTextureCoordinate, -520), "", tiled, "A")
            tile = expression(
                unreal.MaterialExpressionScalarParameter, -390, parameter_name="DetailTile"
            )
            connect(tile, "", tiled, "B")
            for parameter in DETAIL_KINDS.values():
                connect(tiled, "", nodes[parameter], "UVs")
            # Base colour times 2 D, matching external.py's Blender material.
            product = expression(unreal.MaterialExpressionMultiply, -650)
            connect(nodes["BC"], "RGB", product, "A")
            connect(nodes["DetailD"], "R", product, "B")
            doubled = expression(unreal.MaterialExpressionMultiply, -780, const_b=2.0)
            connect(product, "", doubled, "A")
            blend = expression(
                unreal.MaterialExpressionMaterialFunctionCall,
                -910,
                material_function=unreal.load_asset(BLEND_NORMALS),
            )
            connect(nodes["N"], "RGB", blend, "BaseNormal")
            connect(nodes["DetailN"], "RGB", blend, "AdditionalNormal")
            base, normals = (doubled, ""), (blend, "")
        links = [(*base, P.MP_BASE_COLOR), (nodes["ORM"], "R", P.MP_AMBIENT_OCCLUSION)]
        links += [(nodes["ORM"], "G", P.MP_ROUGHNESS), (nodes["ORM"], "B", P.MP_METALLIC)]
        links += [(*normals, P.MP_NORMAL)] * normal
        for node, pin, prop in links:
            assert editing.connect_material_property(node, pin, prop), prop
        editing.recompile_material(material)
        assert library.save_loaded_asset(material), name
        return material

    def parent(name, *args):
        path = f"{MATERIALS}/{name}"
        return unreal.load_asset(path) if library.does_asset_exist(path) else master(name, *args)

    def texture(source, kind, folder):
        (imported,) = interchange(source, folder).values()
        assert imported.get_name() == source.stem, imported.get_name()
        srgb, compression = kinds[kind][2:]
        imported.set_editor_property("srgb", srgb)
        imported.set_editor_property("compression_settings", compression)
        assert library.save_loaded_asset(imported), source.stem
        record["textures"][source.stem] = imported.get_path_name()
        return imported

    def assign(mi, parameter, value):
        # The setter always returns False in UE 5.8.2 (MaterialEditingLibrary.cpp:1515).
        editing.set_material_instance_texture_parameter_value(mi, parameter, value)
        assert editing.get_material_instance_texture_parameter_value(mi, parameter) == value

    record = {"textures": {}, "materials": {}}
    images = report["textures"]
    if rigid:
        details = report["details"]
        images = {k: v for k, v in images.items() if k not in details["detailTextures"]}
        shared = {}
        for name in details["detailTextures"]:
            kind = name.rsplit("_", 1)[1]
            if kind in DETAIL_KINDS:
                path = f"{MATERIALS}/{name}"
                exists = library.does_asset_exist(path)
                shared[DETAIL_KINDS[kind]] = (
                    unreal.load_asset(path)
                    if exists
                    else texture(build / f"{name}.png", kind, MATERIALS)
                )
        assert sorted(shared) == sorted(DETAIL_KINDS.values()), shared
        parents = {True: parent("M_ValleyDetail", True, True)}
    else:
        parents = {True: parent("M_ValleyPBR", True), False: parent("M_ValleyFoliage", False)}
    instances = {}
    for material, files in textures(build, args.asset, images).items():
        mi = tools.create_asset(
            f"MI_{material}",
            content,
            unreal.MaterialInstanceConstant,
            unreal.MaterialInstanceConstantFactoryNew(),
        )
        assert mi, material
        editing.set_material_instance_parent(mi, parents["N" in files])
        for kind, source in files.items():
            assign(mi, kind, texture(source, kind, content))
        if rigid:
            for parameter, value in shared.items():
                assign(mi, parameter, value)
            tile = details["detailUvScale"]
            editing.set_material_instance_scalar_parameter_value(mi, "DetailTile", tile)
            got = editing.get_material_instance_scalar_parameter_value(mi, "DetailTile")
            assert abs(got - tile) < 1e-4 * tile, (got, tile)
        assert library.save_loaded_asset(mi), material
        instances[f"MI_{material}"] = mi
        record["materials"][mi.get_name()] = mi.get_path_name()

    def collisions(mesh):
        shapes = mesh.get_editor_property("body_setup").get_editor_property("agg_geom")
        kinds = ("sphere_elems", "box_elems", "sphyl_elems", "convex_elems")
        return sum(len(shapes.get_editor_property(k)) for k in kinds)

    # Foliage parts voxelize; rigid meshes keep their triangles.
    preservation = (
        unreal.NaniteShapePreservation.NONE if rigid else unreal.NaniteShapePreservation.VOXELIZE
    )

    def finish(mesh):
        for index, slot in enumerate(mesh.get_editor_property("static_materials")):
            mesh.set_material(index, instances[str(slot.material_slot_name)])
        nanite = mesh.get_editor_property("nanite_settings")
        nanite.enabled = True
        nanite.shape_preservation = preservation
        mesh.set_editor_property("nanite_settings", nanite)
        assert (collisions(mesh) > 0) == spec["collision"], mesh
        assert library.save_loaded_asset(mesh), mesh.get_name()
        return mesh

    def single():
        (mesh,) = interchange(glb, content).values()
        # Interchange names a lone mesh after its source file.
        source = mesh.get_outermost().get_name()
        assert library.rename_asset(source, destination), destination
        assert not library.does_asset_exist(source), source
        finish(mesh)
        shapes = mesh.get_editor_property("body_setup").get_editor_property("agg_geom")
        convex = len(shapes.get_editor_property("convex_elems"))
        assert collisions(mesh) == convex == spec["collision"], collisions(mesh)
        return mesh, {}

    def assemble():
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
                ue_matrix(n["matrix"], n["translationM"], shift)
                for n in nodes
                if n["part"] == index
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
        return mesh, {"parts": parts, "instances": len(nodes)}

    mesh, extra = single() if rigid else assemble()
    nanite = mesh.get_editor_property("nanite_settings")
    assert nanite.enabled and nanite.shape_preservation == preservation
    box = mesh.get_bounding_box()
    record |= {
        "family": args.family,
        "asset": args.asset,
        "mesh": mesh.get_path_name(),
        "build": {"path": str(build.relative_to(ROOT)), "sha256": digest(build / "build.json")},
        "glbSha256": report["glb"]["sha256"],
        "boundsCm": [[box.min.x, box.min.y, box.min.z], [box.max.x, box.max.y, box.max.z]],
        "naniteTriangles": mesh.get_num_nanite_triangles(),
        "collisionPrimitives": collisions(mesh),
    } | extra
    record["extentsM"] = check_dimensions(record["boundsCm"], spec)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, indent=2) + "\n")
    unreal.log("IMPORT " + json.dumps({k: record[k] for k in ("mesh", "extentsM")} | extra))


if __name__ == "__main__":
    main()
