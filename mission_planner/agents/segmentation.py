"""Lossless SAM tile masks in their original image coordinates."""

import base64
import io

import numpy as np
from PIL import Image


def encode_mask(pixels, origin_xy):
    assert pixels.dtype == np.bool_ and pixels.ndim == 2 and pixels.any()
    assert len(origin_xy) == 2 and all(type(v) is int and v >= 0 for v in origin_xy)
    output = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8) * 255).save(output, format="PNG")
    return {
        "origin_xy": list(origin_xy),
        "png_base64": base64.b64encode(output.getvalue()).decode(),
    }


def decode_mask(value, width, height, area):
    assert set(value) == {"origin_xy", "png_base64"}, "Unexpected SAM mask fields"
    x, y = value["origin_xy"]
    assert all(type(v) is int and v >= 0 for v in (x, y)), "Invalid SAM mask origin"
    data = base64.b64decode(value["png_base64"], validate=True)
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "PNG" and image.mode == "L", "SAM mask must be a grayscale PNG"
        assert 0 < image.width <= width - x and 0 < image.height <= height - y, (
            "SAM mask extends beyond its source image"
        )
        pixels = np.asarray(image)
    assert np.isin(pixels, (0, 255)).all(), "SAM mask must be binary"
    pixels = pixels == 255
    assert type(area) is int and area > 0 and int(pixels.sum()) == area, (
        "SAM mask area disagrees with its pixels"
    )
    return x, y, pixels
