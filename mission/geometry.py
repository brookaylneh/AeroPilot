"""Pinhole geometry in world metres, using recorded per-aircraft NED origins."""

import math

import numpy as np

from simulation.phase2a import client as sim


class Camera:
    def __init__(self, frame):
        assert frame["schemaVersion"] == 1
        assert (
            frame["coordinateSystem"] == sim.WORLD["coordinateSystem"] == "UE_LH_Z_UP_CENTIMETERS"
        )
        assert (sim.WORLD["north"], sim.WORLD["east"], sim.WORLD["moveWorldOrigin"]) == (
            "+X",
            "+Y",
            False,
        )
        self.name, self.origin = frame["vehicleId"], frame["localNedOriginUeCm"]
        self.width, self.height = frame["width"], frame["height"]
        assert self.width > 0 and self.height > 0 and self.width * 9 == self.height * 16
        fov = frame["horizontalFovDegrees"]
        assert abs(fov - 70) < 0.01
        self.focal = self.width / (2 * math.tan(math.radians(fov) / 2))
        self.position = frame["cameraPoseLocalNed"]["position"]
        self.world = frame["cameraPositionWorldM"]
        assert sim.distance(sim.to_world(self.name, self.position, self.origin), self.world) < 1e-5
        q = frame["cameraPoseLocalNed"]["orientation"]
        self.q = sim.airsim.Quaternionr(*(q[k] for k in "xyzw"))
        norm = self.q.get_length()
        assert abs(norm - 1) < 1e-5
        self.q = self.q / norm
        assert all(
            math.isfinite(v) for p in (self.origin, self.position, self.world) for v in p.values()
        )

    def project(self, point):
        local = sim.to_local(self.name, point, self.origin)
        vector = sim.airsim.Quaternionr(*(local[k] - self.position[k] for k in "xyz"), 0)
        view = self.q.conjugate() * vector * self.q
        assert view.x_val > 0, "World point is behind the camera"
        pixel = np.array(
            [
                self.width / 2 + self.focal * view.y_val / view.x_val,
                self.height / 2 + self.focal * view.z_val / view.x_val,
            ]
        )
        assert pixel.shape == (2,) and np.isfinite(pixel).all()
        return pixel

    def ground(self, pixel):
        u, v = pixel
        assert math.isfinite(u) and math.isfinite(v)
        ray = sim.airsim.Quaternionr(
            1, (u - self.width / 2) / self.focal, (v - self.height / 2) / self.focal, 0
        )
        ray = self.q * ray * self.q.conjugate()
        endpoint = sim.to_world(
            self.name,
            {k: self.position[k] + val for k, val in zip("xyz", (ray.x_val, ray.y_val, ray.z_val))},
            self.origin,
        )
        direction = {k: endpoint[k] - self.world[k] for k in "xyz"}
        assert direction["z"] < -1e-6, "Camera ray must intersect terrain below it"
        height = sim.terrain_height(self.world["x"], self.world["y"])
        for _ in range(30):
            distance = (height - self.world["z"]) / direction["z"]
            assert distance > 0
            point = {k: self.world[k] + distance * direction[k] for k in "xyz"}
            height = sim.terrain_height(point["x"], point["y"])
            if abs(height - point["z"]) < 0.005:
                return point
        raise AssertionError("Terrain intersection did not converge to 5 mm")

    def check(self):
        errors = [
            np.linalg.norm(self.project(self.ground((u, v))) - (u, v))
            for u in np.linspace(0, self.width - 1, 9)
            for v in np.linspace(0, self.height - 1, 5)
        ]
        maximum = float(max(errors))
        assert maximum < 0.5, maximum
        return maximum
