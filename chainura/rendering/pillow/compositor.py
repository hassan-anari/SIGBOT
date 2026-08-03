from __future__ import annotations
import hashlib, os, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, UnidentifiedImageError
from chainura.domain.destination import Exchange
from chainura.domain.signal import TradeDirection
from chainura.rendering.models import (EXCHANGE_IMAGE_SPECS, TIME_DISPLAY_STRATEGIES,
    RenderedSignalImage, SignalRenderPayload, TimeDisplayStrategy)
from chainura.rendering.layouts.models import DebugOverlayConfig, InitialCardLayout
from .errors import RendererAssetError, RendererLayoutError
from .formatting import *
from .geometry import contain_size
from .text import FontProvider, draw_text_layer

def _open_png(path:Path, *, label:str)->Image.Image:
    if not path.is_file(): raise RendererAssetError(f"{label} does not exist: {path}")
    try:
        image=Image.open(path); image.load()
    except (OSError,UnidentifiedImageError) as exc: raise RendererAssetError(f"unreadable {label}: {path}") from exc
    if image.format != "PNG": raise RendererAssetError(f"{label} must be PNG: {path}")
    return image.convert("RGBA")

def _values(payload: SignalRenderPayload) -> dict[str, str]:
    strategy = TIME_DISPLAY_STRATEGIES[payload.exchange]
    if strategy is TimeDisplayStrategy.ELAPSED:
        time = format_elapsed(payload.elapsed_seconds)
    elif strategy is TimeDisplayStrategy.TEHRAN_WALL_CLOCK:
        time = format_tehran_timestamp(payload.rendered_at)
    else:
        time = format_utc_timestamp(payload.rendered_at)

    direction = payload.direction.value.title()
    leverage = format_leverage(payload.leverage)

    values = {
        "symbol": format_symbol(payload.native_symbol),
        "direction": direction,
        "leverage": leverage,
        "entry": format_entry_price(payload.entry_price),
        "price": format_display_price(payload.display_price),
        "pnl": format_initial_pnl(payload.initial_pnl_percent),
        "time": time,
    }

    if payload.exchange is Exchange.OURBIT:
        values["meta"] = f"Perp  |  {direction}  |  {leverage}"

    if payload.exchange is Exchange.TOOBIT:
        date_value, clock_value = values["time"].split(" ", 1)

        values["time_clock"] = clock_value
        values["time_date"] = date_value

    return values

def compose_initial(payload:SignalRenderPayload,template_path:Path|str,output_path:Path|str,layout:InitialCardLayout,font_provider:FontProvider,debug:DebugOverlayConfig=DebugOverlayConfig())->RenderedSignalImage:
    if layout.exchange is not payload.exchange:
        raise RendererLayoutError(f"{layout.exchange.value} layout cannot render {payload.exchange.value} payload")
    try: spec=EXCHANGE_IMAGE_SPECS[payload.exchange]
    except KeyError as exc: raise RendererLayoutError(f"no approved image specification for {payload.exchange!r}") from exc
    template_path,output_path=Path(template_path),Path(output_path)
    image=_open_png(template_path,label="template")
    if image.size != (spec.width,spec.height): raise RendererAssetError(f"template dimensions must be {spec.width}x{spec.height}, got {image.width}x{image.height}")
    draw=ImageDraw.Draw(image,"RGBA"); values=_values(payload); fitted=[]
    for layer in layout.text_layers: fitted.append((layer,draw_text_layer(draw,layer,values[layer.field],font_provider)))
    if payload.coin_logo_path is not None:
        logo=_open_png(payload.coin_logo_path,label="coin logo"); box=layout.logo_layer
        if box:
            bounds=box.box; logo.thumbnail(contain_size(logo.size,bounds),Image.Resampling.LANCZOS)
            image.alpha_composite(logo,(bounds.left+(bounds.width-logo.width)//2,bounds.top+(bounds.height-logo.height)//2))
    if debug.enabled:
        for layer,(size,actual) in fitted:
            draw.rectangle(layer.box.as_tuple(),outline=debug.color,width=3); draw.rectangle(actual,outline=(0,255,255,220),width=2)
            draw.text((layer.box.left,layer.box.top),f"{layer.field} {size}px",fill=debug.color)
        if layout.logo_layer: draw.rectangle(layout.logo_layer.box.as_tuple(),outline=debug.color,width=3)
    try: output_path.parent.mkdir(parents=True,exist_ok=True)
    except OSError as exc: raise RendererAssetError(f"cannot create output directory: {output_path.parent}") from exc
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{output_path.name}.",suffix=".tmp",dir=output_path.parent,delete=False) as stream:
            temporary=Path(stream.name); image.save(stream,format="PNG"); stream.flush(); os.fsync(stream.fileno())
        data=temporary.read_bytes(); os.replace(temporary,output_path); temporary=None
    except OSError as exc: raise RendererAssetError(f"cannot write output: {output_path}") from exc
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return RenderedSignalImage(output_path,spec.width,spec.height,spec.content_type,hashlib.sha256(data).hexdigest())
