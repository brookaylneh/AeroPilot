"""Author the original one-mile open landscape with one runtime location.

The requested Fab packs are not bundled. Temporary grass is the existing CC0
Poly Haven asset; the LandscapeGrassType can be replaced after acquisition.
"""
import json
import math
from pathlib import Path
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
report_path = ROOT / 'evidence/open-meadow-terrain.json'
report = json.loads(report_path.read_text())
WIDTH_CM = report['widthMetres'] * 100
source = json.loads((ROOT / 'evidence/wilderness-import.json').read_text())
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
ml = unreal.MaterialEditingLibrary
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
FOLDER = '/Game/OpenMeadow'
WORLD = FOLDER + '/OpenMeadow'
if report.get('authored') and lib.does_asset_exist(WORLD):
    raise RuntimeError('Completed map already exists; back it up and use a targeted editor update before changing assets.')

def node(mat, cls, **props):
    result = ml.create_material_expression(mat, cls)
    for k, v in props.items():
        result.set_editor_property(k, v)
    return result

def connect(a, b, pin, output=''):
    if not ml.connect_material_expressions(a, output, b, pin):
        raise RuntimeError('Invalid material connection: ' + pin)

def sample(mat, path, uv, normal=False, mask=False):
    result = node(mat, unreal.MaterialExpressionTextureSample, texture=unreal.load_asset(path),
        sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL if normal else
            unreal.MaterialSamplerType.SAMPLERTYPE_MASKS if mask else
            unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if '_diff_' in path else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
    connect(uv, result, 'UVs')
    return result

def terrain_texture(asset, channel):
    return next(path for name, path in source['textures'][asset].items() if '_' + channel + '_' in name)

def material(name):
    path = FOLDER + '/' + name
    result = unreal.load_asset(path) if lib.does_asset_exist(path) else tools.create_asset(name, FOLDER, unreal.Material, unreal.MaterialFactoryNew())
    ml.delete_all_material_expressions(result)
    return result

# A small set of rebased meshes: original FBX variants have layout offsets.
grass_meshes = []
for i, entry in enumerate(source['models']['grass_medium_01'][:3]):
    path = f'{FOLDER}/SM_Grass_{i}'
    mesh = unreal.load_asset(path) if lib.does_asset_exist(path) else None
    if not mesh:
        original = unreal.load_asset(entry['path'])
        dynamic, outcome = unreal.GeometryScript_AssetUtils.copy_mesh_from_static_mesh_v2(original, unreal.DynamicMesh(),
            unreal.GeometryScriptCopyMeshFromAssetOptions(),
            unreal.GeometryScriptMeshReadLOD(lod_type=unreal.GeometryScriptLODType.SOURCE_MODEL, lod_index=0), use_section_materials=False)
        center = unreal.Vector(-(entry['min'][0] + entry['max'][0]) / 2,
            -(entry['min'][1] + entry['max'][1]) / 2, -entry['min'][2] - 1)
        unreal.GeometryScript_MeshTransforms.translate_mesh(dynamic, center)
        mesh, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(dynamic, path,
            unreal.GeometryScriptCreateNewStaticMeshAssetOptions(enable_collision=False))
        if not mesh:
            raise RuntimeError(str(outcome))
        mesh.set_editor_property('static_materials', list(original.get_editor_property('static_materials')))
        lib.save_loaded_asset(mesh)
    grass_meshes.append(mesh)

grass_path = FOLDER + '/GT_Meadow'
grass_type = unreal.load_asset(grass_path) if lib.does_asset_exist(grass_path) else tools.create_asset(
    'GT_Meadow', FOLDER, unreal.LandscapeGrassType, unreal.LandscapeGrassTypeFactory())
varieties = []
for mesh in grass_meshes:
    variety = unreal.GrassVariety()
    for key, value in dict(grass_mesh=mesh, grass_density=unreal.PerPlatformFloat(default=120),
            start_cull_distance=unreal.PerPlatformInt(default=3500), end_cull_distance=unreal.PerPlatformInt(default=6500),
            scale_x=unreal.FloatInterval(min=.9, max=1.65), random_rotation=True, align_to_surface=True,
            cast_dynamic_shadow=True).items():
        variety.set_editor_property(key, value)
    varieties.append(variety)
grass_type.set_editor_property('grass_varieties', varieties)
lib.save_loaded_asset(grass_type)

mask_path = FOLDER + '/ground_mask_020'
if not lib.does_asset_exist(mask_path):
    task = unreal.AssetImportTask()
    task.filename = str(ROOT / 'vendor/open-meadow/ground-mask.png')
    task.destination_path = FOLDER
    task.destination_name = 'ground_mask_020'
    task.automated = True
    task.save = True
    tools.import_asset_tasks([task])
    mask_path = task.imported_object_paths[0]
else:
    mask_path = FOLDER + '/ground_mask_020'
mask_tex = unreal.load_asset(mask_path)
mask_tex.set_editor_property('srgb', False)
mask_tex.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_MASKS)
lib.save_loaded_asset(mask_tex)

