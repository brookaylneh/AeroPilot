"""Apply world/unreal/valley-lighting.json to OpenMeadow: assert the recorded sun, fix manual exposure
on look.daylight and set the sky.haze height fog; the replaced values go to
$SNR_EVIDENCE_DIR/lighting.json."""

import json
import math
import os
from pathlib import Path

import unreal

ROOT = Path(unreal.Paths.project_dir()).resolve().parents[2]
# Koschmieder: visibility is where contrast falls to 2 %, extinction = ln(50) / visibility.
KOSCHMIEDER = math.log(50)

evidence = Path(os.environ["SNR_EVIDENCE_DIR"])
report_path = evidence / "lighting.json"
assert not report_path.exists(), report_path
lighting = json.loads((ROOT / "world/unreal/valley-lighting.json").read_text())
sun_data = json.loads((ROOT / "world/cosys/world.json").read_text())["sun"]

actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
level_actors = actors.get_all_level_actors()


def labelled(label):
    (actor,) = [a for a in level_actors if a.get_actor_label() == label]
    return actor


(sun,) = [a for a in level_actors if isinstance(a, unreal.DirectionalLight)]
# build-open-meadow.py owns the sun rotation; this only proves it is the recorded one.
toward_sun = -sun.get_actor_forward_vector()
assert abs(math.degrees(math.asin(toward_sun.z)) - sun_data["apparentElevationDegrees"]) < 1e-3
azimuth = math.degrees(math.atan2(toward_sun.y, toward_sun.x)) % 360
assert abs(azimuth - sun_data["azimuthDegreesClockwiseFromNorth"]) < 1e-3

look = labelled("look.daylight")
assert isinstance(look, unreal.PostProcessVolume) and look.get_editor_property("unbound")
settings = look.get_editor_property("settings")
previous_exposure = {
    key: str(settings.get_editor_property(key))
    for key in ("auto_exposure_method", "auto_exposure_bias", "auto_exposure_min_brightness")
}
# Manual exposure without the physical camera meters at EV100 0; the bias sets the EV, as in
# world/cosys/configure-drones.py for the drone captures.
for key, value in {
    "auto_exposure_method": unreal.AutoExposureMethod.AEM_MANUAL,
    "auto_exposure_apply_physical_camera_exposure": False,
    "auto_exposure_bias": -lighting["exposureEv100"],
}.items():
    settings.set_editor_property(f"override_{key}", True)
    settings.set_editor_property(key, value)
look.set_editor_property("settings", settings)

haze = labelled("sky.haze")
fog = haze.get_component_by_class(unreal.ExponentialHeightFogComponent)
previous_fog = {
    key: fog.get_editor_property(key)
    for key in ("fog_density", "fog_height_falloff", "enable_volumetric_fog")
}
previous_fog["heightM"] = haze.get_actor_location().z / 100
# The renderer takes FogDensity and FogHeightFalloff in 1/1000 per cm (Renderer SceneCore.cpp:404-405),
# so FogDensity is the extinction per 10 m and the density halves every 1000 / FogHeightFalloff cm
# (exp2 in HeightFogCommon.ush:64).
density = KOSCHMIEDER / lighting["haze"]["visibilityKm"] / 100
falloff = 1000 / (lighting["haze"]["halfHeightM"] * 100)
assert 0 < density < 0.05 and 0.001 <= falloff <= 2, (density, falloff)
fog.set_fog_density(density)
fog.set_fog_height_falloff(falloff)
fog.set_volumetric_fog(False)
location = haze.get_actor_location()
location.z = lighting["haze"]["heightM"] * 100
haze.set_actor_location(location, False, False)
assert abs(fog.get_editor_property("fog_density") - density) < 1e-9
assert abs(fog.get_editor_property("fog_height_falloff") - falloff) < 1e-9
assert levels.save_current_level()

report = {
    "sunIntensityLux": sun.get_component_by_class(unreal.DirectionalLightComponent).intensity,
    "skyLightIntensity": labelled("light.sky")
    .get_component_by_class(unreal.SkyLightComponent)
    .intensity,
    "previousExposure": previous_exposure,
    "previousFog": previous_fog,
    "fogDensity": density,
    "fogHeightFalloff": falloff,
}
report_path.write_text(json.dumps(report, indent=2) + "\n")
unreal.log(
    "SNR_VALLEY_LIT " + json.dumps({k: report[k] for k in ("fogDensity", "fogHeightFalloff")})
)
