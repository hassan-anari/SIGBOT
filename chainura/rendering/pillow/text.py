from __future__ import annotations
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol
from PIL import ImageDraw, ImageFont
from chainura.rendering.layouts.models import HorizontalAlignment, TextLayer, VerticalAlignment
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
        self.role_paths=MappingProxyType(resolved); self._cache={}
        for path in dict.fromkeys(filter(None,(self.fallback_path,*resolved.values()))): self._load(path,10)

    def _load(self,path:Path,size:int):
        key=(path,size)
        if key not in self._cache:
            try: font=ImageFont.truetype(str(path),size)
            except (OSError,ValueError) as exc: raise RendererAssetError(f"invalid font: {path}") from exc
            self._cache[key]=font
        return self._cache[key]

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