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
