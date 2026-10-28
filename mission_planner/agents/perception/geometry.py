"""Pinhole geometry in world metres, using recorded per-aircraft NED origins."""

import math

import numpy as np

from world.cosys.terrain import WORLD, terrain_height


class Camera:
    def __init__(self, frame):
        assert frame["schemaVersion"] == 1
        assert frame["coordinateSystem"] == WORLD["coordinateSystem"] == "UE_LH_Z_UP_CENTIMETERS"
        assert (WORLD["north"], WORLD["east"], WORLD["moveWorldOrigin"]) == (
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
        assert (
            math.dist(
                [self.origin[k] / 100 + self.position[k] * (1 if k != "z" else -1) for k in "xyz"],
                [self.world[k] for k in "xyz"],
            )
            < 1e-5
        )
        q = frame["cameraPoseLocalNed"]["orientation"]
        x, y, z, w = (q[k] for k in "xyzw")
        assert abs(math.sqrt(x * x + y * y + z * z + w * w) - 1) < 1e-5
        norm = math.sqrt(x * x + y * y + z * z + w * w)
        x, y, z, w = (v / norm for v in (x, y, z, w))
        self.rotation = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ]
        )
        assert all(
            math.isfinite(v) for p in (self.origin, self.position, self.world) for v in p.values()
        )

    def project(self, point):
        local = np.array(
            [(point[k] - self.origin[k] / 100) * (1 if k != "z" else -1) for k in "xyz"]
        )
        view = self.rotation.T @ (local - [self.position[k] for k in "xyz"])
        assert view[0] > 0, "World point is behind the camera"
        pixel = np.array(
            [
                self.width / 2 + self.focal * view[1] / view[0],
                self.height / 2 + self.focal * view[2] / view[0],
            ]
        )
        assert pixel.shape == (2,) and np.isfinite(pixel).all()
        return pixel

    def ground(self, pixel):
        u, v = pixel
        assert math.isfinite(u) and math.isfinite(v)
        ray = self.rotation @ np.array(
            [1, (u - self.width / 2) / self.focal, (v - self.height / 2) / self.focal]
        )
        direction = {k: float(ray[i]) * (1 if k != "z" else -1) for i, k in enumerate("xyz")}
        assert direction["z"] < -1e-6, "Camera ray must intersect terrain below it"
        height = terrain_height(self.world["x"], self.world["y"])
        for _ in range(30):
            distance = (height - self.world["z"]) / direction["z"]
            assert distance > 0
            point = {k: self.world[k] + distance * direction[k] for k in "xyz"}
            height = terrain_height(point["x"], point["y"])
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
