"""Author daylight clouds and participating haze for a measured atmosphere variant."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import unreal

output = Path(sys.argv[1])
output.mkdir()
root = Path(unreal.Paths.project_dir()).resolve()
level_file = root / "Content/OpenMeadow/OpenMeadow.umap"
shutil.copy2(level_file, output / "OpenMeadow-before.umap")
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
scene = {actor.get_actor_label(): actor for actor in actors.get_all_level_actors()}
assert not any(isinstance(a, unreal.VolumetricCloud) for a in scene.values())
cloud = actors.spawn_actor_from_class(unreal.VolumetricCloud, unreal.Vector(0, 0, 0))
assert cloud
cloud.set_actor_label("sky.clouds")
component = cloud.get_component_by_class(unreal.VolumetricCloudComponent)
assert component
material = unreal.load_asset("/Engine/EngineSky/VolumetricClouds/m_SimpleVolumetricCloud_Inst")
assert material
component.set_editor_property("material", material)
component.set_editor_property("layer_bottom_altitude", 2.0)
component.set_editor_property("layer_height", 3.0)
sun = scene["light.sun"].light_component
sun.set_editor_property("cast_cloud_shadows", True)
sun.set_editor_property("cloud_shadow_strength", 0.55)
sun.set_editor_property("cloud_shadow_extent", 2.0)
sun.set_editor_property("cloud_shadow_map_resolution_scale", 2.0)
scene["light.sky"].light_component.set_editor_property("real_time_capture", True)
fog = scene["sky.haze"].component
fog.set_editor_property("enable_volumetric_fog", True)
fog.set_editor_property("volumetric_fog_distance", 160000.0)
fog.set_editor_property("volumetric_fog_scattering_distribution", 0.2)
assert levels.save_current_level()
rotation = scene["light.sun"].get_actor_rotation()
report = {
    "cloudMaterial": material.get_path_name(),
    "cloudBaseKm": 2.0,
    "cloudHeightKm": 3.0,
    "cloudShadowExtentKm": 2.0,
    "cloudShadowStrength": 0.55,
    "cloudShadowResolutionScale": 2.0,
    "sunRotationDegrees": dict(pitch=rotation.pitch, yaw=rotation.yaw, roll=rotation.roll),
    "sunIlluminanceLux": sun.get_editor_property("intensity"),
    "fogDensity": fog.get_editor_property("fog_density"),
    "volumetricFogDistanceCm": 160000.0,
    "mapBeforeSha256": hashlib.sha256((output / "OpenMeadow-before.umap").read_bytes()).hexdigest(),
    "mapAfterSha256": hashlib.sha256(level_file.read_bytes()).hexdigest(),
}
(output / "result.json").write_text(json.dumps(report, indent=2) + "\n")
unreal.log("SNR_MEADOW_ATMOSPHERE_AUTHORED")
