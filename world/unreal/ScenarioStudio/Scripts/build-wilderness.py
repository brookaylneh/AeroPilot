"""Author the deterministic Alder Valley world from verified CC0 source assets."""
import json
import math
import random
from pathlib import Path
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
ASSETS = '/Game/Wilderness/Assets'
MAT = '/Game/Wilderness/Materials'
WORLD = '/Game/Wilderness/AlderValley'
SEED = 74021
assets = json.loads((ROOT / '.local/evidence/wilderness-import.json').read_text())
tools = unreal.AssetToolsHelpers.get_asset_tools()
lib = unreal.EditorAssetLibrary
matlib = unreal.MaterialEditingLibrary
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
level_sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
mesh_sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
rng = random.Random(SEED)
report = {'world': WORLD, 'assetVersion': 'alder-valley-unreal-0.2.0', 'seed': SEED,
          'coordinateSystem': 'UE_LH_Z_UP_CENTIMETERS', 'counts': {}, 'rendered': False}


def checkpoint():
    (ROOT / '.local/evidence/wilderness-world.json').write_text(json.dumps(report, indent=2) + '\n')


def material(name):
    path = f'{MAT}/{name}'
    if lib.does_asset_exist(path):
        result = unreal.load_asset(path)
        matlib.delete_all_material_expressions(result)
    else:
        result = tools.create_asset(name, MAT, unreal.Material, unreal.MaterialFactoryNew())
    return result


def node(material, kind, **values):
    expression = matlib.create_material_expression(material, kind)
    for key, value in values.items():
        expression.set_editor_property(key, value)
    return expression


def connect(source, destination, pin, output=''):
    if not matlib.connect_material_expressions(source, output, destination, pin):
        raise RuntimeError(f'Material connection failed: {pin}')


def sample(material, path, normal=False):
    texture_asset = unreal.load_asset(path)
    return node(material, unreal.MaterialExpressionTextureSample,
                texture=texture_asset,
                sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL if normal else
                unreal.MaterialSamplerType.SAMPLERTYPE_MASKS if texture_asset.get_editor_property('compression_settings') == unreal.TextureCompressionSettings.TC_MASKS else
                (unreal.MaterialSamplerType.SAMPLERTYPE_COLOR if '_diff_' in path else unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR))


def texture(asset, channel, prefix=None):
    candidates = assets['textures'][asset]
    selected = [p for n, p in candidates.items() if f'_{channel}_' in n and (not prefix or prefix in n)]
    if not selected:
        return None
    return selected[0]


