"""Seeded valley dressing from valley-dressing.json in mission north/east metres.

`plan` places every row: scatter rows on stratified cells thinned by a cluster noise inside their
constraints and outside the clearances, explicit rows at their authored poses. The placements file
feeds `ScenarioStudio/Scripts/build-valley-dressing.py`, which traces the ground in the editor.
"""

import argparse
import hashlib
import json
import math
import zlib
from pathlib import Path

import numpy as np
from matplotlib.path import Path as PolygonPath
from PIL import Image
from scipy.spatial import cKDTree

from world.cosys.landmarks import LANDMARKS_PATH
from world.cosys.terrain import BOUNDS, FIELD, ROOT, TERRAIN_DIR, WORLD
from world.unreal.valley import catalogue, densify, lookup, points, surface_points

DRESSING_PATH = ROOT / "world/unreal/valley-dressing.json"
# Tread half-widths (m) kept clear around each trail and road centreline; the creek clears its
# receipt bank tops.
LINE_HALF_M = {"trail": 1.2, "road": 2.8}
CLEARED = ("structure", "obstacle", "bridge", "parking", "command_post", "campsite")
# facts.md §8: instances taller than this stand in a polygon whose heightM covers them.
TALL_M = 4.0
# facts.md §7: no instance may top the 18 m lodgepole cap at its row's largest jittered scale.
CAP_M = 18.0
HOLDERS = ("forest", "structure", "obstacle", "outcrop")
SCALE_JITTER = 0.15
# Scatter stays 1 m inside the landscape, where every terrain normal has samples.
WHOLE = [BOUNDS[end][axis] / 100 + sign for axis in "xy" for end, sign in (("min", 1), ("max", -1))]


def half_up(metres):
    return math.ceil(metres * 2 - 1e-9) / 2


