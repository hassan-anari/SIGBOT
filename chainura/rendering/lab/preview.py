from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.rendering.layouts.lbank import LAYOUT as LBANK_LAYOUT
from chainura.rendering.layouts.ourbit import LAYOUT as OURBIT_LAYOUT
from chainura.rendering.layouts.toobit import LAYOUT as TOOBIT_LAYOUT
from chainura.rendering.layouts.models import DebugOverlayConfig
from chainura.rendering.models import SignalRenderPayload
from chainura.rendering.pillow.compositor import compose_initial
from chainura.rendering.pillow.text import FontProvider

def render_ourbit_initial(payload:SignalRenderPayload,template_path:Path|str,output_path:Path|str,font_provider:FontProvider,*,debug:bool=False):
    return compose_initial(payload,template_path,output_path,OURBIT_LAYOUT,font_provider,DebugOverlayConfig(debug))
def render_toobit_initial(payload:SignalRenderPayload,template_path:Path|str,output_path:Path|str,font_provider:FontProvider,*,debug:bool=False):
    return compose_initial(payload,template_path,output_path,TOOBIT_LAYOUT,font_provider,DebugOverlayConfig(debug))
def render_lbank_initial(payload:SignalRenderPayload,template_path:Path|str,output_path:Path|str,font_provider:FontProvider,*,debug:bool=False):
    return compose_initial(payload,template_path,output_path,LBANK_LAYOUT,font_provider,DebugOverlayConfig(debug))

RENDERERS={Exchange.OURBIT:render_ourbit_initial,Exchange.TOOBIT:render_toobit_initial,Exchange.LBANK:render_lbank_initial}

def render_preview(payload,template,output,font_provider,debug=False):
    return RENDERERS[payload.exchange](payload,template,output,font_provider,debug=debug)
