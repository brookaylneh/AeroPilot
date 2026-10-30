"""Replace the search landscape, its ground layers, water, horizon crests and sun from world.json."""

import hashlib
import json
import math
import os
import sys
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
sites = list((ROOT / ".venv-cosys/lib").glob("python*/site-packages"))
assert len(sites) == 1
sys.path.extend([str(ROOT), str(sites[0])])
from mission_planner.environment import load_env  # noqa: E402
from world.unreal.horizon import CREST_LAYERS, crests  # noqa: E402

FOLDER = "/Game/Valley/Ground"
# Mask layer: CC0 Poly Haven scan and two linear albedo tints mixed by the 120 m hue field.
GROUND = {
    "dry-meadow": ("withered_grass", (1.0, 0.94, 0.49), (1.1, 1.2, 0.83)),
    "wet-meadow": ("grass_ground", (0.77, 0.97, 0.84), (0.95, 0.85, 0.6)),
    "trail-tread": ("rocky_trail", (1.746, 1.968, 2.471), (1.626, 1.836, 2.237)),
    "gravel-road": ("gravel_floor_02", (0.781, 0.729, 0.76), (0.821, 0.779, 0.79)),
    "needle-floor": ("forrest_ground_03", (0.656, 0.741, 0.921), (0.724, 0.751, 0.837)),
    "granite": ("rock_boulder_dry", (1.21, 1.38, 1.54), (1.15, 1.25, 1.3)),
    "cobble-bed": ("ganges_river_pebbles", (0.981, 1.092, 1.01), (0.891, 0.95, 0.884)),
}
# Approved references (.local/references/index.json). A referenced layer's two tints average to the
# reference cell mean / scan mean, in linear albedo.
REFERENCES = {
    "needle-floor": "forest-floor",
    "trail-tread": "granite-trail",
    "gravel-road": "fire-road",
    "cobble-bed": "clear-creek-water",
}
MAPS = {
    "Diffuse": ("BC", True, "TC_DEFAULT", "SAMPLERTYPE_COLOR"),
    "nor_dx": ("N", False, "TC_NORMALMAP", "SAMPLERTYPE_NORMAL"),
    "arm": ("ORM", False, "TC_MASKS", "SAMPLERTYPE_MASKS"),
    "Displacement": ("H", False, "TC_GRAYSCALE", "SAMPLERTYPE_LINEAR_GRAYSCALE"),
}
# Hex tiling (Mikkelsen 2022, https://jcgt.org/published/0011/03/05/): each vertex of a 150 cm
# triangle grid rotates and shifts a layer's scans by hashed values (Hoskins hash32,
# https://www.shadertoy.com/view/4djSRW), so neither a scan period nor a feature orientation
# repeats. Returns the barycentric weights; U are the sample UVs, X and Y their screen gradients
# and R the rotation cosine and sine.
HEX_TILES = """
float2 g = mul(float2x2(1, -0.57735027, 0, 1.15470054), P / 150);
float2 b = floor(g);
float3 t = float3(frac(g), 0);
t.z = 1 - t.x - t.y;
float s = step(0, -t.z);
float2 v[3] = {b + s, b + float2(s, 1 - s), b + float2(1 - s, s)};
float2 u[3], x[3], y[3], r[3];
for (int i = 0; i < 3; i++) {
    float3 q = frac(v[i].xyx * float3(0.1031, 0.103, 0.0973));
    q += dot(q, q.yxz + 33.33);
    q = frac((q.xxy + q.yzz) * q.zyx);
    r[i] = float2(cos(6.2831853 * q.z), sin(6.2831853 * q.z));
    float2x2 m = float2x2(r[i].x, -r[i].y, r[i].y, r[i].x);
    u[i] = mul(m, P / Tile) + q.xy;
    x[i] = mul(m, GX / Tile);
    y[i] = mul(m, GY / Tile);
}
U0 = u[0]; U1 = u[1]; U2 = u[2];
X0 = x[0]; X1 = x[1]; X2 = x[2];
Y0 = y[0]; Y1 = y[1]; Y2 = y[2];
R0 = r[0]; R1 = r[1]; R2 = r[2];
return float3(0, s, s) - t.zyx * (2 * s - 1);
"""
# Luminance-weighted, sharpened weights (Mikkelsen's falloff contrast 0.6, exponent 12) let one
# sample win across most of a cell, so blend zones keep the scan contrast. The landscape tangent
# frame is world +X/+Y, so each normal turns back by its sample's rotation.
HEX_BLEND = """
float3 w = lerp(1, mul(float3x3(BC0, BC1, BC2), float3(0.2126, 0.7152, 0.0722)), 0.6) * pow(W, 12);
w /= w.x + w.y + w.z;
N = w.x * float3(mul(N0.xy, float2x2(R0.x, -R0.y, R0.y, R0.x)), N0.z)
    + w.y * float3(mul(N1.xy, float2x2(R1.x, -R1.y, R1.y, R1.x)), N1.z)
    + w.z * float3(mul(N2.xy, float2x2(R2.x, -R2.y, R2.y, R2.x)), N2.z);
ORM = w.x * ORM0 + w.y * ORM1 + w.z * ORM2;
H = dot(w, float3(H0.r, H1.r, H2.r));
return w.x * BC0 + w.y * BC1 + w.z * BC2;
"""
# Water body: Single Layer Water scattering and absorption per metre (linear RGB), and roughness.
WATER = {
    "granite-lake": ((0.003, 0.012, 0.01), (0.5, 0.12, 0.09), 0.03),
    "lookout-tarn": ((0.001, 0.002, 0.002), (0.8, 0.35, 0.25), 0.03),
    "beaver-pond": ((0.02, 0.015, 0.008), (0.35, 0.5, 1.0), 0.04),
    "meadow-creek": ((0.002, 0.004, 0.003), (0.35, 0.07, 0.04), 0.08),
}
CREST_HAZE = (0.15, 0.3, 0.45, 0.6)

