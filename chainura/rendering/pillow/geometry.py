from __future__ import annotations

from PIL import Image

from chainura.rendering.layouts.models import Rectangle


def contain_size(source: tuple[int, int], box: Rectangle) -> tuple[int, int]:
    width, height = source
    scale = min(box.width / width, box.height / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def alpha_rounded_rectangle(image: Image.Image, box: Rectangle, *, radius: int, fill: tuple[int, int, int, int]) -> None:
    layer = Image.new("RGBA", image.size)
    from PIL import ImageDraw
    ImageDraw.Draw(layer).rounded_rectangle(box.as_tuple(), radius=radius, fill=fill)
    image.alpha_composite(layer)
