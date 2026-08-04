from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from chainura.domain.destination import Exchange
from chainura.errors import RenderingConfigurationError, RenderingLifecycleError
from chainura.rendering.core import render_initial
from chainura.rendering.pillow.errors import RendererAssetError
from chainura.rendering.pillow.text import RoleFontProvider, FontProvider
from chainura.rendering.signal_image import register_signal_renderer, unregister_signal_renderer

FONT_ROLES=("regular","medium","semibold","bold")
MAX_RENDER_CONCURRENCY=32
@dataclass(frozen=True,slots=True)
class RenderingSettings:
    template_asset_root:Path; output_root:Path; toobit_logo_root:Path
    regular_font_path:Path; medium_font_path:Path; semibold_font_path:Path; bold_font_path:Path
    concurrency:int
    @classmethod
    def from_mapping(cls,m):
        names=("RENDER_TEMPLATE_ASSET_ROOT","RENDER_OUTPUT_ROOT","TOOBIT_COIN_LOGO_ROOT",
               "RENDER_FONT_REGULAR_PATH","RENDER_FONT_MEDIUM_PATH","RENDER_FONT_SEMIBOLD_PATH","RENDER_FONT_BOLD_PATH")
        try: values=[Path(m[n].strip()) for n in names]; concurrency=int(m["RENDER_CONCURRENCY"])
        except (KeyError,ValueError,AttributeError) as exc: raise RenderingConfigurationError("rendering configuration is incomplete or invalid") from exc
        if concurrency<=0 or concurrency>MAX_RENDER_CONCURRENCY: raise RenderingConfigurationError("RENDER_CONCURRENCY must be between 1 and 32")
        if any(not str(v) or str(v)=="." for v in values): raise RenderingConfigurationError("rendering paths must be non-empty")
        return cls(*values,concurrency)

class ProductionRendererRegistration:
    _guard=RLock(); _active=None
    def __init__(self,settings:RenderingSettings,font_provider:FontProvider|None=None):
        self.settings=settings; self._provider=font_provider; self._started=False
    def start(self):
        with self._guard:
            if self._started or self.__class__._active is not None: raise RenderingLifecycleError("production renderers are already configured")
            try:
                provider=self._provider or RoleFontProvider(role_paths=dict(zip(FONT_ROLES,(self.settings.regular_font_path,self.settings.medium_font_path,self.settings.semibold_font_path,self.settings.bold_font_path))))
                self.settings.template_asset_root.mkdir(parents=True,exist_ok=True); self.settings.output_root.mkdir(parents=True,exist_ok=True)
            except (OSError,RendererAssetError) as exc: raise RenderingConfigurationError("production renderer assets are invalid") from exc
            registered=[]
            try:
                for exchange in Exchange:
                    register_signal_renderer(exchange,lambda p,t,o,provider=provider:render_initial(p,t,o,provider));registered.append(exchange)
            except Exception:
                for exchange in registered:unregister_signal_renderer(exchange)
                raise
            self._started=True; self.__class__._active=self
        return self
    def close(self):
        with self._guard:
            if self._started:
                for exchange in Exchange: unregister_signal_renderer(exchange)
                self._started=False
                if self.__class__._active is self:self.__class__._active=None
    def __enter__(self): return self.start()
    def __exit__(self,*_): self.close()