load_env()
world_file = ROOT / "world/cosys/world.json"
world = json.loads(world_file.read_text())
assert (world["coordinateSystem"], world["north"], world["east"]) == (
    "UE_LH_Z_UP_CENTIMETERS",
    "+X",
    "+Y",
)
grid = world["heightfield"]
bounds = world["searchBoundsCm"]
lo, hi = bounds["min"], bounds["max"]
span_x, span_y = hi["x"] - lo["x"], hi["y"] - lo["y"]
spacing_cm = grid["sampleSpacingMetres"] * 100
assert abs(span_x - (grid["columns"] - 1) * spacing_cm) < 1e-6
assert abs(span_y - (grid["rows"] - 1) * spacing_cm) < 1e-6
heightfile = ROOT / grid["path"]
assert hashlib.sha256(heightfile.read_bytes()).hexdigest() == world["heightfieldSha256"]
receipt = json.loads((heightfile.parent / "terrain.json").read_text())
assert receipt["heightfieldSha256"] == world["heightfieldSha256"]
assert receipt["shapeRowsColumns"] == [grid["rows"], grid["columns"]]
assert (receipt["rowAxis"], receipt["columnAxis"]) == (grid["rowAxis"], grid["columnAxis"])
assert receipt["sampleSpacingM"] == grid["sampleSpacingMetres"]
layers = receipt["masks"]["layers"]
assert sorted(layers) == sorted(GROUND) == sorted(receipt["masks"]["files"])
for name in layers:
    entry = receipt["masks"]["files"][name]
    assert hashlib.sha256((ROOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
    assert (ROOT / entry["path"]).parent == heightfile.parent
landmarks = {
    item["id"]: item
    for item in json.loads((ROOT / "world/unreal/landmarks.json").read_text())["landmarks"]
}
lock = json.loads((ROOT / "world/unreal/blender/polyhaven.lock.json").read_text())
assert lock["license"] == "CC0-1.0"
index = json.loads((ROOT / ".local/references/index.json").read_text())
references = {}
for surface_id, reference_id in REFERENCES.items():
    (master,) = [a["files"]["master"] for a in index["assets"] if a["id"] == reference_id]
    data = (ROOT / ".local/references" / master["file"]).read_bytes()
    assert hashlib.sha256(data).hexdigest() == master["sha256"], reference_id
    references[surface_id] = {"reference": reference_id, **master}
report_path = Path(os.environ["SNR_EVIDENCE_DIR"]) / "landscape-authoring.json"
assert not report_path.exists(), "Archive the previous authoring evidence before a new run"

lib = unreal.EditorAssetLibrary
ml = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
landscapes = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.Landscape)]
suns = [a for a in actors.get_all_level_actors() if isinstance(a, unreal.DirectionalLight)]
assert len(landscapes) == len(suns) == 1
previous, sun = landscapes[0], suns[0]
for actor in actors.get_all_level_actors():
    if actor.get_actor_label().startswith("valley."):
        assert actors.destroy_actor(actor)
