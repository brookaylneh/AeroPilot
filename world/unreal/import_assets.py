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
/Game/Valley/Materials; without a surface, opaque materials take M_ValleyPBR and masked ones
the two-sided M_ValleyMasked, clipped by the BC alpha (Nanite voxels need opaque one-sided
materials, so masked foliage keeps its triangles). Families `rocks` and `structures` are rigid
too. A structure's material instances of M_ValleyPrincipled take the Principled inputs its
build.json records per slot, with the Poly Haven scans shared in /Game/Valley/Materials as
T_<Scan>; Nanite renders only opaque and masked materials, so transmissive glass becomes opaque
glossy black. Meshes come from the GLBs through Interchange (metres, glTF +Y up); dimensions are
asserted within 5 % of the spec.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from world.unreal.blender.spec import TEXTURES, digest, load  # noqa: E402

CATEGORIES = {"conifers": "Vegetation", "rocks": "Rocks", "structures": "Structures"}
EXTERNAL = ROOT / "world/unreal/blender/external.json"
MATERIALS = "/Game/Valley/Materials"
TOLERANCE = 0.05
# Blender build frame (metres, right-handed, +Z up, front +X) to Unreal (cm, left-handed).
FLIP = (1, -1, 1)
KINDS = ("BC", "N", "ORM")
DETAIL_KINDS = {"D": "DetailD", "N": "DetailN"}
BLEND_NORMALS = "/Engine/Functions/Engine_MaterialFunctions02/Utility/BlendAngleCorrectedNormals"
FLATTEN_NORMAL = "/Engine/Functions/Engine_MaterialFunctions01/Texturing/FlattenNormal"


def camel(name):
    """`Mill-scale steel` and Interchange's `Mill_scale_steel` are both MillScaleSteel."""
    return "".join(w[:1].upper() + w[1:] for w in re.split(r"[^0-9A-Za-z]+", name))


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


