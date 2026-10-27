"""JPEG transport and explicit tile coordinates in source-frame pixels."""
import io
from dataclasses import dataclass

from PIL import Image

JPEG_QUALITY = 90


@dataclass(frozen=True)
class Tile:
    jpeg: bytes
    x: int
    y: int
    width: int
    height: int
    frame_width: int
    frame_height: int


def jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, format='JPEG', quality=JPEG_QUALITY, subsampling=0)
    return buffer.getvalue()


def tiles(image: Image.Image, size: int = 1008, overlap: int = 64) -> list[Tile]:
    assert size == 1008 and 64 <= overlap < size
    w, h = image.size
    assert w >= size and h >= size
    def starts(length):
        return sorted(set([*range(0, length-size+1, size-overlap), length-size]))
    return [Tile(jpeg(image.crop((x,y,x+size,y+size))), x,y,size,size,w,h)
            for y in starts(h) for x in starts(w)]


def iou(a, b) -> float:
    intersection = max(0, min(a[2],b[2])-max(a[0],b[0])) * max(0, min(a[3],b[3])-max(a[1],b[1]))
    union = (a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection
    assert union > 0
    return intersection / union