preserved = {
    a.get_path_name(): a.get_actor_transform()
    for a in actors.get_all_level_actors()
    if a not in (previous, sun)
}


def asset(name, cls, factory):
    path = f"{FOLDER}/{name}"
    exists = lib.does_asset_exist(path)
    result = unreal.load_asset(path) if exists else tools.create_asset(name, FOLDER, cls, factory)
    assert isinstance(result, cls), path
    return result


def graph(material):
    """Clear the material and return a node factory: node(class, (pin, source), ..., **properties)."""
    # delete_all_material_expressions skips every other node: it removes while iterating.
    for expression in ml.get_material_expressions(material):
        ml.delete_material_expression(material, expression)
    assert not ml.get_material_expressions(material)

    def node(cls, *inputs, **properties):
        expression = ml.create_material_expression(material, cls)
        for key, value in properties.items():
            expression.set_editor_property(key, value)
        for pin, source in inputs:
            source, pin_out = source if isinstance(source, tuple) else (source, "")
            assert ml.connect_material_expressions(source, pin_out, expression, pin), pin
        return expression

    return node


def struct(cls, **values):
    """A struct whose properties are not Blueprint-visible, so its constructor takes no arguments."""
    result = cls()
    for key, value in values.items():
        result.set_editor_property(key, value)
    return result


def output(source, prop, pin=""):
    assert ml.connect_material_property(source, pin, getattr(unreal.MaterialProperty, prop)), prop


def save(material):
    errors = ml.recompile_material(material)
    assert not errors, (material.get_name(), errors)
    assert lib.save_loaded_asset(material)


def title(identifier):
    return "".join(part.title() for part in identifier.replace(".", "-").split("-"))


def texture(asset_id, map_name, name):
    suffix, srgb, compression, _ = MAPS[map_name]
    (entry,) = [e for e in lock["files"] if (e["asset"], e["map"]) == (asset_id, map_name)]
    assert entry["resolution"] == "2k" and entry["dimensionsMm"][0] == entry["dimensionsMm"][1]
    source = ROOT / ".local/vendor/polyhaven" / asset_id / Path(entry["url"]).name
    assert hashlib.sha256(source.read_bytes()).hexdigest() == entry["sha256"], source
    task = unreal.AssetImportTask()
    task.filename = str(source)
    task.destination_path = FOLDER
    task.destination_name = f"T_{name}_{suffix}"
    task.automated = True
    task.replace_existing = True
    task.save = True
    tools.import_asset_tasks([task])
    assert len(task.imported_object_paths) == 1
    result = unreal.load_asset(task.imported_object_paths[0])
    result.set_editor_property("srgb", srgb)
    result.set_editor_property(
        "compression_settings", getattr(unreal.TextureCompressionSettings, compression)
    )
    assert lib.save_loaded_asset(result)
    return result, entry


ground = asset("M_ValleyGround", unreal.Material, unreal.MaterialFactoryNew())
node = graph(ground)


def mask(source, channels):
    # New ComponentMask nodes select R and G, so every channel is set.
    return node(
        unreal.MaterialExpressionComponentMask, ("", source), **{c: c in channels for c in "rgba"}
    )


position = mask(node(unreal.MaterialExpressionWorldPosition), "rg")
# Gradients of the continuous position keep the mip level smooth across the cell seams.
gradients = [
    ("GX", node(unreal.MaterialExpressionDDX, ("", position))),
    ("GY", node(unreal.MaterialExpressionDDY, ("", position))),
]


def custom(code, size, inputs, outputs):
    """Custom HLSL returning float<size>, with (name, source) inputs and {name: size} outputs."""
    kind = {n: getattr(unreal.CustomMaterialOutputType, f"CMOT_FLOAT{n}") for n in (1, 2, 3)}
    return node(
        unreal.MaterialExpressionCustom,
        *inputs,
        code=code,
        output_type=kind[size],
        inputs=[struct(unreal.CustomInput, input_name=key) for key, _ in inputs],
        additional_outputs=[
            struct(unreal.CustomOutput, output_name=key, output_type=kind[n])
            for key, n in outputs.items()
        ],
    )