mat = material('M_Meadow')
position = node(mat, unreal.MaterialExpressionWorldPosition)
xy = node(mat, unreal.MaterialExpressionComponentMask, r=True, g=True)
connect(position, xy, '')
uv = node(mat, unreal.MaterialExpressionMultiply, const_b=1 / 450)
connect(xy, uv, 'A')
mask_scale = node(mat, unreal.MaterialExpressionMultiply, const_b=1 / WIDTH_CM)
connect(xy, mask_scale, 'A')
mask_uv = node(mat, unreal.MaterialExpressionAdd, const_b=.5)
connect(mask_scale, mask_uv, 'A')
mask = sample(mat, mask_path, mask_uv, mask=True)
depth = node(mat, unreal.MaterialExpressionPixelDepth)
distance_scale = node(mat, unreal.MaterialExpressionMultiply, const_b=1 / 13000)
connect(depth, distance_scale, 'A')
distance_offset = node(mat, unreal.MaterialExpressionAdd, const_b=-2000 / 13000)
connect(distance_scale, distance_offset, 'A')
distance_fade = node(mat, unreal.MaterialExpressionSaturate)
connect(distance_offset, distance_fade, '')

for channel, prop in [('diff', unreal.MaterialProperty.MP_BASE_COLOR), ('nor', unreal.MaterialProperty.MP_NORMAL), ('rough', unreal.MaterialProperty.MP_ROUGHNESS)]:
    grass = sample(mat, terrain_texture('aerial_grass_rock', channel), uv, normal=channel == 'nor')
    soil = sample(mat, terrain_texture('forest_ground_04', channel), uv, normal=channel == 'nor')
    mix = node(mat, unreal.MaterialExpressionLinearInterpolate)
    connect(grass, mix, 'A', 'R' if channel == 'rough' else 'RGB')
    connect(soil, mix, 'B', 'R' if channel == 'rough' else 'RGB')
    connect(mask, mix, 'Alpha', 'R')
    if channel == 'diff':
        tint = node(mat, unreal.MaterialExpressionLinearInterpolate)
        connect(node(mat, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(.76, .85, .65, 1)), tint, 'A')
        connect(node(mat, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(1.12, 1.08, .83, 1)), tint, 'B')
        connect(mask, tint, 'Alpha', 'G')
        colored = node(mat, unreal.MaterialExpressionMultiply)
        connect(mix, colored, 'A'); connect(tint, colored, 'B')
        mix = colored
        distant_grass = node(mat, unreal.MaterialExpressionLinearInterpolate)
        connect(node(mat, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(.105, .135, .045, 1)), distant_grass, 'A')
        connect(node(mat, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(.20, .21, .075, 1)), distant_grass, 'B')
        connect(mask, distant_grass, 'Alpha', 'G')
        distant = node(mat, unreal.MaterialExpressionLinearInterpolate)
        connect(distant_grass, distant, 'A')
        connect(node(mat, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(.18, .135, .075, 1)), distant, 'B')
        connect(mask, distant, 'Alpha', 'R')
    elif channel == 'nor':
        distant = node(mat, unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0, 0, 1, 1))
    else:
        distant = node(mat, unreal.MaterialExpressionConstant, r=.92)
    final = node(mat, unreal.MaterialExpressionLinearInterpolate)
    connect(mix, final, 'A'); connect(distant, final, 'B'); connect(distance_fade, final, 'Alpha')
    mix = final
    ml.connect_material_property(mix, '', prop)
