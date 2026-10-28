"""Apply actual Project Nature grass and flowers to the existing one-mile map.

Original pack materials and meshes are preserved. Runtime grass references
curated meshes and local material instances, including all Pivot Painter inputs.
"""
import datetime
import hashlib
import json
from pathlib import Path
import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
FOLDER = '/Game/OpenMeadow/Vegetation030'
VERSION = 'open-meadow-unreal-0.3.0'
lib = unreal.EditorAssetLibrary
ml = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
inventory = json.loads((ROOT / '.local/evidence/fab-vegetation-inventory.json').read_text())
known = {m['path'].split('/')[-1].split('.')[0]: m for p in inventory['packs'] for m in p['meshes']}
report = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'assetVersion': VERSION,
          'groups': [], 'materials': [], 'terrainUnchanged': False, 'packagedTested': False,
          'visuallyAccepted': False, 'treeCount': 0}
assert levels.load_level('/Game/OpenMeadow/OpenMeadow')
lib.make_directory(FOLDER)

def duplicate(source, destination):
    if lib.does_asset_exist(destination):
        return unreal.load_asset(destination)
    result = lib.duplicate_asset(source, destination)
    assert result, destination
    return result

def node(material, cls, **props):
    expression = ml.create_material_expression(material, cls)
    for key, value in props.items():
        expression.set_editor_property(key, value)
    return expression

def connect(source, target, pin, output=''):
    assert ml.connect_material_expressions(source, output, target, pin), pin

master_cache = {}
instance_cache = {}
def runtime_material(original):
    source_path = original.get_path_name()
    if source_path in instance_cache:
        return instance_cache[source_path]
    assert isinstance(original, unreal.MaterialInstanceConstant), source_path
    parent = original.get_editor_property('parent')
    assert isinstance(parent, unreal.Material), 'Preserve unfamiliar parent chain: ' + source_path
    if parent.get_path_name() not in master_cache:
        master_path = FOLDER + '/M_' + parent.get_name() + '_Fade'
        fresh = not lib.does_asset_exist(master_path)
        master = duplicate(parent.get_path_name(), master_path)
        if fresh:
            existing_fade = any(isinstance(e, unreal.MaterialExpressionPerInstanceFadeAmount)
                                for e in ml.get_material_expressions(master))
            if not existing_fade:
                opacity = ml.get_material_property_input_node(master, unreal.MaterialProperty.MP_OPACITY_MASK)
                output = ml.get_material_property_input_node_output_name(master, unreal.MaterialProperty.MP_OPACITY_MASK)
                fade = node(master, unreal.MaterialExpressionPerInstanceFadeAmount)
                dither = node(master, unreal.MaterialExpressionMaterialFunctionCall,
                    material_function=unreal.load_asset('/Engine/Functions/Engine_MaterialFunctions02/Utility/DitherTemporalAA'))
                connect(fade, dither, 'Alpha Threshold')
                if opacity:
                    multiplied = node(master, unreal.MaterialExpressionMultiply)
                    connect(opacity, multiplied, 'A', output)
                    connect(dither, multiplied, 'B')
                    dither = multiplied
                master.set_editor_property('blend_mode', unreal.BlendMode.BLEND_MASKED)
                assert ml.connect_material_property(dither, '', unreal.MaterialProperty.MP_OPACITY_MASK)
            ml.set_material_usage(master, unreal.MaterialUsage.MATUSAGE_INSTANCED_STATIC_MESHES)
            ml.recompile_material(master)
            lib.save_loaded_asset(master)
        master_cache[parent.get_path_name()] = master
    result = duplicate(source_path, FOLDER + '/MI_' + original.get_name())
    ml.set_material_instance_parent(result, master_cache[parent.get_path_name()])
    names = {str(n) for n in ml.get_scalar_parameter_names(result)}
    # Disable the pack's player-interaction effects; this phase has stationary
    # authoring actors, with no interactive-vegetation controller.
    for name in ['Trail Strength', 'Bending Strength 1', 'Bending Strength 2', 'Bending Strength 3', 'Player Wobble Strength']:
        if name in names:
            ml.set_material_instance_scalar_parameter_value(result, name, 0.)
    ml.update_material_instance(result)
    lib.save_loaded_asset(result)
    instance_cache[source_path] = result
    report['materials'].append({'source': source_path, 'runtime': result.get_path_name(),
        'textures': [t.get_path_name() for t in ml.get_material_used_textures(result)]})
    return result

