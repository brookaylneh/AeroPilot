"""Author a small daypack with padded straps, pocket, seams and fabric shading.

Geometry is original project work. Fitting and visual acceptance are separate
runtime checks; its dimensions are in centimeters around a standing adult.
"""
import json
import math
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[1]
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
matlib = unreal.MaterialEditingLibrary
folder = '/Game/Scenario/Equipment'
vertices, triangles, uv, colors = [], [], [], []

def signed(value, exponent):
    return math.copysign(abs(value) ** exponent, value)

def patch(points, rows, columns, tint=1, metal=0, rough=1, repeat=(1, 1)):
    start = len(vertices)
    vertices.extend(points)
    for iy in range(rows):
        for ix in range(columns):
            uv.append(unreal.Vector2D(ix / (columns - 1) * repeat[0], iy / (rows - 1) * repeat[1]))
            colors.append(unreal.LinearColor(tint, metal, rough, 1))
            if iy < rows - 1 and ix < columns - 1:
                a = start + iy * columns + ix
                triangles.extend([unreal.IntVector(a, a + 1, a + columns), unreal.IntVector(a + 1, a + columns + 1, a + columns)])

def shell(center, axes, exponent=.45, tint=1, metal=0, rough=1):
    rows, columns = (7, 13) if max(axes) < 3 else (25, 49)
    points = []
    for iy in range(rows):
        latitude = -math.pi / 2 + math.pi * iy / (rows - 1)
        for ix in range(columns):
            longitude = 2 * math.pi * ix / (columns - 1)
            points.append(unreal.Vector(
                center[0] + axes[0] * signed(math.cos(latitude), exponent) * signed(math.cos(longitude), exponent),
                center[1] + axes[1] * signed(math.cos(latitude), exponent) * signed(math.sin(longitude), exponent),
                center[2] + axes[2] * signed(math.sin(latitude), exponent)))
    patch(points, rows, columns, tint, metal, rough, (10, 5))

def tube(points, width, depth, tint=0, metal=0, rough=1):
    shape = []
    sides = 13
    for index, point in enumerate(points):
        previous, following = points[max(0, index - 1)], points[min(len(points) - 1, index + 1)]
        direction = following - previous
        length = max(.0001, math.sqrt(direction.x ** 2 + direction.y ** 2 + direction.z ** 2))
        direction /= length
        reference = unreal.Vector(0, 1, 0) if abs(direction.y) < .8 else unreal.Vector(1, 0, 0)
        projection = reference.x * direction.x + reference.y * direction.y + reference.z * direction.z
        axis = reference - direction * projection
        axis /= max(.0001, math.sqrt(axis.x ** 2 + axis.y ** 2 + axis.z ** 2))
        normal = unreal.Vector(direction.y * axis.z - direction.z * axis.y,
                               direction.z * axis.x - direction.x * axis.z,
                               direction.x * axis.y - direction.y * axis.x)
        for side in range(sides):
            angle = math.tau * side / (sides - 1)
            shape.append(point + axis * (math.cos(angle) * width) + normal * (math.sin(angle) * depth))
    patch(shape, len(points), sides, tint, metal, rough, (1, 10))

def catmull(points):
    result = []
    # Calculate with Python scalars: Unreal's reflected vector negation can
    # modify a referenced vector, corrupting subsequent spline segments.
    coordinates = [(p.x, p.y, p.z) for p in points]
    padded = [coordinates[0]] + coordinates + [coordinates[-1]]
    for index in range(1, len(padded) - 2):
        a, b, c, d = padded[index - 1:index + 3]
        for step in range(12):
            t = step / 12
            result.append(unreal.Vector(*[
                .5 * (2 * b[k] + (c[k] - a[k]) * t
                      + (2 * a[k] - 5 * b[k] + 4 * c[k] - d[k]) * t * t
                      + (-a[k] + 3 * b[k] - 3 * c[k] + d[k]) * t * t * t)
                for k in range(3)]))
    result.append(points[-1])
    return result

shell((-22, 0, 123), (10, 16, 23))
shell((-30, 0, 115), (5, 13, 12), tint=.85)
shell((-20, 0, 103), (10, 15, 4), tint=0)
shell((-22, 0, 144), (10, 15, 4), tint=.75)
shell((-11.5, 0, 123), (1.4, 14, 20), tint=0)
for side in [-1, 1]:
    path = [unreal.Vector(x, side * y, z) for x, y, z in
            [(-16, 13, 141), (-9, 14, 147), (1, 15, 149), (12, 15, 145),
             (16, 14, 135), (13, 13, 119), (1, 13, 106), (-16, 12, 105)]]
    tube(catmull(path), 2.3, .45)
    shell((13, side * 13, 119), (1, 2.7, 2), exponent=.3, tint=0, rough=.55)
    # Side pouch and compression strap.
    shell((-22, side * 16, 112), (6, 3, 9), tint=.4)
    tube([unreal.Vector(x, side * 18, 125) for x in range(-28, -13)], .65, .3)
