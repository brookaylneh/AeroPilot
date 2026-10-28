"""Set coherent daylight exposure for the actual server render path."""
import unreal
terrain = unreal.load_asset('/Game/Wilderness/SM_AlderTerrain')
ground = unreal.load_asset('/Game/Wilderness/Materials/M_AlderGround')
if not ground:
    raise RuntimeError('Ground material missing')
terrain.set_editor_property('static_materials', [unreal.StaticMaterial(material_interface=ground, material_slot_name='Ground')])
unreal.EditorAssetLibrary.save_loaded_asset(terrain)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
if not levels.load_level('/Game/Wilderness/AlderValley'):
    raise RuntimeError('Wilderness map is missing')
post = None
for actor in actors.get_all_level_actors():
    if actor.get_actor_label() == 'terrain.alder-valley':
        actor.static_mesh_component.set_material(0, ground)
    if actor.get_actor_label() == 'light.sun':
        actor.light_component.set_editor_property('intensity', 40000.0)
    if actor.get_actor_label() == 'look.daylight':
        post = actor
if post is None:
    post = actors.spawn_actor_from_class(unreal.PostProcessVolume, unreal.Vector())
    post.set_actor_label('look.daylight')
post.set_editor_property('unbound', True)
settings = post.get_editor_property('settings')
for name, value in {
    'override_auto_exposure_min_brightness': True,
    'override_auto_exposure_max_brightness': True,
    'auto_exposure_min_brightness': 13.0,
    'auto_exposure_max_brightness': 13.0,
    'override_auto_exposure_bias': True, 'auto_exposure_bias': 0.0,
    'override_motion_blur_amount': True, 'motion_blur_amount': 0.0,
    'override_bloom_intensity': True, 'bloom_intensity': 0.1,
}.items():
    settings.set_editor_property(name, value)
post.set_editor_property('settings', settings)
if not levels.save_current_level():
    raise RuntimeError('Could not save daylight settings')
unreal.log('SNR_WILDERNESS_DAYLIGHT_SAVED')