def surface(asset, prefix=None, foliage=False):
    suffix = f'_{prefix}' if prefix else ''
    result = material(f'M_{asset}{suffix}')
    for channel, prop, normal in [('diff', unreal.MaterialProperty.MP_BASE_COLOR, False),
                                  ('nor', unreal.MaterialProperty.MP_NORMAL, True),
                                  ('rough', unreal.MaterialProperty.MP_ROUGHNESS, False)]:
        path = texture(asset, channel, prefix)
        if path:
            expression = sample(result, path, normal)
            matlib.connect_material_property(expression, 'RGB' if channel in ('diff', 'nor') else 'R', prop)
    if foliage:
        result.set_editor_property('two_sided', True)
        result.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
        result.set_editor_property('shading_model', unreal.MaterialShadingModel.MSM_TWO_SIDED_FOLIAGE)
        alpha = texture(asset, 'alpha', prefix)
        if alpha:
            matlib.connect_material_property(sample(result, alpha), 'R', unreal.MaterialProperty.MP_OPACITY_MASK)
        tint = node(result, unreal.MaterialExpressionMultiply, const_b=0.4)
        connect(sample(result, texture(asset, 'diff', prefix)), tint, 'A', 'RGB')
        matlib.connect_material_property(tint, '', unreal.MaterialProperty.MP_SUBSURFACE_COLOR)
    matlib.set_material_usage(result, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
    matlib.recompile_material(result)
    lib.save_loaded_asset(result)
    return result


surfaces = {}
for asset in assets['models']:
    prefixes = sorted({slot[len(asset) + 1:] for mesh in assets['models'][asset] for slot in mesh['slots'] if slot.startswith(asset + '_')}) or [None]
    for prefix in prefixes:
        if texture(asset, 'diff', prefix):
            surfaces[(asset, prefix)] = surface(asset, prefix, foliage=bool(texture(asset, 'alpha', prefix)))
    for entry in assets['models'][asset]:
        mesh = unreal.load_asset(entry['path'])
        for index, slot in enumerate(entry['slots']):
            choices = [(prefix, m) for (a, prefix), m in surfaces.items() if a == asset]
            chosen = next((m for prefix, m in choices if prefix and prefix in slot.lower()), choices[0][1])
            if mesh.get_material(index) != chosen:
                mesh.set_material(index, chosen)
        lib.save_loaded_asset(mesh)

ground_material = material('M_AlderGround')
color = node(ground_material, unreal.MaterialExpressionVertexColor)
for channel, prop, normal in [('diff', unreal.MaterialProperty.MP_BASE_COLOR, False),
                              ('nor', unreal.MaterialProperty.MP_NORMAL, True),
                              ('rough', unreal.MaterialProperty.MP_ROUGHNESS, False)]:
    grass = sample(ground_material, texture('aerial_grass_rock', channel), normal)
    forest = sample(ground_material, texture('forest_ground_04', channel), normal)
    rock = sample(ground_material, texture('rocky_terrain_02', channel), normal)
    mix = node(ground_material, unreal.MaterialExpressionLinearInterpolate)
    connect(grass, mix, 'A', 'RGB' if channel != 'rough' else 'R')
    connect(forest, mix, 'B', 'RGB' if channel != 'rough' else 'R')
    connect(color, mix, 'Alpha', 'R')
    final = node(ground_material, unreal.MaterialExpressionLinearInterpolate)
    connect(mix, final, 'A')
    connect(rock, final, 'B', 'RGB' if channel != 'rough' else 'R')
    connect(color, final, 'Alpha', 'G')
    matlib.connect_material_property(final, '', prop)
matlib.recompile_material(ground_material)
lib.save_loaded_asset(ground_material)


def trail_y(x):
    return 7 * math.sin(x * 0.045) + 2 * math.sin(x * 0.09)


def height(x, y):
    hill = 13 * math.exp(-((x - 65) / 40) ** 2 - ((y - 58) / 32) ** 2)
    distant = max(0, (max(abs(x), abs(y)) - 100) / 60) ** 2 * 22
    gentle = 1.2 * math.sin(x / 29) * math.cos(y / 32) + 0.6 * math.sin(y / 13 + x / 35)
    detail = 0.12 * math.sin(x * 0.81) * math.sin(y * 0.69) + 0.07 * math.sin(x * 1.71 + y * 0.4)
    return hill + gentle + detail + distant


mesh_path = '/Game/Wilderness/SM_AlderTerrain'
if lib.does_asset_exist(mesh_path):
    terrain = unreal.load_asset(mesh_path)
else:
    vertices, normals, uvs, colors, triangles = [], [], [], [], []
    count = 321
    for iy in range(count):
        y = iy - 160
        for ix in range(count):
            x = ix - 160
            vertices.append(unreal.Vector(x * 100, y * 100, height(x, y) * 100))
            dx = (height(x + 0.1, y) - height(x - 0.1, y)) / 0.2
            dy = (height(x, y + 0.1) - height(x, y - 0.1)) / 0.2
            length = math.sqrt(dx * dx + dy * dy + 1)
            normals.append(unreal.Vector(-dx / length, -dy / length, 1 / length))
            uvs.append(unreal.Vector2D(x / 4, y / 4))
            path = max(0, 1 - abs(y - trail_y(x)) / 3)
            forest = max(path, math.exp(-((x - 10) / 60) ** 2 - (y / 37) ** 2) * 0.88)
            rock = min(0.9, math.exp(-((x - 65) / 31) ** 2 - ((y - 58) / 29) ** 2) * 0.8 + max(0, math.hypot(dx, dy) - 0.38))
            colors.append(unreal.LinearColor(forest, rock, 0, 1))
            if ix < count - 1 and iy < count - 1:
                a = iy * count + ix
                triangles.extend([unreal.IntVector(a, a + count, a + 1), unreal.IntVector(a + 1, a + count, a + count + 1)])
    buffers = unreal.GeometryScriptSimpleMeshBuffers(vertices=vertices, normals=normals, uv0=uvs, vertex_colors=colors, triangles=triangles)
    dynamic = unreal.DynamicMesh()
    unreal.GeometryScript_MeshEdits.append_buffers_to_mesh(dynamic, buffers)
    options = unreal.GeometryScriptCreateNewStaticMeshAssetOptions(enable_recompute_tangents=True,
        enable_collision=True, collision_mode=unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
    terrain, outcome = unreal.GeometryScript_NewAssetUtils.create_new_static_mesh_asset_from_mesh(dynamic, mesh_path, options)
    if terrain is None:
        raise RuntimeError(f'Terrain creation failed: {outcome}')
if not terrain.get_material(0):
    terrain.set_editor_property('static_materials', [unreal.StaticMaterial(material_interface=ground_material, material_slot_name='Ground')])
else:
    terrain.set_material(0, ground_material)
lib.save_loaded_asset(terrain)

if lib.does_asset_exist(WORLD):
    previous_path = ROOT / '.local/evidence/wilderness-world.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    if previous.get('authored'):
        raise RuntimeError('Completed world already exists. Inspect it before regenerating.')
    if not level_sub.load_level(WORLD):
        raise RuntimeError('Could not recover incomplete generated world.')
    for existing in actor_sub.get_all_level_actors():
        if existing.get_class().get_name() not in ('WorldSettings', 'LevelScriptActor', 'Brush'):
            actor_sub.destroy_actor(existing)
elif not level_sub.new_level(WORLD):
    raise RuntimeError('Could not create wilderness level.')
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()


def spawn(cls, label, location=unreal.Vector(), rotation=unreal.Rotator()):
    actor = actor_sub.spawn_actor_from_class(cls, location, rotation)
    actor.set_actor_label(label)
    actor.set_editor_property('tags', [unreal.Name(label)])
    return actor


ground = spawn(unreal.StaticMeshActor, 'terrain.alder-valley')
ground.static_mesh_component.set_static_mesh(terrain)
ground.static_mesh_component.set_collision_profile_name('ScenarioTerrain')

# A sun/sky setup with restrained exposure; actual captured images determine final tuning.
sun = spawn(unreal.DirectionalLight, 'light.sun', rotation=unreal.Rotator(pitch=-38, yaw=-25))
sun.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
sun.light_component.set_editor_property('intensity', 10.0)
sun.light_component.set_editor_property('atmosphere_sun_light', True)
sun.light_component.set_editor_property('dynamic_shadow_distance_movable_light', 18000.0)
spawn(unreal.SkyAtmosphere, 'sky.atmosphere')
sky = spawn(unreal.SkyLight, 'light.sky')
sky.light_component.set_mobility(unreal.ComponentMobility.MOVABLE)
sky.light_component.set_editor_property('real_time_capture', True)
sky.light_component.set_editor_property('intensity', 1.0)
fog = spawn(unreal.ExponentialHeightFog, 'sky.haze')
fog.component.set_editor_property('fog_density', 0.001)

start = spawn(unreal.PlayerStart, 'camera.start', unreal.Vector(-2200, -400, height(-22, -4) * 100 + 260), unreal.Rotator(pitch=-4, yaw=8))


def foliage(asset, transforms, tree=False):
    choices = assets['models'][asset]
    # Prefer lower authored geometry when the source contains separate LOD meshes.
    lods = [m for m in choices if 'lod2' in m['path'].lower()]
    if not lods:
        lods = [m for m in choices if 'lod1' in m['path'].lower()]
    choices = lods or [m for m in choices if 'lod' not in m['path'].lower()] or choices
    groups = [[] for _ in choices]
    for index, transform in enumerate(transforms):
        groups[index % len(choices)].append(transform)
    for index, (entry, group) in enumerate(zip(choices, groups)):
        if not group:
            continue
        mesh = unreal.load_asset(entry['path'])
        name = f'FT_{asset}_{index}'
        folder = '/Game/Wilderness/Foliage'
        ft = unreal.load_asset(f'{folder}/{name}') if lib.does_asset_exist(f'{folder}/{name}') else tools.create_asset(name, folder, unreal.FoliageType_InstancedStaticMesh, unreal.FoliageType_InstancedStaticMeshFactory())
        ft.set_editor_property('mesh', mesh)
        ft.set_editor_property('cull_distance', unreal.Int32Interval(min=20000 if tree else 6500, max=30000 if tree else 9000))
        ft.set_editor_property('cast_shadow', True)
        lib.save_loaded_asset(ft)
        # FBX variants are laid out in a source grid. Remove that source offset
        # from each instance so its visible base rests at the chosen ground point.
        pivot = unreal.Vector((entry['min'][0] + entry['max'][0]) / 2,
                              (entry['min'][1] + entry['max'][1]) / 2, entry['min'][2])
        for item in group:
            origin = item.translation
            item.translation = origin * 2 - unreal.MathLibrary.transform_location(item, pivot)
        unreal.InstancedFoliageActor.add_instances(world, ft, group)
    report['counts'][asset] = len(transforms)


def transform(x, y, scale, yaw=None):
    return unreal.Transform(location=unreal.Vector(x * 100, y * 100, height(x, y) * 100 - 2),
                            rotation=unreal.Rotator(yaw=rng.uniform(0, 360) if yaw is None else yaw),
                            scale=unreal.Vector(scale, scale, scale))


trees = []
tree_locations = []
for _ in range(400):
    x, y = rng.uniform(-115, 115), rng.uniform(-105, 110)
    if abs(y - trail_y(x)) < 4.5 or ((x + 60) ** 2 + (y - 45) ** 2 < 35 ** 2) or (x > 35 and y > 25):
        continue
    if any(math.hypot(x - a, y - b) < 6 for a, b in tree_locations):
        continue
    tree_locations.append((x, y))
    trees.append(transform(x, y, rng.uniform(0.68, 1.06)))
foliage('fir_tree_01', trees, True)
# Separate trunk collision keeps foliage leaves traversable while rejecting a
# person's capsule inside the solid lower trunk. Actors have persistent tags.
trunk_mesh = unreal.load_asset('/Engine/BasicShapes/Cylinder')
for index, (x, y) in enumerate(tree_locations):
    trunk = spawn(unreal.StaticMeshActor, f'tree.trunk.{index:03d}', unreal.Vector(x * 100, y * 100, height(x, y) * 100 + 125))
    trunk.static_mesh_component.set_static_mesh(trunk_mesh)
    trunk.set_actor_scale3d(unreal.Vector(0.7, 0.7, 2.5))
    trunk.set_actor_hidden_in_game(True)
    trunk.static_mesh_component.set_cast_shadow(False)
    trunk.static_mesh_component.set_collision_profile_name('ScenarioObstacle')

for asset, total, scale_range in [('pine_sapling_small', 160, (0.7, 1.2)), ('fern_02', 750, (0.55, 1.0)), ('grass_medium_01', 6500, (0.45, 0.95))]:
    instances = []
    for _ in range(total):
        x, y = rng.uniform(-100, 100), rng.uniform(-85, 90)
        if abs(y - trail_y(x)) < 1.7 or (x > 40 and y > 30 and rng.random() < 0.8):
            continue
        instances.append(transform(x, y, rng.uniform(*scale_range)))
    foliage(asset, instances, asset == 'pine_sapling_small')

rocks = assets['models']['rock_moss_set_01']
for index in range(65):
    x, y = (rng.uniform(30, 90), rng.uniform(25, 90)) if index < 45 else (rng.uniform(-65, 65), rng.uniform(-35, 35))
    if abs(y - trail_y(x)) < 2.4:
        continue
    entry = rocks[index % len(rocks)]
    actor = spawn(unreal.StaticMeshActor, f'rock.{index:03d}', unreal.Vector(x * 100, y * 100, height(x, y) * 100 - 10), unreal.Rotator(yaw=rng.uniform(0, 360)))
    actor.static_mesh_component.set_static_mesh(unreal.load_asset(entry['path']))
    pivot = unreal.Vector((entry['min'][0] + entry['max'][0]) / 2,
                          (entry['min'][1] + entry['max'][1]) / 2,
                          entry['min'][2] + (entry['max'][2] - entry['min'][2]) * 0.15)
    actor.set_actor_location(actor.get_actor_location() * 2 - unreal.MathLibrary.transform_location(actor.get_actor_transform(), pivot), False, False)
    body = actor.static_mesh_component.static_mesh.get_editor_property('body_setup')
    body.set_editor_property('collision_trace_flag', unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
    lib.save_loaded_asset(actor.static_mesh_component.static_mesh)
    actor.static_mesh_component.set_collision_profile_name('ScenarioObstacle')
report['counts']['rocks'] = 65
report['regions'] = [
    {'id': 'meadow', 'name': 'Open meadow', 'min': [-9000, 1500, -10000], 'max': [-2500, 8000, 10000]},
    {'id': 'forest', 'name': 'Forest trail', 'min': [-3500, -4000, -10000], 'max': [3500, 2200, 10000]},
    {'id': 'hillside', 'name': 'Rocky hillside', 'min': [3500, 2500, -10000], 'max': [9000, 9000, 10000]}]
if not level_sub.save_current_level():
    raise RuntimeError('Could not save the wilderness map.')
report['authored'] = True
checkpoint()
unreal.log('SNR_WILDERNESS_WORLD_SAVED')