# Densities are engine values per 10 m by 10 m square (LandscapeGrass.cpp).
# Keep natural centimetre scale; no tree assets or forest scenery is referenced.
groups = [
    ('LowMeadow', 'A', 7000, 11000, [
        ('openWorld_grass_01_01_01', 650, .55, .85),
        ('openWorld_grass_02_01_01', 450, .60, .90)]),
    ('DetailedGrass', 'A', 4000, 8000, [
        ('lowGrass_01_01_SM', 320, .85, 1.15),
        ('lowGrass_02_01_SM', 180, .65, 1.00)]),
    ('GrassPatches', 'B', 5000, 9500, [
        ('lowGrass_04_01_SM', 180, .70, 1.00)]),
    ('FlowerPatchesA', 'R', 4500, 8000, [
        ('flower_01_02', 180, .85, 1.15),
        ('flower_07_02', 140, .85, 1.15)]),
    ('FlowerPatchesB', 'G', 4500, 8000, [
        ('flower_08_02', 100, .85, 1.15),
        ('flower_11_02', 140, .90, 1.20)])
]
grass_types = []
for name, channel, near, far, selections in groups:
    path = FOLDER + '/GT_' + name
    grass_type = unreal.load_asset(path) if lib.does_asset_exist(path) else tools.create_asset(
        'GT_' + name, FOLDER, unreal.LandscapeGrassType, unreal.LandscapeGrassTypeFactory())
    varieties = []
    selected = []
    for mesh_name, density, lo, hi in selections:
        metadata = known[mesh_name]
        mesh = unreal.load_asset(metadata['path'])
        assert mesh and metadata['lods'] >= 3
        overrides = [runtime_material(slot.material_interface) for slot in mesh.get_editor_property('static_materials')]
        variety = unreal.GrassVariety()
        for key, value in dict(grass_mesh=mesh, override_materials=overrides,
                grass_density=unreal.PerPlatformFloat(default=density),
                start_cull_distance=unreal.PerPlatformInt(default=near),
                end_cull_distance=unreal.PerPlatformInt(default=far),
                scale_x=unreal.FloatInterval(min=lo, max=hi),
                use_grid=False, random_rotation=True, align_to_surface=True,
                cast_dynamic_shadow=True, cast_contact_shadow=True,
                instance_world_position_offset_disable_distance=4500).items():
            variety.set_editor_property(key, value)
        varieties.append(variety)
        selected.append({'mesh': metadata['path'], 'density': density, 'scale': [lo, hi], 'lods': metadata['lods']})
    grass_type.set_editor_property('grass_varieties', varieties)
    lib.save_loaded_asset(grass_type)
    grass_types.append((name, channel, grass_type))
    report['groups'].append({'name': name, 'maskChannel': channel, 'cullDistanceCm': [near, far], 'varieties': selected})

mask_path = FOLDER + '/T_VegetationMask'
if not lib.does_asset_exist(mask_path):
    task = unreal.AssetImportTask()
    task.filename = str(ROOT / '.local/vendor/open-meadow/vegetation-mask-030.png')
    task.destination_path = FOLDER
    task.destination_name = 'T_VegetationMask'
    task.automated = True
    task.save = True
    tools.import_asset_tasks([task])
mask_texture = unreal.load_asset(mask_path)
assert mask_texture
mask_texture.set_editor_property('srgb', False)
mask_texture.set_editor_property('compression_settings', unreal.TextureCompressionSettings.TC_MASKS)
lib.save_loaded_asset(mask_texture)

ground_path = FOLDER + '/M_Meadow030'
ground = duplicate('/Game/OpenMeadow/M_Meadow', ground_path)
expressions = ml.get_material_expressions(ground)
for expression in expressions:
    if isinstance(expression, unreal.MaterialExpressionLandscapeGrassOutput):
        ml.delete_material_expression(ground, expression)
ground_mask = next(e for e in expressions if isinstance(e, unreal.MaterialExpressionTextureSample)
                   and e.get_editor_property('texture').get_name() == 'ground_mask_020')
mask_uv = ml.get_inputs_for_material_expression(ground, ground_mask)[0]
mask = node(ground, unreal.MaterialExpressionTextureSample, texture=mask_texture,
            sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
connect(mask_uv, mask, 'UVs')
output = node(ground, unreal.MaterialExpressionLandscapeGrassOutput)
inputs = []
for name, channel, grass_type in grass_types:
    entry = unreal.GrassInput()
    entry.set_editor_property('name', name)
    entry.set_editor_property('grass_type', grass_type)
    inputs.append(entry)
output.set_editor_property('grass_types', inputs)
for name, channel, grass_type in grass_types:
    connect(mask, output, name, channel)
ml.recompile_material(ground)
lib.save_loaded_asset(ground)
landscape = next(a for a in actors.get_all_level_actors() if a.get_actor_label() == 'terrain.open-meadow')
landscape.set_editor_property('landscape_material', ground)
catalog = unreal.load_asset('/Game/OpenMeadow/Catalog')
catalog.set_editor_property('asset_version', VERSION)
lib.save_loaded_asset(catalog)
assert levels.save_current_level()

terrain_path = ROOT / '.local/evidence/open-meadow-terrain.json'
terrain = json.loads(terrain_path.read_text())
assert hashlib.sha256((ROOT / '.local/vendor/open-meadow/height.r16').read_bytes()).hexdigest() == terrain['heightmapSha256']
report['terrainUnchanged'] = True
report['heightmapSha256'] = terrain['heightmapSha256']
report['integrated'] = True
terrain.update(assetVersion=VERSION, requestedVegetationImported=True,
    vegetationStatus='All three requested Project Nature packs integrated into source map. Packaged and visual checks pending.',
    grassSource='Project Nature Open World Foliage, Grass Library and Meadow Flowers (Fab Standard License)',
    packagedTested=False, publicBrowserTested=False, visuallyReviewed=False, visuallyAccepted=False)
terrain_path.write_text(json.dumps(terrain, indent=2) + '\n')
(ROOT / '.local/evidence/fab-vegetation-integration.json').write_text(json.dumps(report, indent=2) + '\n')
unreal.log('SNR_FAB_VEGETATION_INTEGRATED')