def field(scale_m, octaves, low, high):
    noise = node(
        unreal.MaterialExpressionNoise,
        scale=1 / (scale_m * 100),
        levels=octaves,
        noise_function=unreal.NoiseFunction.NOISEFUNCTION_GRADIENT_TEX3D,
        output_min=0.0,
        output_max=1.0,
        turbulence=False,
    )
    return node(
        unreal.MaterialExpressionSmoothStep, ("Value", noise), const_min=low, const_max=high
    )


# Colour fields of mean 1 at 120 m (hue) and 16 m (patch) keep the map legible from altitude.
hue = field(120, 4, 0.3, 0.7)
patch = node(
    unreal.MaterialExpressionLinearInterpolate,
    ("Alpha", field(16, 3, 0, 1)),
    const_a=0.92,
    const_b=1.08,
)
blends = {
    key: node(
        unreal.MaterialExpressionLandscapeLayerBlend,
        layers=[
            struct(
                unreal.LayerBlendInput,
                layer_name=name,
                blend_type=unreal.LandscapeLayerBlendType.LB_HEIGHT_BLEND,
                preview_weight=float(name == "dry-meadow"),
            )
            for name in layers
        ],
    )
    for key in ("BC", "N", "ORM")
}
sources = {}
for name in layers:
    asset_id, tint_a, tint_b = GROUND[name]
    images = {}
    for map_name, (suffix, _, _, sampler) in MAPS.items():
        image, entry = texture(asset_id, map_name, title(name))
        sources[image.get_name()] = {k: entry[k] for k in ("url", "sha256", "authors", "source")}
        images[suffix] = image, getattr(unreal.MaterialSamplerType, sampler)
        tile_cm = entry["dimensionsMm"][0] / 10
    tiles = custom(
        HEX_TILES,
        3,
        [("P", position), *gradients, ("Tile", node(unreal.MaterialExpressionConstant, r=tile_cm))],
        {f"{key}{i}": 2 for key in "UXYR" for i in range(3)},
    )
    taps = [
        (
            f"{suffix}{i}",
            (
                node(
                    unreal.MaterialExpressionTextureSample,
                    ("UVs", (tiles, f"U{i}")),
                    ("DDX(UVs)", (tiles, f"X{i}")),
                    ("DDY(UVs)", (tiles, f"Y{i}")),
                    texture=image,
                    sampler_type=sampler,
                    sampler_source=unreal.SamplerSourceMode.SSM_WRAP_WORLD_GROUP_SETTINGS,
                    mip_value_mode=unreal.TextureMipValueMode.TMVM_DERIVATIVE,
                ),
                "RGB",
            ),
        )
        for suffix, (image, sampler) in images.items()
        for i in range(3)
    ]
    rotations = [(f"R{i}", (tiles, f"R{i}")) for i in range(3)]
    blend = custom(HEX_BLEND, 3, [("W", tiles), *rotations, *taps], {"N": 3, "ORM": 3, "H": 1})
    tint = node(
        unreal.MaterialExpressionLinearInterpolate,
        ("A", node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(*tint_a))),
        ("B", node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(*tint_b))),
        ("Alpha", hue),
    )
    colour = node(
        unreal.MaterialExpressionMultiply,
        ("A", node(unreal.MaterialExpressionMultiply, ("A", blend), ("B", tint))),
        ("B", patch),
    )
    for key, source, pin in (("BC", colour, ""), ("N", blend, "N"), ("ORM", blend, "ORM")):
        assert ml.connect_material_expressions(source, pin, blends[key], f"Layer {name}")
        assert ml.connect_material_expressions(blend, "H", blends[key], f"Height {name}")
output(blends["BC"], "MP_BASE_COLOR")
output(blends["N"], "MP_NORMAL")
for pin, prop in (("r", "MP_AMBIENT_OCCLUSION"), ("g", "MP_ROUGHNESS")):
    output(mask(blends["ORM"], pin), prop)
save(ground)