class Site:
    """Landmark geometry, clearances and layer masks shared by every row."""

    def __init__(self, document, receipt):
        self.landmarks = {item["id"]: item for item in catalogue(document)}
        port = WORLD["port"]
        centre = np.array([port["centerUeCm"]["x"], port["centerUeCm"]["y"]]) / 100
        half = np.array(port["dimensionsMetres"][:2]) / 2
        corners = centre + half * [[-1, -1], [-1, 1], [1, 1], [1, -1]]
        self.areas = {
            i: surface_points(item)
            for i, item in self.landmarks.items()
            if item["geometry"]["type"] == "polygon"
        }
        self.areas["port"] = corners
        # centerUeCm.z is the pad top (build-drone-port.py hangs the slab below it).
        self.port_top = port["centerUeCm"]["z"] / 100
        data = (ROOT / FIELD["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == WORLD["heightfieldSha256"]
        raw = np.frombuffer(data, dtype="<u2").reshape(FIELD["rows"], FIELD["columns"])
        self.elevation = (raw.astype(float) - FIELD["zeroSample"]) / FIELD["samplesPerMetre"]
        self.edges = {
            area: (PolygonPath(ring), cKDTree(densify(np.vstack([ring, ring[:1]]), 0.25)[1]))
            for area, ring in self.areas.items()
        }
        creek = receipt["drainage"]["creek"]
        route = points(self.landmarks[creek["id"]])
        assert len(creek["bankTopM"]) == len(route) == creek["routeNodes"]
        stations, dense = densify(route, 0.25)
        arc = np.r_[0, np.cumsum(np.linalg.norm(np.diff(route, axis=0), axis=1))]
        self.creek = cKDTree(dense)
        self.banks = np.interp(stations, arc, creek["bankTopM"])
        self.lines = [
            (cKDTree(densify(points(item), 0.25)[1]), LINE_HALF_M[item["kind"]])
            for item in self.landmarks.values()
            if item["kind"] in LINE_HALF_M
        ]
        self.ponds = [i for i, item in self.landmarks.items() if item["kind"] == "pond"]
        self.cleared = [i for i, item in self.landmarks.items() if item["kind"] in CLEARED]
        self.cleared += ["port"]
        self.masks = {
            name: np.asarray(Image.open(ROOT / entry["path"]))
            for name, entry in receipt["masks"]["files"].items()
        }
        assert all(m.shape == (FIELD["rows"], FIELD["columns"]) for m in self.masks.values())

    def edge(self, areas, p, bound):
        """Signed metres from the nearest edge of the disjoint areas, negative inside one, and
        infinite beyond bound metres."""
        signed = []
        for area in areas:
            path, tree = self.edges[area]
            distance = tree.query(p, distance_upper_bound=bound)[0]
            signed.append(np.where(path.contains_points(p), -distance, distance))
        return np.min(signed, axis=0)

    def channel(self, p, bound):
        """Metres from the creek centreline (infinite beyond bound) and the bank-top half-width."""
        distance, index = self.creek.query(p, distance_upper_bound=bound)
        # A miss has index len(banks) and an infinite distance, which clears any bank.
        return distance, self.banks[np.minimum(index, len(self.banks) - 1)]

    def clear(self, p, margin, reach):
        """Centres margin metres clear of the ponds, and margin plus the instance reach clear of
        the trails, roads, creek banks and the other cleared areas; shore rows overhang water."""
        keep = self.edge(self.ponds, p, margin) >= margin
        margin += reach
        for tree, half in self.lines:
            keep &= tree.query(p, distance_upper_bound=half + margin)[0] >= half + margin
        distance, bank = self.channel(p, self.banks.max() + margin)
        keep &= distance >= bank + margin
        return keep & (self.edge(self.cleared, p, margin) >= margin)

    def ground(self, kind, p):
        """Ground metres under p: the bilinear heightfield or the drone port's top."""
        heights = {
            "terrain": lambda: lookup(self.elevation, p),
            "port": lambda: np.full(len(p), self.port_top),
        }
        return heights[kind]()

    def normal(self, p):
        """Unit terrain normals (north, east, up) from 0.4 m central differences."""
        step = np.array([[0.4, 0], [0, 0.4]])
        slope = [
            (lookup(self.elevation, p + d) - lookup(self.elevation, p - d)) / 0.8 for d in step
        ]
        n = np.column_stack((-slope[0], -slope[1], np.ones(len(p))))
        return n / np.linalg.norm(n, axis=1)[:, None]

    def mask(self, name, p):
        spacing = FIELD["sampleSpacingMetres"]
        column = np.rint((p[:, 0] - BOUNDS["min"]["x"] / 100) / spacing).astype(int)
        row = np.rint((p[:, 1] - BOUNDS["min"]["y"] / 100) / spacing).astype(int)
        return self.masks[name][row, column]


def extent(site, constraint):
    """North/east box [n0, n1, e0, e1] holding every point the constraint accepts."""
    kind, *values = constraint
    if kind == "box":
        return values
    if kind == "mask":
        return WHOLE
    ring = (
        np.vstack([site.areas[a] for a in values[0]])
        if kind == "polygon"
        else points(site.landmarks[values[0]])
    )
    reach = max(values[2], 0)
    low, high = ring.min(axis=0) - reach, ring.max(axis=0) + reach
    return [low[0], high[0], low[1], high[1]]


def accepts(site, constraint, p):
    kind, *values = constraint
    if kind == "box":
        n0, n1, e0, e1 = values
        return (p[:, 0] >= n0) & (p[:, 0] <= n1) & (p[:, 1] >= e0) & (p[:, 1] <= e1)
    if kind == "mask":
        return site.mask(values[0], p) >= values[1]
    # Distances beyond the band's finite limits only need to be known as beyond.
    bound = max(abs(v) for v in values[1:] if math.isfinite(v)) + 1
    if kind == "polygon":
        signed = site.edge(values[0], p, bound)
    else:
        assert kind == "line" and values[0] == "meadow-creek", constraint
        signed = site.channel(p, bound)[0]
    return (signed >= values[1]) & (signed <= values[2])


def noise(p, cell_m, seed):
    """Smooth value noise in [0, 1) on a cell_m lattice."""
    q = p / cell_m
    base = np.floor(q).astype(np.int64)
    t = q - base
    t = t * t * (3 - 2 * t)

    def lattice(di, dj):
        h = (base[:, 0] + di) * 73856093 ^ (base[:, 1] + dj) * 19349663 ^ seed
        h = (h ^ (h >> 13)) * 1274126177
        return ((h ^ (h >> 16)) & 0xFFFFFF) / 0x1000000

    bottom = lattice(0, 0) * (1 - t[:, 1]) + lattice(0, 1) * t[:, 1]
    top = lattice(1, 0) * (1 - t[:, 1]) + lattice(1, 1) * t[:, 1]
    return bottom * (1 - t[:, 0]) + top * t[:, 0]


def reach(row, meshes):
    """Horizontal bounding radius (m) of the row's widest mesh at its largest scale, whose
    tallest mesh must stay under the cap."""
    largest = row["scale"] * (1 + SCALE_JITTER)
    assert largest * max(meshes[m]["boundsM"][5] for m in row["meshes"]) <= CAP_M, row["name"]
    radii = [
        math.hypot(*np.abs(np.reshape(meshes[m]["boundsM"][:4], (2, 2))).max(1))
        for m in row["meshes"]
    ]
    return largest * max(radii)


def scatter(site, row, seed, meshes):
    salt = zlib.crc32(row["name"].encode())
    rng = np.random.default_rng([seed, salt])
    boxes = np.array([WHOLE] + [extent(site, c) for c in row["region"]])
    n0, e0 = boxes[:, [0, 2]].max(axis=0)
    n1, e1 = boxes[:, [1, 3]].min(axis=0)
    assert n1 > n0 and e1 > e0, row["name"]
    step = row["spacingM"]
    grid = np.stack(np.meshgrid(np.arange(n0, n1, step), np.arange(e0, e1, step)), -1)
    p = grid.reshape(-1, 2) + rng.uniform(0, step, (grid.size // 2, 2))
    for constraint in [["box", n0, n1, e0, e1]] + row["region"]:
        p = p[accepts(site, constraint, p)]
    p = p[site.clear(p, row["clearM"], reach(row, meshes))]
    assert len(p), row["name"]
    score = 0.75 * noise(p, row["clusterM"], (seed ^ salt) & 0xFFFF)
    score += 0.25 * rng.uniform(size=len(p))
    p = p[score < np.quantile(score, row["keep"])]
    count = len(p)
    assert count, row["name"]
    return {
        "z": site.ground(row["ground"], p),
        "mesh": [row["meshes"][i] for i in rng.integers(len(row["meshes"]), size=count)],
        "north": p[:, 0],
        "east": p[:, 1],
        "yaw": rng.uniform(0, 360, count),
        "scale": row["scale"] * rng.uniform(1 - SCALE_JITTER, 1 + SCALE_JITTER, count),
    }


def footprint(bounds, north, east, yaw, scale):
    """The four horizontal bounding-box corners (m) of a mesh at a pose."""
    x0, x1, y0, y1 = (scale * b for b in bounds[:4])
    c, s = math.cos(math.radians(yaw)), math.sin(math.radians(yaw))
    return np.array(
        [
            [north + x * c - y * s, east + x * s + y * c]
            for x, y in ((x0, y0), (x0, y1), (x1, y1), (x1, y0))
        ]
    )


def explicit(site, row, meshes):
    poses = np.array(row["at"], dtype=float)
    assert poses.shape[1] == 3, row["name"]
    ring = PolygonPath(
        site.areas["port"] if row["area"] == "port" else points(site.landmarks[row["area"]])
    )
    corners = [footprint(meshes[row["mesh"]]["boundsM"], *pose, row["scale"]) for pose in poses]
    for pose, box in zip(poses, corners):
        assert ring.contains_points(box).all(), (row["name"], pose)
    centres = site.ground(row["ground"], poses[:, :2])
    return {
        # A footprint stands at the median ground of its centre and corners, so a bridge rests
        # on its banks rather than on the channel bed.
        "z": np.array(
            [np.median(np.r_[z, site.ground(row["ground"], c)]) for z, c in zip(centres, corners)]
        ),
        "mesh": [row["mesh"]] * len(poses),
        "north": poses[:, 0],
        "east": poses[:, 1],
        "yaw": poses[:, 2],
        "scale": np.full(len(poses), float(row["scale"])),
    }


def plan(dressing, document, receipt):
    """{row name: placement arrays}, asserting heights against the landmark polygons."""
    site = Site(document, receipt)
    meshes = dressing["meshes"]
    rows = {}
    for row in dressing["rows"]:
        assert row["name"] not in rows and isinstance(row["align"], bool), row["name"]
        placed = (
            explicit(site, row, meshes)
            if "at" in row
            else scatter(site, row, dressing["seed"], meshes)
        )
        placed["top"] = placed["scale"] * [meshes[m]["boundsM"][5] for m in placed["mesh"]]
        assert placed["top"].max() <= CAP_M, row["name"]
        p = np.column_stack((placed["north"], placed["east"]))
        placed["normal"] = site.normal(p) if row["align"] else np.tile([0.0, 0.0, 1.0], (len(p), 1))
        rows[row["name"]] = placed
    holders = [i for i, item in site.landmarks.items() if item["kind"] in HOLDERS]
    for name, placed in rows.items():
        tall = placed["top"] > TALL_M
        p = np.column_stack((placed["north"], placed["east"]))[tall]
        covered = np.zeros(len(p), bool)
        for area in holders:
            inside = PolygonPath(site.areas[area]).contains_points(p)
            height = site.landmarks[area]["heightM"]
            covered |= inside & (placed["top"][tall] <= height)
        assert covered.all(), (name, p[~covered][:3])
    return rows


def measured(rows, document):
    """Rounded-up tallest instance top per holder polygon."""
    tops = {}
    for item in document["landmarks"]:
        if item["kind"] not in HOLDERS:
            continue
        ring = PolygonPath(points(item))
        found = [
            float(
                placed["top"][
                    ring.contains_points(np.column_stack((placed["north"], placed["east"])))
                ].max(initial=0)
            )
            for placed in rows.values()
        ]
        tops[item["id"]] = half_up(max(found))
    return tops


def indexed(names):
    unique, index = np.unique(names, return_inverse=True)
    return unique.tolist(), index.tolist()


def load():
    receipt = json.loads((TERRAIN_DIR / "terrain.json").read_text())
    assert receipt["heightfieldSha256"] == WORLD["heightfieldSha256"]
    return json.loads(DRESSING_PATH.read_text()), json.loads(LANDMARKS_PATH.read_text()), receipt


def write(output):
    """Plan the dressing into a new placements file for build-valley-dressing.py."""
    assert not output.exists(), output
    dressing, document, receipt = load()
    rows = plan(dressing, document, receipt)
    digest = {
        "dressingSha256": DRESSING_PATH,
        "landmarksSha256": LANDMARKS_PATH,
        "heightfieldSha256": ROOT / FIELD["path"],
    }
    placements = {
        **{key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in digest.items()},
        "meshes": dressing["meshes"],
        "measuredHeightM": measured(rows, document),
        "rows": [
            {
                "name": row["name"],
                "ground": row["ground"],
                # Each instance names its mesh by index into the row's list.
                **dict(zip(("meshes", "mesh"), indexed(rows[row["name"]]["mesh"]))),
                **{
                    key: np.round(rows[row["name"]][key], 3).tolist()
                    for key in ("north", "east", "z", "yaw", "scale")
                },
                "normal": np.round(rows[row["name"]]["normal"], 4).tolist(),
            }
            for row in dressing["rows"]
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(placements) + "\n")
    counts = {row["name"]: len(row["mesh"]) for row in placements["rows"]}
    counts["total"] = sum(counts.values())
    print(
        json.dumps({"counts": counts, "measuredHeightM": placements["measuredHeightM"]}, indent=2)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    write(parser.parse_args().output.resolve())
