from __future__ import annotations
from pathlib import Path
from typing import Protocol
from PIL import ImageDraw, ImageFont
from chainura.rendering.layouts.models import HorizontalAlignment, TextLayer, VerticalAlignment
from .errors import RendererAssetError, RendererLayoutError

class FontProvider(Protocol):
    def get(self, role:str, size:int): ...

class PathFontProvider:
    def __init__(self,path:Path|str):
        self.path=Path(path)
        if self.path.is_dir():
            candidates=sorted((*self.path.glob("*.ttf"),*self.path.glob("*.otf")))
            if len(candidates) != 1:
                raise RendererAssetError(f"font directory must contain exactly one .ttf or .otf file: {self.path}")
            self.path=candidates[0]
        if not self.path.is_file(): raise RendererAssetError(f"font does not exist: {self.path}")
    def get(self,role:str,size:int):
        try: return ImageFont.truetype(str(self.path),size)
        except (OSError,ValueError) as exc: raise RendererAssetError(f"invalid font: {self.path}") from exc

class DefaultTestFontProvider:
    """Explicit test injection; never selected by production or CLI code."""
    def get(self,role:str,size:int): return ImageFont.load_default(size=size)

def fit_text(draw:ImageDraw.ImageDraw,text:str,layer:TextLayer,provider:FontProvider):
    sizes=range(layer.font.preferred_size,layer.font.minimum_size-1,-1) if layer.fitting.shrink_to_fit else (layer.font.preferred_size,)
    for size in sizes:
        font=provider.get(layer.font.role,size)
        box=draw.textbbox((0,0),text,font=font)
        if box[2]-box[0] <= layer.box.width and box[3]-box[1] <= layer.box.height: return font,size,box
    raise RendererLayoutError(f"{layer.field} cannot fit its layout box at minimum font size")

def draw_text_layer(draw:ImageDraw.ImageDraw,layer:TextLayer,value:str,provider:FontProvider):
    text=f"{layer.prefix}{value}{layer.suffix}"
    font,size,measured=fit_text(draw,text,layer,provider); width=measured[2]-measured[0]; height=measured[3]-measured[1]
    x={HorizontalAlignment.LEFT:layer.box.left,HorizontalAlignment.CENTER:layer.box.left+(layer.box.width-width)/2,HorizontalAlignment.RIGHT:layer.box.right-width}[layer.horizontal]
    y={VerticalAlignment.TOP:layer.box.top,VerticalAlignment.CENTER:layer.box.top+(layer.box.height-height)/2,VerticalAlignment.BOTTOM:layer.box.bottom-height}[layer.vertical]
    draw.text((round(x)-measured[0],round(y)-measured[1]),text,font=font,fill=layer.fill)
    return size,(round(x),round(y),round(x+width),round(y+height))