assert actors.destroy_actor(previous)
landscape = unreal.ScenarioLandscapeTools.create_landscape(
    str(world_file),
    str(heightfile),
    grid["columns"],
    grid["rows"],
    ground,
    layers,
    FOLDER + "/Layers",
)
assert landscape
for name in layers:
    assert lib.save_asset(f"{FOLDER}/Layers/LI_{name}", only_if_is_dirty=False)

water = asset("M_ValleyWater", unreal.Material, unreal.MaterialFactoryNew())
water.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_SINGLE_LAYER_WATER)
water.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)
node = graph(water)
node(
    unreal.MaterialExpressionSingleLayerWaterMaterialOutput,
    *(
        (
            pin,
            node(
                unreal.MaterialExpressionMultiply,
                ("A", node(unreal.MaterialExpressionVectorParameter, parameter_name=key)),
                const_b=0.01,
            ),
        )
        for pin, key in (
            ("ScatteringCoefficients", "ScatteringPerMetre"),
            ("AbsorptionCoefficients", "AbsorptionPerMetre"),
        )
    ),
    ("PhaseG", node(unreal.MaterialExpressionConstant, r=0.1)),
)
output(node(unreal.MaterialExpressionScalarParameter, parameter_name="Roughness"), "MP_ROUGHNESS")
# Water reflects F0 = 0.02, which is 0.08 * Specular.
output(node(unreal.MaterialExpressionConstant, r=0.25), "MP_SPECULAR")
# Single Layer Water scales the water volume by 1 - Opacity, and an unconnected Opacity is 1, which
# leaves only the black base colour (BasePassPixelShader.usf).
output(node(unreal.MaterialExpressionConstant, r=0.0), "MP_OPACITY")
save(water)

crest = asset("M_HorizonCrest", unreal.Material, unreal.MaterialFactoryNew())
node = graph(crest)
top = node(unreal.MaterialExpressionScalarParameter, parameter_name="CrestTopCm")
rise = node(
    unreal.MaterialExpressionSaturate,
    (
        "",
        node(
            unreal.MaterialExpressionDivide,
            ("A", (node(unreal.MaterialExpressionWorldPosition), "Z")),
            ("B", top),
        ),
    ),
)
rock = node(
    unreal.MaterialExpressionLinearInterpolate,
    (
        "A",
        node(
            unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0.05, 0.06, 0.045)
        ),
    ),
    (
        "B",
        node(
            unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0.35, 0.34, 0.32)
        ),
    ),
    ("Alpha", rise),
)
haze = node(
    unreal.MaterialExpressionLinearInterpolate,
    ("A", rock),
    (
        "B",
        node(
            unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0.32, 0.42, 0.55)
        ),
    ),
    ("Alpha", node(unreal.MaterialExpressionScalarParameter, parameter_name="Haze")),
)
output(haze, "MP_BASE_COLOR")
output(node(unreal.MaterialExpressionConstant, r=0.9), "MP_ROUGHNESS")
save(crest)


def instance(name, parent, scalars, vectors):
    result = asset(
        name, unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew()
    )
    ml.set_material_instance_parent(result, parent)
    # UE 5.8's setters always return False, so each value is read back instead.
    for key, value in scalars.items():
        ml.set_material_instance_scalar_parameter_value(result, key, value)
        assert abs(ml.get_material_instance_scalar_parameter_value(result, key) - value) < 1e-6, key
    for key, value in vectors.items():
        ml.set_material_instance_vector_parameter_value(result, key, unreal.LinearColor(*value))
        stored = ml.get_material_instance_vector_parameter_value(result, key)
        assert all(abs(a - b) < 1e-6 for a, b in zip((stored.r, stored.g, stored.b), value)), key
    assert lib.save_loaded_asset(result)
    return result