grass_output = node(mat, unreal.MaterialExpressionLandscapeGrassOutput)
grass_input = unreal.GrassInput()
grass_input.set_editor_property('name', 'Meadow')
grass_input.set_editor_property('grass_type', grass_type)
grass_output.set_editor_property('grass_types', [grass_input])
connect(mask, grass_output, 'Meadow', 'B')
ml.recompile_material(mat)
lib.save_loaded_asset(mat)

if lib.does_asset_exist(WORLD):
    if report.get('authored'):
        raise RuntimeError('Completed map already exists; inspect before regenerating.')
    if not levels.load_level(WORLD):
        raise RuntimeError('Cannot load incomplete map')
    for actor in actors.get_all_level_actors():
        if actor.get_class().get_name() not in ('WorldSettings', 'LevelScriptActor', 'Brush'):
            actors.destroy_actor(actor)
elif not levels.new_level(WORLD):
    raise RuntimeError('Cannot create map')

landscape = unreal.ScenarioLandscapeTools.create_landscape(str(ROOT / 'vendor/open-meadow/height.r16'), 2017, WIDTH_CM, mat)
if not landscape:
    raise RuntimeError('Landscape import failed')

# Scenery beyond the editable boundary prevents a floating square horizon.
# It has no collision and cannot host scenario placements.
def backdrop_height(x, y):
    x, y = x * 2, y * 2
    return (8 + 3 * math.sin(x / 470) * math.cos(y / 590)
        + 1.3 * math.sin(y / 95 + x / 180)
        + 24 * math.exp(-((x + 1100) / 500)**2 - ((y - 700) / 650)**2)
        + 36 * math.exp(-((x - 1200) / 650)**2 - ((y - 600) / 450)**2)
        + 26 * math.exp(-((x - 600) / 550)**2 - ((y + 1400) / 600)**2)
        + .06 * math.sin(x / 6) * math.sin(y / 7))

backdrop_path = FOLDER + '/SM_Horizon_020'
backdrop = unreal.load_asset(backdrop_path) if lib.does_asset_exist(backdrop_path) else None
if not backdrop:
    vertices, normals, triangles, uvs = [], [], [], []
    segments = 256
    ring_size = segments * 4
    for ri, radius in enumerate([report['widthMetres'] / 2 - 1, 1100, 1600, 2500, 5000, 10000]):
        for edge in range(4):
            for step in range(segments):
                t = -radius + 2 * radius * step / segments
                x, y = [(t, -radius), (radius, t), (-t, radius), (-radius, -t)][edge]
                z = backdrop_height(x, y) - (.3 if ri == 0 else 0)
                vertices.append(unreal.Vector(x * 100, y * 100, z * 100))
                normals.append(unreal.Vector(0, 0, 1))
                uvs.append(unreal.Vector2D(x / 4.5, y / 4.5))
        if ri:
            for step in range(ring_size):
                a, b = (ri - 1) * ring_size + step, (ri - 1) * ring_size + (step + 1) % ring_size
                c, d = ri * ring_size + step, ri * ring_size + (step + 1) % ring_size
                triangles.extend([unreal.IntVector(a, c, b), unreal.IntVector(b, c, d)])
    dynamic = unreal.DynamicMesh()
    unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dynamic, unreal.GeometryScriptSimpleMeshBuffers(
        vertices=vertices, normals=normals, uv0=uvs, triangles=triangles))
    backdrop, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(dynamic, backdrop_path,
        unreal.GeometryScriptCreateNewStaticMeshAssetOptions(enable_collision=False, enable_recompute_tangents=True))
    if not backdrop:
        raise RuntimeError(str(outcome))
    backdrop.set_editor_property('static_materials', [unreal.StaticMaterial(material_interface=mat, material_slot_name='Ground')])
    lib.save_loaded_asset(backdrop)
