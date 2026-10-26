from __future__ import annotations

from dataclasses import dataclass
from html import escape
from html.parser import HTMLParser
from string import Formatter

from chainura.domain.message_template import DestinationMessageKind, MessageTemplateSource
from chainura.errors import CaptionGenerationError
from chainura.domain.signal import TradeDirection
from chainura.telegram.presentation.formatting import (EXCHANGE_NAMES, format_decimal,
    format_leverage, format_percentage, format_symbol)
from chainura.trade_id import format_trade_id


BUILTIN_TEMPLATES = {
    DestinationMessageKind.INITIAL_PHOTO_CAPTION: "",
    DestinationMessageKind.SIGNAL_DETAILS: (
        "📊 <b>سیگنال {symbol}</b>\n📈 جهت: <b>{direction}</b>\n🎯 ورود: {entry}\n"
        "⚡️ اهرم: {leverage}\n\nاهداف:\n{targets}\n\n🛑 حد ضرر: {stop_loss}\n\n{private_tag}"
    ),
    DestinationMessageKind.TP_HIT: "🎯 <b>تارگت {tp_index} {symbol} لمس شد</b>",
    DestinationMessageKind.SL_HIT: "🛑 <b>حد ضرر {symbol} فعال شد</b>",
    DestinationMessageKind.BREAKEVEN: "⚖️ <b>سیگنال {symbol} در بریک‌ایون بسته شد</b>",
    DestinationMessageKind.SIGNAL_CANCELLED: "❌ <b>سیگنال {symbol} کنسل شد</b>",
    DestinationMessageKind.WARMUP: "🔥 <b>آماده باشید</b>\n\nبه‌زودی سیگنال جدید منتشر می‌شود.",
    DestinationMessageKind.RISK_FREE_ACTIVATED: "⚖️ <b>ریسک فری فعال شد</b>\nحد ضرر به نقطه ورود منتقل شد.",
    DestinationMessageKind.SAVE_PROFIT_50: "💰 <b>سیو سود</b>\n۵۰٪ از حجم باقی‌مانده پوزیشن بسته شد.",
    DestinationMessageKind.FULL_CLOSE: "⛔ <b>پوزیشن به‌طور کامل بسته شد.</b>",
    DestinationMessageKind.RISK_FREE_HIT: "⚖️ <b>ریسک فری فعال شد و باقی‌مانده پوزیشن در نقطه ورود بسته شد.</b>",
    DestinationMessageKind.LIMIT_PENDING: (
        "⏳ <b>سفارش لیمیت در انتظار رسیدن قیمت</b>\n\n"
        "شناسه معامله: {trade_id}\nنماد: {symbol}\nجهت: {direction}\n"
        "ورود لیمیت: {entry}\nاهرم: {leverage}\nاهداف: {targets}\nحد ضرر: {stop_loss}"
    ),
    DestinationMessageKind.LIMIT_CANCELLED: (
        "❌ <b>سفارش لیمیت لغو شد</b>\n"
        "شناسه معامله: {trade_id}\nنماد: {symbol}"
    ),
}

PLACEHOLDERS = {
    DestinationMessageKind.INITIAL_PHOTO_CAPTION: frozenset(("symbol", "direction", "entry", "leverage", "last_price", "pnl", "exchange", "private_tag")),
    DestinationMessageKind.SIGNAL_DETAILS: frozenset(("symbol", "direction", "entry", "leverage", "targets", "stop_loss", "private_tag")),
    DestinationMessageKind.TP_HIT: frozenset(("symbol", "direction", "entry", "leverage", "tp_index", "tp_price", "execution_price", "roi", "exchange", "private_tag")),
    DestinationMessageKind.SL_HIT: frozenset(("symbol", "direction", "entry", "leverage", "stop_loss", "execution_price", "roi", "exchange", "private_tag")),
    DestinationMessageKind.BREAKEVEN: frozenset(("symbol", "direction", "entry", "leverage", "execution_price", "roi", "exchange", "private_tag")),
    DestinationMessageKind.SIGNAL_CANCELLED: frozenset(("symbol", "direction", "entry", "leverage", "private_tag")),
    DestinationMessageKind.WARMUP: frozenset(),
    DestinationMessageKind.LIMIT_PENDING: frozenset(("trade_id", "symbol", "direction", "entry", "leverage", "targets", "stop_loss", "private_tag")),
    DestinationMessageKind.LIMIT_CANCELLED: frozenset(("trade_id", "symbol", "direction", "entry", "leverage", "private_tag")),
}
_ACTION_FIELDS=frozenset(("symbol","direction","entry","leverage","tp_index",
    "execution_price","remaining_fraction","realized_weighted_roi","private_tag"))
PLACEHOLDERS.update({kind:_ACTION_FIELDS for kind in (
    DestinationMessageKind.RISK_FREE_ACTIVATED,DestinationMessageKind.SAVE_PROFIT_50,
    DestinationMessageKind.FULL_CLOSE,DestinationMessageKind.RISK_FREE_HIT)})