def images(slot):
    """(parameter, image) pairs of a structure material slot's textured inputs."""
    pairs = [("BC", slot["baseColor"]), ("Rough", slot["roughness"])]
    pairs += [("N", normal) for normal in slot["normal"]]
    assert len(slot["normal"]) <= 1, slot
    return [(parameter, image) for parameter, image in pairs if isinstance(image, dict)]


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
    rigid = args.family != "conifers"
    principled = args.family == "structures"
    glb = build / f"{args.asset}.glb"
    assert digest(glb) == report["glb"]["sha256"], glb
    if not rigid:
        assembly = report["details"]["assembly"]
        assert digest(build / "tufts.glb") == assembly["glb"], "tufts.glb"
        assert digest(build / "tufts.json") == assembly["json"], "tufts.json"
    slots = report["details"]["materials"] if principled else {}
    sources = (
        {i["name"]: ROOT / i["path"] for slot in slots.values() for _, i in images(slot)}
        if principled
        else {name: build / f"{name}.png" for name in report["textures"]}
    )
    for name, source in sources.items():
        assert digest(source) == report["textures"][name]["sha256"], name
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
        pipeline.material_pipeline.texture_pipeline.import_textures = source.suffix != ".glb"
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
    kinds["D"] = kinds["Rough"] = kinds["ORM"]
    kinds["N_GL"] = kinds["N"]

    def master(name, normal, detail=False, principled=False, masked=False):
        material = tools.create_asset(name, MATERIALS, unreal.Material, unreal.MaterialFactoryNew())
        if masked:
            material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
            material.set_editor_property("two_sided", True)
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

        parameters = list(("BC", "Rough" if principled else "ORM", "N")[: 2 + normal])
        parameters += list(DETAIL_KINDS.values()) * detail
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
        if principled:

            def scalar(name, y):
                return expression(unreal.MaterialExpressionScalarParameter, y, parameter_name=name)

            def lerp(a, b, alpha, y):
                node = expression(unreal.MaterialExpressionLinearInterpolate, y)
                for (source, pin), socket in zip((a, b, (alpha, "")), ("A", "B", "Alpha")):
                    connect(source, pin, node, socket)
                return node

            # Constant inputs leave the texture unset and take the alpha-0 side of the lerp.
            tint = expression(
                unreal.MaterialExpressionVectorParameter, -260, parameter_name="BaseTint"
            )
            textured = scalar("BaseTextured", -390)
            base = lerp((tint, ""), (nodes["BC"], "RGB"), textured, -520)
            value, textured = scalar("Roughness", -650), scalar("RoughTextured", -780)
            rough = lerp((value, ""), (nodes["Rough"], "R"), textured, -910)
            flat = expression(
                unreal.MaterialExpressionMaterialFunctionCall,
                -1040,
                material_function=unreal.load_asset(FLATTEN_NORMAL),
            )
            connect(nodes["N"], "RGB", flat, "Normal")
            connect(scalar("Flatness", -1170), "", flat, "Flatness")
            links = [(base, "", P.MP_BASE_COLOR), (rough, "", P.MP_ROUGHNESS)]
            links += [(scalar("Metallic", -1300), "", P.MP_METALLIC), (flat, "", P.MP_NORMAL)]
        else:
            links = [(*base, P.MP_BASE_COLOR), (nodes["ORM"], "R", P.MP_AMBIENT_OCCLUSION)]
            links += [(nodes["ORM"], "G", P.MP_ROUGHNESS), (nodes["ORM"], "B", P.MP_METALLIC)]
            links += [(*normals, P.MP_NORMAL)] * normal
            links += [(nodes["BC"], "A", P.MP_OPACITY_MASK)] * masked
        for node, pin, prop in links:
            assert editing.connect_material_property(node, pin, prop), prop
        editing.recompile_material(material)
        assert library.save_loaded_asset(material), name
        return material

    def parent(name, *args, **kwargs):
        path = f"{MATERIALS}/{name}"
        exists = library.does_asset_exist(path)
        return unreal.load_asset(path) if exists else master(name, *args, **kwargs)

    def texture(source, kind, folder, name):
        """Texture `name` imported from `source`; shared textures in MATERIALS import once."""
        path = f"{folder}/{name}"
        if folder == MATERIALS and library.does_asset_exist(path):
            return unreal.load_asset(path)
        (imported,) = interchange(source, folder).values()
        if imported.get_name() != name:
            assert library.rename_asset(imported.get_outermost().get_name(), path), path
        srgb, compression = kinds[kind][2:]
        imported.set_editor_property("srgb", srgb)
        imported.set_editor_property("compression_settings", compression)
        imported.set_editor_property("flip_green_channel", kind == "N_GL")
        assert library.save_loaded_asset(imported), name
        record["textures"][name] = imported.get_path_name()
        return imported

    def assign(mi, parameter, value):
        # The setter always returns False in UE 5.8.2 (MaterialEditingLibrary.cpp:1515).
        editing.set_material_instance_texture_parameter_value(mi, parameter, value)
        assert editing.get_material_instance_texture_parameter_value(mi, parameter) == value

    def scalar(mi, parameter, value):
        editing.set_material_instance_scalar_parameter_value(mi, parameter, value)
        got = editing.get_material_instance_scalar_parameter_value(mi, parameter)
        assert abs(got - value) <= 1e-4 * max(1, abs(value)), (parameter, got, value)

    record = {"textures": {}, "materials": {}}
    instances = {}

    def instance(name, parent):
        mi = tools.create_asset(
            name,
            content,
            unreal.MaterialInstanceConstant,
            unreal.MaterialInstanceConstantFactoryNew(),
        )
        assert mi, name
        editing.set_material_instance_parent(mi, parent)
        record["materials"][name] = mi.get_path_name()
        return mi

    if principled:
        prefix = names(args.asset)[3:].replace("_", "")
        principal = parent("M_ValleyPrincipled", True, False, True)
        for material, slot in slots.items():
            mi = instance(f"MI_{prefix}{camel(material)}", principal)
            for parameter, image in images(slot):
                source = ROOT / image["path"]
                scan = source.is_relative_to(TEXTURES)
                name = f"T_{camel(source.stem)}" if scan else f"T_{prefix}{camel(image['name'])}"
                kind = {"BC": "BC", "Rough": "Rough", "N": "N_GL"}[parameter]
                assign(mi, parameter, texture(source, kind, MATERIALS if scan else content, name))
            base, rough = slot["baseColor"], slot["roughness"]
            constant = isinstance(base, list)
            assert constant or slot["transmission"] == 0, material
            # Opaque glass: the light it would transmit is lost into the dark interior.
            tint = [c * (1 - slot["transmission"]) for c in base] if constant else [1.0] * 3
            colour = unreal.LinearColor(*tint, 1.0)
            editing.set_material_instance_vector_parameter_value(mi, "BaseTint", colour)
            assert editing.get_material_instance_vector_parameter_value(mi, "BaseTint") == colour
            scalar(mi, "BaseTextured", float(not constant))
            scalar(mi, "Roughness", rough if isinstance(rough, float) else 0.0)
            scalar(mi, "RoughTextured", float(isinstance(rough, dict)))
            scalar(mi, "Metallic", slot["metallic"])
            scalar(mi, "Flatness", 1 - sum(n["strength"] for n in slot["normal"]))
            assert library.save_loaded_asset(mi), material
            instances[camel(material)] = mi
    else:
        pngs = report["textures"]
        external = args.family == "external"
        details = report["details"]
        surface = external and details["external"]["surface"] is not None
        if surface:
            pngs = {k: v for k, v in pngs.items() if k not in details["detailTextures"]}
            shared = {
                DETAIL_KINDS[kind]: texture(build / f"{name}.png", kind, MATERIALS, name)
                for name in details["detailTextures"]
                if (kind := name.rsplit("_", 1)[1]) in DETAIL_KINDS
            }
            assert sorted(shared) == sorted(DETAIL_KINDS.values()), shared
            parents = {"opaque": parent("M_ValleyDetail", True, True)}
        elif external:
            assert not details["detailTextures"], details["detailTextures"]
            masked = parent("M_ValleyMasked", True, masked=True)
            parents = {"opaque": parent("M_ValleyPBR", True), "masked": masked}
        else:
            parents = {True: parent("M_ValleyPBR", True), False: parent("M_ValleyFoliage", False)}
        for material, files in textures(build, args.asset, pngs).items():
            key = details["materials"][material]["class"] if external else "N" in files
            mi = instance(f"MI_{material}", parents[key])
            for kind, source in files.items():
                assign(mi, kind, texture(source, kind, content, source.stem))
            if surface:
                for parameter, value in shared.items():
                    assign(mi, parameter, value)
                scalar(mi, "DetailTile", details["detailUvScale"])
            assert library.save_loaded_asset(mi), material
            instances[camel(mi.get_name())] = mi

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
            mesh.set_material(index, instances[camel(str(slot.material_slot_name))])
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
        hulls = sum(name.startswith("UCX_") for name in report["objectTriangles"])
        assert collisions(mesh) == convex == hulls, (collisions(mesh), hulls)
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
