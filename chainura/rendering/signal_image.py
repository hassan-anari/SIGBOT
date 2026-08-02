from __future__ import annotations
from pathlib import Path
from typing import Callable
from chainura.domain.destination import Exchange
from chainura.errors import RendererNotConfiguredError
from chainura.rendering.models import RenderedSignalImage, SignalRenderPayload

Renderer=Callable[[SignalRenderPayload,Path,Path],RenderedSignalImage]
_RENDERERS:dict[Exchange,Renderer]={}
def register_signal_renderer(exchange:Exchange, renderer:Renderer)->None: _RENDERERS[exchange]=renderer
def unregister_signal_renderer(exchange:Exchange)->None: _RENDERERS.pop(exchange,None)
def render_signal_image(payload:SignalRenderPayload,template_path:Path|str,output_path:Path|str)->RenderedSignalImage:
    renderer=_RENDERERS.get(payload.exchange)
    if renderer is None: raise RendererNotConfiguredError(f"renderer is not configured for {payload.exchange.value}")
    return renderer(payload,Path(template_path),Path(output_path))
