from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

from chainura.domain.destination import Exchange

class HorizontalAlignment(str, Enum): LEFT="left"; CENTER="center"; RIGHT="right"
class VerticalAlignment(str, Enum): TOP="top"; CENTER="center"; BOTTOM="bottom"

@dataclass(frozen=True, slots=True)
class Point:
    x: int; y: int

@dataclass(frozen=True, slots=True)
class Rectangle:
    left: int; top: int; right: int; bottom: int
    @property
    def width(self)->int: return self.right-self.left
    @property
    def height(self)->int: return self.bottom-self.top
    def as_tuple(self)->tuple[int,int,int,int]: return self.left,self.top,self.right,self.bottom

@dataclass(frozen=True, slots=True)
class FontSpec:
    role: str; preferred_size: int; minimum_size: int

@dataclass(frozen=True, slots=True)
class TextFitting:
    shrink_to_fit: bool=True; multiline: bool=False


RGBA = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class BadgeStyle:
    fill: RGBA
    radius: int = 10
    padding_x: int = 12
    padding_y: int = 5
    fill_by_value: tuple[tuple[str, RGBA], ...] = ()

@dataclass(frozen=True, slots=True)
class TextLayer:
    field: str
    box: Rectangle
    font: FontSpec

    horizontal: HorizontalAlignment = HorizontalAlignment.LEFT
    vertical: VerticalAlignment = VerticalAlignment.CENTER

    prefix: str = ""
    suffix: str = ""
    fitting: TextFitting = TextFitting()

    fill: RGBA = (255, 255, 255, 255)

    # Optional dynamic text colors, e.g. Long/Short
    fill_by_value: tuple[tuple[str, RGBA], ...] = ()
    # Semantic states avoid coupling production styles to formatted fixture values.
    fill_by_state: tuple[tuple[str, RGBA], ...] = ()

    # Optional rounded background
    badge: BadgeStyle | None = None

@dataclass(frozen=True, slots=True)
class InlineTextItem:
    field: str
    font: FontSpec
    flexible: bool = False
    prefix: str = ""
    suffix: str = ""
    fill: RGBA = (255, 255, 255, 255)
    fill_by_value: tuple[tuple[str, RGBA], ...] = ()
    badge: BadgeStyle | None = None

@dataclass(frozen=True, slots=True)
class InlineRowLayer:
    field: str
    box: Rectangle
    items: tuple[InlineTextItem, ...]
    gap: int
    vertical: VerticalAlignment = VerticalAlignment.CENTER

    def __post_init__(self) -> None:
        if not self.field or self.box.width <= 0 or self.box.height <= 0:
            raise ValueError("inline row identity and box must be valid")
        if not isinstance(self.items, tuple) or not self.items:
            raise ValueError("inline row must contain items")
        if self.gap < 0 or not any(item.flexible for item in self.items):
            raise ValueError("inline row needs a non-negative gap and flexible item")
        for item in self.items:
            if item.font.minimum_size <= 0 or item.font.preferred_size < item.font.minimum_size:
                raise ValueError(f"{item.field} font sizes are invalid")

@dataclass(frozen=True, slots=True)
class ImageLayer:
    field: str; box: Rectangle

@dataclass(frozen=True, slots=True)
class DebugOverlayConfig:
    enabled: bool=False; color: tuple[int,int,int,int]=(255,0,255,220)

@dataclass(frozen=True, slots=True)
class InitialCardLayout:
    exchange: Exchange
    text_layers: tuple[TextLayer,...]
    logo_layer: ImageLayer|None=None
    inline_rows: tuple[InlineRowLayer, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.exchange, Exchange):
            raise ValueError("layout exchange must be an Exchange")
        if not isinstance(self.text_layers, tuple) or not self.text_layers:
            raise ValueError("layout must contain text layers")
        if any(not isinstance(layer, TextLayer) for layer in self.text_layers):
            raise ValueError("layout text layers are invalid")
        if not isinstance(self.inline_rows, tuple) or any(
            not isinstance(row, InlineRowLayer) for row in self.inline_rows
        ):
            raise ValueError("layout inline rows are invalid")
        for layer in self.text_layers:
            if layer.box.width <= 0 or layer.box.height <= 0:
                raise ValueError(f"{layer.field} layout box must have positive dimensions")
            if layer.font.minimum_size <= 0 or layer.font.preferred_size < layer.font.minimum_size:
                raise ValueError(f"{layer.field} font sizes are invalid")