PHOTO_KINDS = frozenset((DestinationMessageKind.INITIAL_PHOTO_CAPTION, DestinationMessageKind.TP_HIT, DestinationMessageKind.BREAKEVEN))


class MessageTemplateValidationError(CaptionGenerationError):
    pass


class _MarkupValidator(HTMLParser):
    allowed = frozenset(("b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre"))

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.allowed or attrs:
            raise MessageTemplateValidationError("unsupported HTML markup")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise MessageTemplateValidationError("malformed HTML markup")

    def handle_startendtag(self, tag, attrs):
        raise MessageTemplateValidationError("unsupported HTML markup")

    def close(self):
        super().close()
        if self.stack:
            raise MessageTemplateValidationError("malformed HTML markup")


def validate_template(kind: DestinationMessageKind, template: str) -> tuple[str, ...]:
    if not isinstance(kind, DestinationMessageKind):
        raise MessageTemplateValidationError("unsupported message kind")
    if not isinstance(template, str) or not template.strip():
        raise MessageTemplateValidationError("template must not be empty")
    fields = []
    try:
        parsed = tuple(Formatter().parse(template))
    except ValueError as exc:
        raise MessageTemplateValidationError("malformed placeholder braces") from exc
    for _, field, spec, conversion in parsed:
        if field is None:
            continue
        if not field or spec or conversion is not None or field not in PLACEHOLDERS[kind]:
            raise MessageTemplateValidationError("unsupported placeholder")
        fields.append(field)
    validator = _MarkupValidator()
    try:
        validator.feed(template)
        validator.close()
    except (ValueError, MessageTemplateValidationError) as exc:
        raise MessageTemplateValidationError("invalid Telegram HTML") from exc
    return tuple(fields)


def render_template(kind: DestinationMessageKind, template: str, context: dict[str, object]) -> str:
    fields = validate_template(kind, template)
    missing = set(fields).difference(context)
    if missing:
        raise MessageTemplateValidationError("template context is incomplete")
    escaped = {name: (str(context[name]) if isinstance(context[name], SafeDynamicValue)
                      else escape(str(context[name]), quote=False)) for name in set(fields)}
    # Validation guarantees plain identifiers, so format_map cannot traverse objects.
    rendered = template.format_map(escaped)
    rendered = "\n".join(line.rstrip() for line in rendered.splitlines()).strip()
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
    if not rendered:
        raise MessageTemplateValidationError("rendered template is empty")
    limit = 1024 if kind in PHOTO_KINDS else 4096
    if len(rendered) > limit:
        raise MessageTemplateValidationError("rendered template exceeds Telegram limit")
    return rendered


class SafeDynamicValue(str):
    """Application-produced, already escaped Telegram HTML (never user input)."""


def _safe(value): return SafeDynamicValue(value)

def _direction(signal): return "لانگ" if signal.direction is TradeDirection.LONG else "شورت"
def _private(private): return "#پیامک" if private else ""
def _exchange(exchange): return EXCHANGE_NAMES[exchange.value]

def initial_context(signal, exchange, *, private):
    prices = dict(signal.initial_source_prices)
    last_price = prices[exchange]
    change = ((last_price - signal.entry_price) / signal.entry_price * 100)
    if signal.direction is TradeDirection.SHORT: change = -change
    pnl = change * signal.leverage
    return {"symbol": _safe(format_symbol(signal.canonical_symbol)), "direction": _direction(signal),
            "entry": _safe(format_decimal(signal.entry_price)), "leverage": _safe(format_leverage(signal.leverage)),
            "last_price": _safe(format_decimal(last_price)), "pnl": _safe(format_percentage(pnl.quantize(__import__('decimal').Decimal('0.01')))),
            "exchange": _exchange(exchange), "private_tag": _private(private)}

def details_context(signal, *, private):
    if not signal.take_profits or signal.stop_loss is None:
        raise MessageTemplateValidationError("active signal details are incomplete")
    markers = ("1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟")
    targets = "\n".join(f"{markers[i-1] if i <= len(markers) else f'{i}.'} {format_decimal(price)}"
                        for i, price in enumerate(signal.take_profits, 1))
    return {"symbol": _safe(format_symbol(signal.canonical_symbol)), "direction": _direction(signal),
            "entry": _safe(format_decimal(signal.entry_price)), "leverage": _safe(format_leverage(signal.leverage)),
            "targets": _safe(targets), "stop_loss": _safe(format_decimal(signal.stop_loss)),
            "private_tag": _private(private)}

