from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

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

@dataclass(frozen=True, slots=True)
class TextLayer:
    field: str; box: Rectangle; font: FontSpec
    horizontal: HorizontalAlignment=HorizontalAlignment.LEFT
    vertical: VerticalAlignment=VerticalAlignment.CENTER
    prefix: str=""; suffix: str=""; fitting: TextFitting=TextFitting()
    fill: tuple[int,int,int,int]=(255,255,255,255)

@dataclass(frozen=True, slots=True)
class ImageLayer:
    field: str; box: Rectangle

@dataclass(frozen=True, slots=True)
class DebugOverlayConfig:
    enabled: bool=False; color: tuple[int,int,int,int]=(255,0,255,220)

@dataclass(frozen=True, slots=True)
class InitialCardLayout:
    exchange: str; text_layers: tuple[TextLayer,...]; logo_layer: ImageLayer|None=None
