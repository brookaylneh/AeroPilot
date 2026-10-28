"""Clip scenery triangles out of the searchable rectangle, retaining vertex attributes."""


def outside_rectangle(triangle, lower, upper):
    assert len(triangle) == 3 and len({len(vertex) for vertex in triangle}) == 1
    assert all(lower[axis] < upper[axis] for axis in (0, 1))
    for axis in (0, 1):
        if (
            max(v[axis] for v in triangle) <= lower[axis]
            or min(v[axis] for v in triangle) >= upper[axis]
        ):
            return [triangle]
    remainder = list(triangle)
    outside = []
    for axis, boundary, sign in (
        (0, lower[0], 1),
        (0, upper[0], -1),
        (1, lower[1], 1),
        (1, upper[1], -1),
    ):
        if not remainder:
            break
        inside, rejected = [], []
        for previous, current in zip(remainder[-1:] + remainder[:-1], remainder):
            a = sign * (previous[axis] - boundary)
            b = sign * (current[axis] - boundary)
            if (a >= 0) != (b >= 0):
                weight = a / (a - b)
                intersection = tuple(p + weight * (c - p) for p, c in zip(previous, current))
                intersection = (*intersection[:axis], boundary, *intersection[axis + 1 :])
                inside.append(intersection)
                rejected.append(intersection)
            (inside if b >= 0 else rejected).append(current)
        for index in range(1, len(rejected) - 1):
            a, b, c = rejected[0], rejected[index], rejected[index + 1]
            area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
            if area != 0:
                outside.append((a, b, c))
        remainder = inside
    return outside
