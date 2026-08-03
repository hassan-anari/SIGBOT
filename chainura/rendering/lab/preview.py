import importlib
from pathlib import Path
from chainura.domain.destination import Exchange
from chainura.rendering.layouts.models import DebugOverlayConfig, InitialCardLayout
from chainura.rendering.models import SignalRenderPayload
from chainura.rendering.pillow.compositor import compose_initial
from chainura.rendering.pillow.errors import RendererLayoutError
from chainura.rendering.pillow.text import FontProvider
from typing import Mapping

_LAYOUT_MODULES = {
    Exchange.OURBIT: "chainura.rendering.layouts.ourbit",
    Exchange.TOOBIT: "chainura.rendering.layouts.toobit",
    Exchange.LBANK: "chainura.rendering.layouts.lbank",
}


def load_exchange_layout(exchange: Exchange, *, reload: bool = False):
    try:
        module = importlib.import_module(_LAYOUT_MODULES[exchange])
    except KeyError as exc:
        raise RendererLayoutError(f"unsupported layout exchange: {exchange!r}") from exc
    if reload:
        importlib.invalidate_caches()
        cached = getattr(module, "__cached__", None)
        if cached:
            try: Path(cached).unlink()
            except FileNotFoundError: pass
        module = importlib.reload(module)
    layout = getattr(module, "LAYOUT", None)
    if not isinstance(layout, InitialCardLayout):
        raise RendererLayoutError(f"{exchange.value} layout module must define an InitialCardLayout named LAYOUT")
    if layout.exchange is not exchange:
        raise RendererLayoutError(f"{exchange.value} layout has the wrong exchange identity")
    return layout


def _render_expected(
    expected,
    payload,
    template_path,
    output_path,
    font_provider,
    *,
    debug=False,
    layout=None,
    value_overrides: Mapping[str, str] | None = None,
):
    if payload.exchange is not expected:
        raise RendererLayoutError(
            f"{expected.value} renderer cannot render "
            f"{payload.exchange.value} payload"
        )

    return compose_initial(
        payload,
        template_path,
        output_path,
        layout or load_exchange_layout(expected),
        font_provider,
        DebugOverlayConfig(debug),
        value_overrides=value_overrides,
    )

def render_ourbit_initial(
    payload: SignalRenderPayload,
    template_path: Path | str,
    output_path: Path | str,
    font_provider: FontProvider,
    *,
    debug: bool = False,
    value_overrides: Mapping[str, str] | None = None,
):
    return _render_expected(
        Exchange.OURBIT,
        payload,
        template_path,
        output_path,
        font_provider,
        debug=debug,
        value_overrides=value_overrides,
    )


def render_toobit_initial(
    payload: SignalRenderPayload,
    template_path: Path | str,
    output_path: Path | str,
    font_provider: FontProvider,
    *,
    debug: bool = False,
    value_overrides: Mapping[str, str] | None = None,
):
    return _render_expected(
        Exchange.TOOBIT,
        payload,
        template_path,
        output_path,
        font_provider,
        debug=debug,
        value_overrides=value_overrides,
    )


def render_lbank_initial(
    payload: SignalRenderPayload,
    template_path: Path | str,
    output_path: Path | str,
    font_provider: FontProvider,
    *,
    debug: bool = False,
    value_overrides: Mapping[str, str] | None = None,
):
    return _render_expected(
        Exchange.LBANK,
        payload,
        template_path,
        output_path,
        font_provider,
        debug=debug,
        value_overrides=value_overrides,
    )

RENDERERS={Exchange.OURBIT:render_ourbit_initial,Exchange.TOOBIT:render_toobit_initial,Exchange.LBANK:render_lbank_initial}

def render_preview(
    payload,
    template,
    output,
    font_provider,
    debug=False,
    *,
    layout=None,
    value_overrides: Mapping[str, str] | None = None,
):
    if layout is None:
        return RENDERERS[payload.exchange](
            payload,
            template,
            output,
            font_provider,
            debug=debug,
            value_overrides=value_overrides,
        )

    return _render_expected(
        payload.exchange,
        payload,
        template,
        output,
        font_provider,
        debug=debug,
        layout=layout,
        value_overrides=value_overrides,
    )
