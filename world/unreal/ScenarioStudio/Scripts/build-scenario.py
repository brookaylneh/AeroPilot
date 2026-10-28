"""Create runtime catalog/character wrappers and attach the authoring actors."""
import json
import math
from pathlib import Path
import unreal

root = Path(unreal.Paths.project_dir()).resolve().parents[2]
configuration = json.loads((root / 'world/unreal/character-runtime.json').read_text())
world = json.loads((root / 'evidence/wilderness-world.json').read_text())
lib = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
people = []
for preset in configuration['people']:
    folder = '/Game/Scenario/Runtime'
    name = 'BP_Person_' + preset['id'].replace('-', '_')
    path = folder + '/' + name
    factory = unreal.BlueprintFactory()
    factory.set_editor_property('parent_class', unreal.ScenarioHuman)
    bp = unreal.load_asset(path) if lib.does_asset_exist(path) else tools.create_asset(name, folder, unreal.Blueprint, factory)
    cls = lib.load_blueprint_class(path)
    default = unreal.get_default_object(cls)
    default.set_editor_property('character_class', lib.load_blueprint_class(preset['blueprint']))
    default.get_editor_property('character').set_editor_property('relative_rotation', unreal.Rotator(yaw=preset.get('characterYaw', 0)))
    default.set_editor_property('tint_bindings', [unreal.ScenarioTintBinding(
        appearance_key=b['key'], component_name=b['component'], material_slot=b['slot'], parameter_name=b['parameter'])
        for b in preset['tints']])
    default.set_editor_property('color_palette', {
        key: unreal.LinearColor(*value, 1) for key, value in configuration['palette'].items()})
    if preset.get('standingPose'):
        animation = unreal.load_asset(preset['standingPose'])
        if not animation:
            raise RuntimeError('Standing animation is missing')
        default.set_editor_property('standing_pose', animation)
    if preset.get('backpackMesh'):
        mesh = unreal.load_asset(preset['backpackMesh'])
        if not mesh:
            raise RuntimeError('Backpack mesh is missing')
        default.get_editor_property('backpack').set_static_mesh(mesh)
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    lib.save_loaded_asset(bp)
    cls = lib.load_blueprint_class(path)
    people.append(unreal.ScenarioPersonPreset(id=preset['id'], display_name=preset['name'], actor_class=cls,
                                              capsule_radius_cm=35, capsule_half_height_cm=95))

factory = unreal.DataAssetFactory()
factory.set_editor_property('data_asset_class', unreal.ScenarioCatalog)
path = '/Game/Scenario/Catalog'
catalog = unreal.load_asset(path) if lib.does_asset_exist(path) else tools.create_asset('Catalog', '/Game/Scenario', unreal.ScenarioCatalog, factory)
catalog.set_editor_property('environment_id', 'alder-valley-unreal')
catalog.set_editor_property('asset_version', world['assetVersion'])
regions = []
for r in world['regions']:
    target = (unreal.Vector(*r['min']) + unreal.Vector(*r['max'])) * .5
    target.z = 1300 if r['id'] == 'hillside' else 100
    pitch, yaw = math.radians(38), math.radians(-140)
    location = target + unreal.Vector(math.cos(yaw) * math.cos(pitch), math.sin(yaw) * math.cos(pitch), math.sin(pitch)) * 5500
    camera = unreal.Transform(location=location, rotation=unreal.MathLibrary.find_look_at_rotation(location, target))
    regions.append(unreal.ScenarioRegion(id=r['id'], display_name=r['name'],
        min=unreal.Vector(*r['min']), max=unreal.Vector(*r['max']), preview_camera=camera, maximum_slope_degrees=18))
catalog.set_editor_property('regions', regions)
catalog.set_editor_property('people', people)
catalog.set_editor_property('appearance_options', [unreal.ScenarioOptionGroup(key=key, allowed_values=values)
    for key, values in configuration['options'].items()])
lib.save_loaded_asset(catalog)
if not levels.load_level(world['world']):
    raise RuntimeError('Wilderness level unavailable')
for actor in actors.get_all_level_actors():
    if isinstance(actor, (unreal.ScenarioManager, unreal.ScenarioSession)):
        actors.destroy_actor(actor)
manager = actors.spawn_actor_from_class(unreal.ScenarioManager, unreal.Vector())
manager.set_actor_label('scenario.manager')
manager.set_editor_property('catalog', catalog)
session = actors.spawn_actor_from_class(unreal.ScenarioSession, unreal.Vector())
session.set_actor_label('scenario.session')
session.set_editor_property('manager', manager)
session.set_editor_property('marker_material', unreal.load_asset('/Game/Scenario/Equipment/M_PlacementMarker'))
if not levels.save_current_level():
    raise RuntimeError('Could not save scenario integration')
(root / 'evidence/native-catalog.json').write_text(json.dumps({
    'authored': True, 'runtimeTested': False, 'people': [p['id'] for p in configuration['people']],
    'world': world['world'], 'catalog': path}, indent=2) + '\n')
unreal.log('SNR_NATIVE_CATALOG_SAVED')