def surface(label, vertices, triangles, material):
    """A no-collision, shadowless mesh actor whose every face points up (+Z)."""
    dynamic = unreal.DynamicMesh()
    unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(
        dynamic,
        unreal.GeometryScriptSimpleMeshBuffers(
            vertices=[unreal.Vector(*v) for v in vertices],
            # Planar UVs in metres: the static mesh build computes MikkTSpace tangents from UV0.
            uv0=[unreal.Vector2D(v[0] / 100, v[1] / 100) for v in vertices],
            triangles=[unreal.IntVector(*t) for t in triangles],
        ),
    )
    queries = unreal.GeometryScript_MeshQueries
    assert queries.get_num_triangle_i_ds(dynamic) == len(triangles)
    for index in range(len(triangles)):
        normal, valid = queries.get_triangle_face_normal(dynamic, index)
        assert valid and normal.z > 0, (label, index, normal)
    path = f"{FOLDER}/SM_{title(label.removeprefix('valley.'))}"
    if lib.does_asset_exist(path):
        mesh = unreal.load_asset(path)
        _, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(
            dynamic,
            mesh,
            unreal.GeometryScriptCopyMeshToAssetOptions(
                enable_recompute_normals=True, enable_recompute_tangents=True
            ),
            unreal.GeometryScriptMeshWriteLOD(lod_index=0),
            use_section_materials=False,
        )
    else:
        mesh, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(
            dynamic,
            path,
            unreal.GeometryScriptCreateNewStaticMeshAssetOptions(
                enable_collision=False,
                enable_recompute_normals=True,
                enable_recompute_tangents=True,
            ),
        )
    assert mesh and outcome == unreal.GeometryScriptOutcomePins.SUCCESS
    assert lib.save_loaded_asset(mesh)
    actor = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0))
    actor.set_actor_label(label)
    component = actor.static_mesh_component
    component.set_static_mesh(mesh)
    component.set_material(0, material)
    component.set_collision_profile_name("NoCollision")
    component.set_editor_property("cast_shadow", False)
    collision = component.get_collision_enabled()
    assert collision == unreal.CollisionEnabled.NO_COLLISION, (label, collision)
    return {"mesh": path, "triangles": len(triangles), "material": material.get_path_name()}


def flat(points, z):
    """Ear-clipped shoreline polygon at a flat water level."""
    ring = [(p["x"], p["y"]) for p in points]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(ring, ring[1:] + ring[:1]))
    ring = ring if area > 0 else ring[::-1]
    dynamic = unreal.DynamicMesh()
    unreal.GeometryScript_Primitives.append_triangulated_polygon(
        dynamic,
        unreal.GeometryScriptPrimitiveOptions(),
        unreal.Transform(location=unreal.Vector(0, 0, z)),
        [unreal.Vector2D(x, y) for x, y in ring],
        False,
    )
    queries = unreal.GeometryScript_MeshQueries
    vertices, triangles = [], []
    for index in range(queries.get_num_triangle_i_ds(dynamic)):
        valid, *corners = queries.get_triangle_positions(dynamic, index)
        assert valid and all(abs(c.z - z) < 0.01 for c in corners)
        triangles.append(tuple(range(len(vertices), len(vertices) + 3)))
        vertices += [(c.x, c.y, c.z) for c in corners]
    assert len(triangles) == len(ring) - 2
    return vertices, triangles


def ribbon(points, levels_m, half_width_cm):
    """Flat cross-sections along the creek out to the dry bank tops, 1 cm under the lake and pond
    planes it enters."""
    vertices = []
    for index, (point, level_m) in enumerate(zip(points, levels_m)):
        before, after = points[max(index - 1, 0)], points[min(index + 1, len(points) - 1)]
        dx, dy = after["x"] - before["x"], after["y"] - before["y"]
        length = math.hypot(dx, dy)
        for side in (1, -1):
            vertices.append(
                (
                    point["x"] - side * dy / length * half_width_cm,
                    point["y"] + side * dx / length * half_width_cm,
                    level_m * 100 - 1,
                )
            )
    triangles = []
    for index in range(0, 2 * len(points) - 2, 2):
        triangles += [(index, index + 2, index + 1), (index + 1, index + 2, index + 3)]
    return vertices, triangles


