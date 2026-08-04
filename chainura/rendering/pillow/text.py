from __future__ import annotations
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol
from threading import local
from PIL import ImageDraw, ImageFont
from chainura.rendering.layouts.models import (HorizontalAlignment, InlineRowLayer,
    InlineTextItem, Rectangle, TextLayer, VerticalAlignment)
from .errors import RendererAssetError, RendererLayoutError

class FontProvider(Protocol):
    def get(self, role:str, size:int): ...

def _font_file(path:Path|str)->Path:
    result=Path(path)
    if result.is_dir():
        candidates=sorted((*result.glob("*.ttf"),*result.glob("*.otf")))
        if len(candidates) != 1:
            raise RendererAssetError(f"font directory must contain exactly one .ttf or .otf file: {result}")
        result=candidates[0]
    if not result.is_file(): raise RendererAssetError(f"font does not exist: {result}")
    return result.resolve()


class RoleFontProvider:
    def __init__(self,fallback_path:Path|str|None=None,role_paths:Mapping[str,Path|str]|None=None):
        self.fallback_path=_font_file(fallback_path) if fallback_path is not None else None
        resolved={role:_font_file(path) for role,path in (role_paths or {}).items()}
        if any(not isinstance(role,str) or not role for role in resolved):
            raise RendererAssetError("font roles must be non-empty strings")
        if self.fallback_path is None and not resolved:
            raise RendererAssetError("configure --font or at least one --font-role")
        self.role_paths=MappingProxyType(resolved); self._local=local()
        for path in dict.fromkeys(filter(None,(self.fallback_path,*resolved.values()))): self._load(path,10)

    def _load(self,path:Path,size:int):
        cache=getattr(self._local,"cache",None)
        if cache is None:cache={};self._local.cache=cache
        key=(path,size)
        if key not in cache:
            try:font=ImageFont.truetype(str(path),size)
            except (OSError,ValueError) as exc:raise RendererAssetError(f"invalid font: {path}") from exc
            cache[key]=font
        return cache[key]

    def get(self,role:str,size:int):
        path=self.role_paths.get(role,self.fallback_path)
        if path is None: raise RendererAssetError(f"no font configured for role: {role}")
        return self._load(path,size)


class PathFontProvider(RoleFontProvider):
    """Backward-compatible one-font provider used as every role's fallback."""
    def __init__(self,path:Path|str): super().__init__(fallback_path=path)

class DefaultTestFontProvider:
    """Explicit test injection; never selected by production or CLI code."""
    def get(self,role:str,size:int): return ImageFont.load_default(size=size)

def fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    layer: TextLayer,
    provider: FontProvider,
):
    sizes = (
        range(
            layer.font.preferred_size,
            layer.font.minimum_size - 1,
            -1,
        )
        if layer.fitting.shrink_to_fit
        else (layer.font.preferred_size,)
    )

    padding_x = layer.badge.padding_x if layer.badge else 0
    padding_y = layer.badge.padding_y if layer.badge else 0

    for size in sizes:
        font = provider.get(layer.font.role, size)
        measured = draw.textbbox((0, 0), text, font=font)

        text_width = measured[2] - measured[0]
        text_height = measured[3] - measured[1]

        required_width = text_width + padding_x * 2
        required_height = text_height + padding_y * 2

        if (
            required_width <= layer.box.width
            and required_height <= layer.box.height
        ):
            return font, size, measured

    raise RendererLayoutError(
        f"{layer.field} cannot fit its layout box "
        "at minimum font size"
    )

def draw_text_layer(
    draw: ImageDraw.ImageDraw,
    layer: TextLayer,
    value: str,
    provider: FontProvider,
):
    text = f"{layer.prefix}{value}{layer.suffix}"

    font, size, measured = fit_text(
        draw,
        text,
        layer,
        provider,
    )

    text_width = measured[2] - measured[0]
    text_height = measured[3] - measured[1]

    padding_x = layer.badge.padding_x if layer.badge else 0
    padding_y = layer.badge.padding_y if layer.badge else 0

    rendered_width = text_width + padding_x * 2
    rendered_height = text_height + padding_y * 2

    left = {
        HorizontalAlignment.LEFT:
            layer.box.left,

        HorizontalAlignment.CENTER:
            layer.box.left
            + (layer.box.width - rendered_width) / 2,

        HorizontalAlignment.RIGHT:
            layer.box.right - rendered_width,
    }[layer.horizontal]

    top = {
        VerticalAlignment.TOP:
            layer.box.top,

        VerticalAlignment.CENTER:
            layer.box.top
            + (layer.box.height - rendered_height) / 2,

        VerticalAlignment.BOTTOM:
            layer.box.bottom - rendered_height,
    }[layer.vertical]

    left = round(left)
    top = round(top)

    if layer.badge is not None:
        badge_fill = _resolved_color(
            layer.badge.fill,
            layer.badge.fill_by_value,
            value,
        )

        radius = min(
            layer.badge.radius,
            rendered_height // 2,
        )

        draw.rounded_rectangle(
            (
                left,
                top,
                left + rendered_width,
                top + rendered_height,
            ),
            radius=radius,
            fill=badge_fill,
        )

    text_fill = _resolved_color(
        layer.fill,
        layer.fill_by_value,
        value,
    )

    text_x = left + padding_x - measured[0]
    text_y = top + padding_y - measured[1]

    draw.text(
        (text_x, text_y),
        text,
        font=font,
        fill=text_fill,
    )

    actual = (
        left,
        top,
        left + rendered_width,
        top + rendered_height,
    )

    return size, actual