# Recessed zipper around the front pocket. Small dark teeth catch daylight.
for index in range(100):
    angle = math.tau * index / 100
    y, z = 12 * signed(math.cos(angle), .5), 115 + 10 * signed(math.sin(angle), .5)
    shell((-35.1, y, z), (.16, .22, .22), exponent=.7, tint=0, metal=.65, rough=.35)
shell((-35.5, 10, 123), (.25, .5, 1.4), exponent=.35, tint=0, metal=.7, rough=.3)
# Top handle.
tube(catmull([unreal.Vector(-15, y, z) for y, z in [(-5, 146), (-4, 151), (4, 151), (5, 146)]]), .6, .3)

normals = [unreal.Vector() for _ in vertices]
for triangle in triangles:
    a, b, c = triangle.x, triangle.y, triangle.z
    first, second = vertices[b] - vertices[a], vertices[c] - vertices[a]
    normal = unreal.Vector(first.y * second.z - first.z * second.y,
                           first.z * second.x - first.x * second.z,
                           first.x * second.y - first.y * second.x)
    for index in (a, b, c):
        normals[index] += normal
for index, normal in enumerate(normals):
    length = math.sqrt(normal.x ** 2 + normal.y ** 2 + normal.z ** 2)
    normals[index] = normal / length if length > .000001 else unreal.Vector(0, 0, 1)

textures = {}
lock = json.loads((root / 'unreal/equipment-assets.lock.json').read_text())
for entry in lock['files']:
    source = root / entry['path']
    path = folder + '/' + source.stem
    if not lib.does_asset_exist(path):
        task = unreal.AssetImportTask()
        for key, value in {'filename': str(source), 'destination_path': folder, 'automated': True, 'save': True}.items():
            task.set_editor_property(key, value)
        tools.import_asset_tasks([task])
    texture = unreal.load_asset(path)
    texture.set_editor_property('srgb', False)
    kind = 'normal' if 'nor_dx' in source.name else 'roughness'
    if kind == 'normal':
        texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_NORMALMAP)
    lib.save_loaded_asset(texture)
    textures[kind] = texture

path = folder + '/M_Daypack'
material = unreal.load_asset(path) if lib.does_asset_exist(path) else tools.create_asset('M_Daypack', folder, unreal.Material, unreal.MaterialFactoryNew())
matlib.delete_all_material_expressions(material)
def node(kind, **properties):
    result = matlib.create_material_expression(material, kind)
    for key, value in properties.items():
        result.set_editor_property(key, value)
    return result
mask = node(unreal.MaterialExpressionVertexColor)
color = node(unreal.MaterialExpressionVectorParameter, parameter_name='Color', default_value=unreal.LinearColor(.8, .15, .025, 1))
dark = node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(.016, .021, .025, 1))
mix = node(unreal.MaterialExpressionLinearInterpolate)
matlib.connect_material_expressions(dark, '', mix, 'A')
matlib.connect_material_expressions(color, '', mix, 'B')
matlib.connect_material_expressions(mask, 'R', mix, 'Alpha')
matlib.connect_material_property(mix, '', unreal.MaterialProperty.MP_BASE_COLOR)
matlib.connect_material_property(mask, 'G', unreal.MaterialProperty.MP_METALLIC)
for kind, prop, sampler in [('normal', unreal.MaterialProperty.MP_NORMAL, unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL),
                            ('roughness', unreal.MaterialProperty.MP_ROUGHNESS, unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)]:
    sample = node(unreal.MaterialExpressionTextureSample, texture=textures[kind], sampler_type=sampler)
    matlib.connect_material_property(sample, 'RGB' if kind == 'normal' else 'R', prop)
matlib.recompile_material(material)
lib.save_loaded_asset(material)

path = folder + '/SM_Daypack'
# Keep outward vertex normals while using Unreal's front-face convention.
triangles = [unreal.IntVector(t.x, t.z, t.y) for t in triangles]
assert min(p.z for p in vertices) > 90 and max(p.z for p in vertices) < 165, 'Invalid backpack geometry'
mesh = unreal.DynamicMesh()
buffers = unreal.GeometryScriptSimpleMeshBuffers(vertices=vertices, normals=normals, uv0=uv, vertex_colors=colors, triangles=triangles)
unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(mesh, buffers)
options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(enable_recompute_tangents=True)
if lib.does_asset_exist(path):
    asset = unreal.load_asset(path)
    _, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_to_static_mesh(mesh, asset,
        unreal.GeometryScriptCopyMeshToAssetOptions(enable_recompute_tangents=True), unreal.GeometryScriptMeshWriteLOD())
else:
    asset, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(mesh, path, options)
if not asset:
    raise RuntimeError(str(outcome))
asset.set_editor_property('static_materials', [unreal.StaticMaterial(material_interface=material, material_slot_name='Fabric')])
lib.save_loaded_asset(asset)
(root / 'evidence/daypack.json').write_text(json.dumps({'mesh': path, 'geometry': 'original project work',
    'fabricLicense': 'CC0-1.0', 'vertices': len(vertices), 'triangles': len(triangles), 'fittingValidated': False,
    'bounds': str(asset.get_bounding_box()), 'visuallyAccepted': False}, indent=2) + '\n')
unreal.log('SNR_DAYPACK_AUTHORED')
