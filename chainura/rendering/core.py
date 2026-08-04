"""Pure, production-safe exchange compositor dispatch."""
from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.rendering.layouts.ourbit import LAYOUT as OURBIT_LAYOUT
from chainura.rendering.layouts.toobit import LAYOUT as TOOBIT_LAYOUT
from chainura.rendering.layouts.lbank import LAYOUT as LBANK_LAYOUT
from chainura.rendering.layouts.models import DebugOverlayConfig, InitialCardLayout
from chainura.rendering.models import SignalRenderPayload, RenderedSignalImage
from chainura.rendering.pillow.compositor import compose_initial
from chainura.rendering.pillow.errors import RendererLayoutError
from chainura.rendering.pillow.text import FontProvider
from typing import Mapping

LAYOUTS={Exchange.OURBIT:OURBIT_LAYOUT,Exchange.TOOBIT:TOOBIT_LAYOUT,Exchange.LBANK:LBANK_LAYOUT}

def render_initial(payload:SignalRenderPayload, template_path:Path|str,
                   output_path:Path|str, font_provider:FontProvider)->RenderedSignalImage:
    try: selected=LAYOUTS[payload.exchange]
    except KeyError as exc: raise RendererLayoutError("unsupported production exchange") from exc
    if selected.exchange is not payload.exchange:
        raise RendererLayoutError("layout exchange does not match payload exchange")
    return compose_initial(payload,template_path,output_path,selected,font_provider,
                           DebugOverlayConfig(False))

def render_lab_initial(payload:SignalRenderPayload,template_path:Path|str,output_path:Path|str,
                       font_provider:FontProvider,*,layout:InitialCardLayout|None=None,
                       debug:bool=False,value_overrides:Mapping[str,str]|None=None):
    """Developer-Lab adapter; deliberately absent from the production lifecycle API."""
    selected=layout or LAYOUTS.get(payload.exchange)
    if selected is None or selected.exchange is not payload.exchange:
        raise RendererLayoutError("layout exchange does not match payload exchange")
    return compose_initial(payload,template_path,output_path,selected,font_provider,
                           DebugOverlayConfig(debug),value_overrides=value_overrides)