bodies = {}
creek = receipt["drainage"]["creek"]
for identifier, (scattering, absorption, roughness) in WATER.items():
    item = landmarks[identifier]
    points = item["geometry"]["points"]
    material = instance(
        "MI_" + title(identifier),
        water,
        {"Roughness": roughness},
        {"ScatteringPerMetre": scattering, "AbsorptionPerMetre": absorption},
    )
    if item["kind"] == "pond":
        assert item["geometry"]["type"] == "polygon"
        level_cm = receipt["basins"][identifier]["surfaceM"] * 100
        mesh = flat(points, level_cm)
    else:
        assert (item["kind"], item["geometry"]["type"], creek["id"]) == (
            "creek",
            "polyline",
            identifier,
        )
        assert len(points) == creek["routeNodes"] == len(creek["waterSurfaceM"])
        level_cm = [creek["waterSurfaceM"][0] * 100, creek["waterSurfaceM"][-1] * 100]
        mesh = ribbon(points, creek["waterSurfaceM"], creek["bankTopM"] * 100)
    bodies[identifier] = {
        **surface(f"valley.water.{identifier}", *mesh, material),
        "surfaceCm": level_cm,
    }

horizon = []
for index, ((vertices, triangles), haze, (_, _, top_m)) in enumerate(
    zip(crests((lo["x"], lo["y"]), (hi["x"], hi["y"])), CREST_HAZE, CREST_LAYERS, strict=True)
):
    material = instance(
        f"MI_HorizonCrest{index}", crest, {"Haze": haze, "CrestTopCm": top_m * 100}, {}
    )
    # horizon.py makes cross(b - a, c - a) point up; GeometryScript normals follow cross(c - a, b - a).
    flipped = [(a, c, b) for a, b, c in triangles]
    horizon.append(surface(f"valley.horizon.crest{index}", vertices, flipped, material))

sun_rotation = unreal.Rotator(
    roll=0,
    pitch=-world["sun"]["apparentElevationDegrees"],
    yaw=world["sun"]["azimuthDegreesClockwiseFromNorth"] - 180,
)
sun.set_actor_rotation(sun_rotation, False)
toward_sun = -sun.get_actor_forward_vector()
assert abs(math.degrees(math.asin(toward_sun.z)) - world["sun"]["apparentElevationDegrees"]) < 1e-3
azimuth = math.degrees(math.atan2(toward_sun.y, toward_sun.x)) % 360
assert abs(azimuth - world["sun"]["azimuthDegreesClockwiseFromNorth"]) < 1e-3

catalog = unreal.load_asset("/Game/OpenMeadow/Catalog")
assert catalog
regions = catalog.get_editor_property("regions")
assert len(regions) == 1
region = regions[0]
region.set_editor_property("min", unreal.Vector(lo["x"], lo["y"], lo["z"]))
region.set_editor_property("max", unreal.Vector(hi["x"], hi["y"], hi["z"]))
region.set_editor_property("display_name", "Open Meadow")
regions[0] = region
catalog.set_editor_property("regions", regions)
catalog.set_editor_property("asset_version", world["environmentAssetVersion"])
assert lib.save_loaded_asset(catalog)
assert levels.save_current_level()

origin, extent = landscape.get_actor_bounds(False)
assert abs(extent.x * 2 - span_x) < 0.1
assert abs(extent.y * 2 - span_y) < 0.1
assert abs(origin.x - (lo["x"] + hi["x"]) / 2) < 0.1
assert abs(origin.y - (lo["y"] + hi["y"]) / 2) < 0.1
component_count = len(landscape.get_components_by_class(unreal.LandscapeComponent))
assert component_count == (grid["columns"] - 1) * (grid["rows"] - 1) // (126 * 126)
remaining = {
    a.get_path_name(): a
    for a in actors.get_all_level_actors()
    if a not in (landscape, sun) and not a.get_actor_label().startswith("valley.")
}
assert remaining.keys() == preserved.keys()
for name, transform in preserved.items():
    assert remaining[name].get_actor_transform() == transform, name
report_path.write_text(
    json.dumps(
        {
            "environmentAssetVersion": world["environmentAssetVersion"],
            "heightfieldSha256": world["heightfieldSha256"],
            "masks": receipt["masks"]["files"],
            "extentCm": {"northSouth": extent.x * 2, "eastWest": extent.y * 2},
            "landscapeComponents": component_count,
            "landscapeMaterial": ground.get_path_name(),
            "groundTextures": sources,
            "groundLicence": lock["license"],
            "references": references,
            "water": bodies,
            "horizon": horizon,
            "sunRotation": {"pitch": sun_rotation.pitch, "yaw": sun_rotation.yaw},
            "preservedActors": sorted(preserved),
        },
        indent=2,
    )
    + "\n"
)
unreal.log("SNR_OPEN_MEADOW_AUTHORED")