def event_context(signal, event, exchange, *, private):
    context = {"symbol": _safe(format_symbol(signal.canonical_symbol)), "direction": _direction(signal),
        "entry": _safe(format_decimal(signal.entry_price)), "leverage": _safe(format_leverage(signal.leverage)),
        "execution_price": _safe(format_decimal(event.roi_basis_price)),
        "roi": _safe(format_percentage(event.leveraged_roi)), "exchange": _exchange(exchange),
        "private_tag": _private(private)}
    if event.tp_index is not None:
        context.update(tp_index=_safe(format_symbol(str(event.tp_index))), tp_price=_safe(format_decimal(event.threshold_price)))
    if signal.stop_loss is not None: context["stop_loss"] = _safe(format_decimal(signal.stop_loss))
    return context

def cancellation_context(signal, *, private):
    return {"symbol":_safe(format_symbol(signal.canonical_symbol)),"direction":_direction(signal),
        "entry":_safe(format_decimal(signal.entry_price)),"leverage":_safe(format_leverage(signal.leverage)),
        "private_tag":_private(private)}

def limit_pending_context(signal, *, private):
    context=details_context(signal,private=private)
    context["trade_id"]=_safe(format_symbol(format_trade_id(signal.id)))
    return context

def limit_cancellation_context(signal, *, private):
    context=cancellation_context(signal,private=private)
    context["trade_id"]=_safe(format_symbol(format_trade_id(signal.id)))
    return context

def position_action_context(signal,event,action,*,private):
    return {"symbol":_safe(format_symbol(signal.canonical_symbol)),"direction":_direction(signal),
        "entry":_safe(format_decimal(signal.entry_price)),"leverage":_safe(format_leverage(signal.leverage)),
        "tp_index":_safe(format_symbol(str(event.tp_index)) if event.tp_index is not None else "—"),
        "execution_price":_safe(format_decimal(action.execution_price)),
        "remaining_fraction":_safe(format_percentage(action.remaining_fraction_after*100)),
        "realized_weighted_roi":_safe(format_percentage(action.realized_weighted_roi_after)),
        "private_tag":_private(private)}


SAMPLE_CONTEXT = {name: value for name, value in (
    ("symbol", "⁦BTCUSDT⁩"), ("direction", "لانگ"), ("entry", "⁦64137⁩"),
    ("leverage", "⁦10x⁩"), ("last_price", "⁦64200⁩"), ("pnl", "⁦0.98%⁩"),
    ("exchange", "Toobit"), ("private_tag", "#پیامک"),
    ("targets", "1️⃣ ⁦65000⁩\n2️⃣ ⁦66000⁩\n3️⃣ ⁦67000⁩"),
    ("stop_loss", "⁦63000⁩"), ("tp_index", "⁦2⁩"), ("tp_price", "⁦66000⁩"),
    ("execution_price", "⁦66000⁩"), ("roi", "⁦28.10%⁩"),
    ("remaining_fraction", "⁦50%⁩"), ("realized_weighted_roi", "⁦10%⁩"),
    ("trade_id", "⁦TR-000123⁩"),
)}


@dataclass(frozen=True, slots=True)
class ResolvedMessageTemplate:
    text: str
    source: MessageTemplateSource
    template_text: str


class DestinationMessageTemplateService:
    def __init__(self, transactions):
        self.transactions = transactions

    async def _template(self, kind, destination_id):
        async with self.transactions() as tx:
            destination = await tx.message_templates.get_destination(kind, destination_id) if destination_id is not None else None
            global_text = None if destination is not None else await tx.message_templates.get_global(kind)
            await tx.commit()
        if destination is not None:
            return destination, MessageTemplateSource.DESTINATION
        if global_text is not None:
            return global_text, MessageTemplateSource.GLOBAL
        return BUILTIN_TEMPLATES[kind], MessageTemplateSource.BUILTIN

    async def resolve(self, kind, *, destination_id, context):
        template, source = await self._template(kind, destination_id)
        if (kind is DestinationMessageKind.INITIAL_PHOTO_CAPTION
                and source is MessageTemplateSource.BUILTIN and template == ""):
            return ResolvedMessageTemplate("", source, template)
        return ResolvedMessageTemplate(render_template(kind, template, context), source, template)

    async def effective_template(self, kind, destination_id=None):
        template, source = await self._template(kind, destination_id)
        return template, source

    def preview(self, kind, template):
        return render_template(kind, template, SAMPLE_CONTEXT)

    async def set_global(self, kind, text):
        self.preview(kind, text)
        async with self.transactions() as tx:
            await tx.message_templates.set_global(kind, text); await tx.commit()

    async def set_destination(self, kind, destination_id, text):
        self.preview(kind, text)
        async with self.transactions() as tx:
            await tx.message_templates.set_destination(kind, destination_id, text); await tx.commit()

    async def remove_global(self, kind):
        async with self.transactions() as tx:
            await tx.message_templates.remove_global(kind); await tx.commit()

    async def remove_destination(self, kind, destination_id):
        async with self.transactions() as tx:
            await tx.message_templates.remove_destination(kind, destination_id); await tx.commit()
