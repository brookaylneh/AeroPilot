"""Apply valley-lighting.json and the world.json sun; record stored values in lighting.json."""

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

load_env()
# Koschmieder: visibility is where contrast falls to 2 %, extinction = ln(50) / visibility.
KOSCHMIEDER = math.log(50)

evidence = Path(os.environ["SNR_EVIDENCE_DIR"])
report_path = evidence / "lighting.json"
assert evidence.is_dir() and not report_path.exists(), report_path
lighting = json.loads((ROOT / "world/unreal/valley-lighting.json").read_text())
assert set(lighting) == {
    "exposureEv100",
    "aerialPerspectiveViewDistanceScale",
    "clouds",
    "volumetricFog",
    "haze",
    "sunIntensityLux",
    "skyLightIntensity",
    "whiteBalanceKelvin",
    "localExposure",
}
assert lighting["exposureEv100"] == 13
assert 0 < lighting["sunIntensityLux"] <= 120000
assert 0 < lighting["skyLightIntensity"] <= 4
assert 1500 <= lighting["whiteBalanceKelvin"] <= 15000
assert set(lighting["localExposure"]) == {"highlightContrast", "shadowContrast"}
assert all(0.6 <= v <= 1 for v in lighting["localExposure"].values())
assert 0 < lighting["aerialPerspectiveViewDistanceScale"] <= 3
world = json.loads((ROOT / "world/cosys/world.json").read_text())
assert (world["coordinateSystem"], world["north"], world["east"]) == (
    "UE_LH_Z_UP_CENTIMETERS",
    "+X",
    "+Y",
)
sun_data = world["sun"]
assert 0 < sun_data["apparentElevationDegrees"] < 90
assert 0 <= sun_data["azimuthDegreesClockwiseFromNorth"] < 360
cloud_data = lighting["clouds"]
assert set(cloud_data) == {
    "material",
    "bottomKm",
    "heightKm",
    "shadowStrength",
    "shadowExtentKm",
    "shadowResolutionScale",
}
assert cloud_data["material"].startswith("/Engine/")
assert cloud_data["bottomKm"] >= 0 and cloud_data["heightKm"] > 0
assert 0 <= cloud_data["shadowStrength"] <= 1
assert cloud_data["shadowExtentKm"] > 0 and cloud_data["shadowResolutionScale"] > 0
volume_data = lighting["volumetricFog"]
assert set(volume_data) == {"distanceM", "scatteringDistribution"}
assert volume_data["distanceM"] > 0 and -0.9 <= volume_data["scatteringDistribution"] <= 0.9
assert set(lighting["haze"]) == {"visibilityKm", "halfHeightM", "heightM"}
assert lighting["haze"]["visibilityKm"] > 0 and lighting["haze"]["halfHeightM"] > 0
assert lighting["haze"]["heightM"] == 0

actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
assert levels.load_level("/Game/OpenMeadow/OpenMeadow")
level_actors = actors.get_all_level_actors()
(cloud,) = [a for a in level_actors if isinstance(a, unreal.VolumetricCloud)]
assert cloud.get_actor_label() == "sky.clouds"


def labelled(label):
    (actor,) = [a for a in level_actors if a.get_actor_label() == label]
    return actor


def set_properties(component, properties):
    for key, value in properties.items():
        component.set_editor_property(key, value)
    stored = {key: component.get_editor_property(key) for key in properties}
    for key, value in properties.items():
        actual = stored[key]
        if type(value) in (int, float):
            assert math.isclose(actual, value, rel_tol=1e-6, abs_tol=1e-9), (key, actual, value)
        elif isinstance(value, unreal.Color):
            assert (actual.r, actual.g, actual.b, actual.a) == (value.r, value.g, value.b, value.a)
        else:
            assert actual == value, (key, actual, value)
    return stored


(sun,) = [a for a in level_actors if isinstance(a, unreal.DirectionalLight)]
previous_rotation = sun.get_actor_rotation()
assert sun.set_actor_rotation(
    unreal.Rotator(
        roll=0,
        pitch=-sun_data["apparentElevationDegrees"],
        yaw=sun_data["azimuthDegreesClockwiseFromNorth"] - 180,
    ),
    False,
)
toward_sun = -sun.get_actor_forward_vector()
elevation = math.degrees(math.asin(toward_sun.z))
azimuth = math.degrees(math.atan2(toward_sun.y, toward_sun.x)) % 360
assert abs(elevation - sun_data["apparentElevationDegrees"]) < 1e-3
assert abs(azimuth - sun_data["azimuthDegreesClockwiseFromNorth"]) < 1e-3
assert abs(sun.get_actor_rotation().roll) < 1e-3
sun_component = sun.get_component_by_class(unreal.DirectionalLightComponent)
set_properties(
    sun_component, {"light_color": unreal.Color(255, 255, 255, 255), "use_temperature": False}
)
sun_shadows = set_properties(
    sun_component,
    {
        "intensity": lighting["sunIntensityLux"],
        "cast_cloud_shadows": True,
        "cloud_shadow_strength": cloud_data["shadowStrength"],
        "cloud_shadow_extent": cloud_data["shadowExtentKm"],
        "cloud_shadow_map_resolution_scale": cloud_data["shadowResolutionScale"],
    },
)
component = cloud.get_component_by_class(unreal.VolumetricCloudComponent)
assert component
material = unreal.load_asset(cloud_data["material"])
assert material
set_properties(component, {"material": material})
cloud_properties = set_properties(
    component,
    {
        "layer_bottom_altitude": cloud_data["bottomKm"],
        "layer_height": cloud_data["heightKm"],
    },
)
sky = labelled("light.sky").get_component_by_class(unreal.SkyLightComponent)
set_properties(sky, {"light_color": unreal.Color(255, 255, 255, 255)})
sky_properties = set_properties(
    sky, {"real_time_capture": True, "intensity": lighting["skyLightIntensity"]}
)
atmosphere = labelled("sky.atmosphere").get_component_by_class(unreal.SkyAtmosphereComponent)
# The UE 5.8.2 property is spelled AerialPespectiveViewDistanceScale in SkyAtmosphereComponent.h.
previous_aerial_scale = atmosphere.get_editor_property("aerial_pespective_view_distance_scale")
atmosphere_properties = set_properties(
    atmosphere,
    {"aerial_pespective_view_distance_scale": lighting["aerialPerspectiveViewDistanceScale"]},
)