background = actors.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector())
background.set_actor_label('scenery.non-placeable-horizon')
background.static_mesh_component.set_static_mesh(backdrop)
background.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)

def spawn(cls, name, location=unreal.Vector(), rotation=unreal.Rotator()):
    actor = actors.spawn_actor_from_class(cls, location, rotation)
    actor.set_actor_label(name)
    actor.set_editor_property('tags', [unreal.Name(name)])
    return actor

sun = spawn(unreal.DirectionalLight, 'light.sun', rotation=unreal.Rotator(pitch=-43, yaw=-28))
sun.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
sun.light_component.set_editor_property('intensity', 50000)
sun.light_component.set_editor_property('atmosphere_sun_light', True)
sun.light_component.set_editor_property('dynamic_shadow_distance_movable_light', 18000)
spawn(unreal.SkyAtmosphere, 'sky.atmosphere')
sky = spawn(unreal.SkyLight, 'light.sky')
sky.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
sky.light_component.set_editor_property('real_time_capture', True)
sky.light_component.set_editor_property('intensity', 1.8)
fog = spawn(unreal.ExponentialHeightFog, 'sky.haze')
fog.component.set_editor_property('fog_density', .0002)
post = spawn(unreal.PostProcessVolume, 'look.daylight')
post.set_editor_property('unbound', True)
settings = post.get_editor_property('settings')
for key, value in dict(override_auto_exposure_min_brightness=True, override_auto_exposure_max_brightness=True,
        auto_exposure_min_brightness=13., auto_exposure_max_brightness=13., override_auto_exposure_bias=True,
        auto_exposure_bias=0., override_motion_blur_amount=True, motion_blur_amount=0., override_bloom_intensity=True, bloom_intensity=.1).items():
    settings.set_editor_property(key, value)
post.set_editor_property('settings', settings)
spawn(unreal.PlayerStart, 'camera.start', unreal.Vector(0, 0, 4000))

catalog_path = FOLDER + '/Catalog'
catalog = unreal.load_asset(catalog_path) if lib.does_asset_exist(catalog_path) else lib.duplicate_asset('/Game/Scenario/Catalog', catalog_path)
catalog.set_editor_property('environment_id', report['environmentId'])
catalog.set_editor_property('display_name', 'Open Meadow')
catalog.set_editor_property('asset_version', report['assetVersion'])
target = unreal.Vector(0, 0, 1500)
pitch, yaw, distance = math.radians(20), math.radians(-135), 24000
location = target + unreal.Vector(math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)) * distance
catalog.set_editor_property('regions', [unreal.ScenarioRegion(id='open-meadow', display_name='Open Meadow · 1 × 1 mile',
    min=unreal.Vector(*report['boundsCm']['min']), max=unreal.Vector(*report['boundsCm']['max']), maximum_slope_degrees=18,
    use_preview_camera=True, preview_target_cm=target,
    preview_camera=unreal.Transform(location=location, rotation=unreal.MathLibrary.find_look_at_rotation(location, target)))])
lib.save_loaded_asset(catalog)
manager = spawn(unreal.ScenarioManager, 'scenario.manager')
manager.set_editor_property('catalog', catalog)
session = spawn(unreal.ScenarioSession, 'scenario.session')
session.set_editor_property('manager', manager)
session.set_editor_property('marker_material', unreal.load_asset('/Game/Scenario/Equipment/M_PlacementMarker'))
if not levels.save_current_level():
    raise RuntimeError('Map save failed')
origin, extent = landscape.get_actor_bounds(False)
assert abs(extent.x * 2 - WIDTH_CM) < .1 and abs(extent.y * 2 - WIDTH_CM) < .1, 'Native landscape extent mismatch'
components = len(landscape.get_components_by_class(unreal.LandscapeComponent))
assert components == 256
report['nativeExtentCm'] = [extent.x * 2, extent.y * 2, extent.z * 2]
report.update(authored=True, catalog=catalog_path, landscapeComponents=components,
    grassSource='Poly Haven grass_medium_01 (CC0), temporary pending requested Fab packs',
    runtimeGrassGeneration=True, treeCount=0)
report_path.write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_OPEN_MEADOW_AUTHORED')