def _resolved_color(
    default: tuple[int, int, int, int],
    rules: tuple[tuple[str, tuple[int, int, int, int]], ...],
    value: str,
) -> tuple[int, int, int, int]:
    for expected, color in rules:
        if value == expected:
            return color

    return default


def _measure_inline(draw, item: InlineTextItem, text: str, provider: FontProvider,
                    sizes):
    padding_x = item.badge.padding_x if item.badge else 0
    padding_y = item.badge.padding_y if item.badge else 0
    for size in sizes:
        font = provider.get(item.font.role, size)
        measured = draw.textbbox((0, 0), text, font=font)
        width = measured[2] - measured[0] + 2 * padding_x
        height = measured[3] - measured[1] + 2 * padding_y
        yield font, size, measured, width, height


def draw_inline_row(draw: ImageDraw.ImageDraw, row: InlineRowLayer,
                    values: Mapping[str, str], provider: FontProvider):
    """Measure fixed items first, allocate the remainder, and draw without wrapping."""
    prepared = []
    for item in row.items:
        value = values[item.field]
        text = f"{item.prefix}{value}{item.suffix}"
        if item.flexible:
            prepared.append((item, value, text, None))
            continue
        candidates = _measure_inline(draw, item, text, provider,
                                     (item.font.preferred_size,))
        measurement = next(candidates)
        if measurement[4] > row.box.height:
            raise RendererLayoutError(f"inline row {row.field} cannot fit {item.field}")
        prepared.append((item, value, text, measurement))

    fixed_width = sum(entry[3][3] for entry in prepared if entry[3] is not None)
    available = row.box.width - row.gap * (len(row.items) - 1) - fixed_width
    flexible_count = sum(item.flexible for item in row.items)
    if available < flexible_count:
        raise RendererLayoutError(f"inline row {row.field} cannot fit at minimum sizes")
    base, remainder = divmod(available, flexible_count)
    slots = iter(base + (1 if index < remainder else 0)
                 for index in range(flexible_count))

    selected = []
    for item, value, text, measurement in prepared:
        slot_width = next(slots) if item.flexible else measurement[3]
        if item.flexible:
            measurement = None
            for candidate in _measure_inline(
                draw, item, text, provider,
                range(item.font.preferred_size, item.font.minimum_size - 1, -1),
            ):
                if candidate[3] <= slot_width and candidate[4] <= row.box.height:
                    measurement = candidate
                    break
            if measurement is None:
                raise RendererLayoutError(
                    f"inline row {row.field} cannot fit {item.field} at minimum size"
                )
        selected.append((item, value, text, measurement, slot_width))

    rendered = []
    left = row.box.left
    for item, value, text, measurement, slot_width in selected:
        font, size, bounds, rendered_width, rendered_height = measurement
        top = {
            VerticalAlignment.TOP: row.box.top,
            VerticalAlignment.CENTER: row.box.top + (row.box.height-rendered_height)/2,
            VerticalAlignment.BOTTOM: row.box.bottom-rendered_height,
        }[row.vertical]
        top = round(top)
        # The flexible allocation is its fitting constraint; its actual measured
        # content boundary drives the next exact gap.
        actual_width = rendered_width if item.flexible else slot_width
        child_box = Rectangle(left, top, left + actual_width, top + rendered_height)
        padding_x = item.badge.padding_x if item.badge else 0
        padding_y = item.badge.padding_y if item.badge else 0
        if item.badge:
            fill = _resolved_color(item.badge.fill, item.badge.fill_by_value, value)
            draw.rounded_rectangle((left, top, left + rendered_width, top + rendered_height),
                                   radius=min(item.badge.radius, rendered_height // 2), fill=fill)
        draw.text((left + padding_x - bounds[0], top + padding_y - bounds[1]), text,
                  font=font, fill=_resolved_color(item.fill, item.fill_by_value, value))
        rendered.append((item, size, child_box.as_tuple()))
        left += actual_width + row.gap
    if left - row.gap > row.box.right:
        raise RendererLayoutError(f"inline row {row.field} escaped its layout box")
    return tuple(rendered)