look = labelled("look.daylight")
assert isinstance(look, unreal.PostProcessVolume) and look.get_editor_property("unbound")
settings = look.get_editor_property("settings")
previous_exposure = {
    key: str(settings.get_editor_property(key))
    for key in ("auto_exposure_method", "auto_exposure_bias", "auto_exposure_min_brightness")
}
# Manual exposure without the physical camera meters at EV100 0; the bias sets the EV, as in
# world/cosys/configure_drones.py for the drone captures.
exposure = {
    "auto_exposure_method": unreal.AutoExposureMethod.AEM_MANUAL,
    "auto_exposure_apply_physical_camera_exposure": False,
    "auto_exposure_bias": -lighting["exposureEv100"],
    "white_temp": lighting["whiteBalanceKelvin"],
    "white_tint": 0.0,
    "local_exposure_highlight_contrast_scale": lighting["localExposure"]["highlightContrast"],
    "local_exposure_shadow_contrast_scale": lighting["localExposure"]["shadowContrast"],
}
for key, value in exposure.items():
    settings.set_editor_property(f"override_{key}", True)
    settings.set_editor_property(key, value)
look.set_editor_property("settings", settings)
settings = look.get_editor_property("settings")
for key, value in exposure.items():
    assert settings.get_editor_property(f"override_{key}")
    actual = settings.get_editor_property(key)
    assert (
        math.isclose(actual, value, rel_tol=1e-6)
        if type(value) in (int, float)
        else actual == value
    )

haze = labelled("sky.haze")
fog = haze.get_component_by_class(unreal.ExponentialHeightFogComponent)
previous_fog = {
    key: fog.get_editor_property(key)
    for key in ("fog_density", "fog_height_falloff", "enable_volumetric_fog")
}
previous_fog["heightM"] = haze.get_actor_location().z / 100
# The renderer takes FogDensity and FogHeightFalloff in 1/1000 per cm (Renderer SceneCore.cpp),
# so FogDensity is the extinction per 10 m and the density halves every 1000 / FogHeightFalloff cm
# (exp2 in HeightFogCommon.ush).
density = KOSCHMIEDER / lighting["haze"]["visibilityKm"] / 100
falloff = 1000 / (lighting["haze"]["halfHeightM"] * 100)
assert math.isclose(density, 0.0002, rel_tol=0, abs_tol=1e-12), density
assert math.isclose(falloff, 0.2, rel_tol=0, abs_tol=1e-12), falloff
fog_properties = set_properties(
    fog,
    {
        "fog_density": density,
        "fog_height_falloff": falloff,
        "enable_volumetric_fog": True,
        "volumetric_fog_distance": volume_data["distanceM"] * 100,
        "volumetric_fog_scattering_distribution": volume_data["scatteringDistribution"],
    },
)
location = haze.get_actor_location()
location.z = lighting["haze"]["heightM"] * 100
haze.set_actor_location(location, False, False)
assert haze.get_actor_location().z == location.z
assert levels.save_current_level()

report = {
    "sunIntensityLux": sun_component.intensity,
    "skyLightIntensity": sky.intensity,
    "sun": {
        "localTime": sun_data["localTime"],
        "source": sun_data["source"],
        "apparentElevationDegrees": elevation,
        "azimuthDegreesClockwiseFromNorth": azimuth,
        "previousRotationDegrees": {
            "pitch": previous_rotation.pitch,
            "yaw": previous_rotation.yaw,
            "roll": previous_rotation.roll,
        },
        "cloudShadows": sun_shadows,
    },
    "clouds": {"material": material.get_path_name(), **cloud_properties},
    "skyLight": sky_properties,
    "previousAerialPerspectiveViewDistanceScale": previous_aerial_scale,
    "atmosphere": atmosphere_properties,
    "previousExposure": previous_exposure,
    "exposure": {key: str(settings.get_editor_property(key)) for key in exposure},
    "exposureEv100": -settings.get_editor_property("auto_exposure_bias"),
    "previousFog": previous_fog,
    "fog": {**fog_properties, "heightM": haze.get_actor_location().z / 100},
    "fogDensity": fog_properties["fog_density"],
    "fogHeightFalloff": fog_properties["fog_height_falloff"],
}
report_path.write_text(json.dumps(report, indent=2) + "\n")
unreal.log("SNR_VALLEY_LIT " + json.dumps(report))
