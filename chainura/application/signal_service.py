from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping, Protocol

from chainura.domain.destination import Exchange
from chainura.domain.signal import (PriceSourceType, Signal, SignalLifecycleStatus,
    SignalMode, TradeDirection, entry_is_within_tolerance)
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (EntryValidationError, SignalCreationError,
    SignalistCannotCreateSignalError)

@dataclass(frozen=True, slots=True)
class ReferencePriceResult:
    reference_price: Decimal | None
    price_source_type: PriceSourceType
    source_prices: Mapping[Exchange, Decimal]
    healthy: bool
    canonical_symbol: str
    native_symbols: Mapping[Exchange, str]
    failure_reason: str | None = None

class ReferencePriceProvider(Protocol):
    async def get_reference_price(self, *, mode: SignalMode, canonical_symbol: str,
        selected_exchange: Exchange | None, native_symbol: str | None) -> ReferencePriceResult: ...

@dataclass(frozen=True, slots=True)
class CreateSignalCommand:
    signalist_id: int; creation_token: str; mode: SignalMode; canonical_symbol: str
    base_asset: str; quote_asset: str; selected_exchange: Exchange | None
    native_symbol: str | None; direction: TradeDirection; entry_price: Decimal
    leverage: int; take_profits: tuple[Decimal, ...]; stop_loss: Decimal

class SignalService:
    def __init__(self, transactions, reference_prices: ReferencePriceProvider, clock=lambda: datetime.now(timezone.utc)):
        self._transactions=transactions; self._prices=reference_prices; self._clock=clock

    async def validate_entry(self, command: CreateSignalCommand) -> ReferencePriceResult:
        try:
            result=await self._prices.get_reference_price(mode=command.mode,
                canonical_symbol=command.canonical_symbol, selected_exchange=command.selected_exchange,
                native_symbol=command.native_symbol)
        except Exception as exc:
            raise EntryValidationError("reference price is temporarily unavailable") from exc
        if not result.healthy or result.reference_price is None:
            raise EntryValidationError("a healthy current reference price is required")
        if command.mode is SignalMode.COMPOSITE and len(result.source_prices) < 2:
            raise EntryValidationError("composite reference requires at least two sources")
        if not entry_is_within_tolerance(command.entry_price, result.reference_price):
            raise EntryValidationError("entry differs from reference by more than 0.05 percent")
        return result

    async def create(self, command: CreateSignalCommand) -> Signal:
        # This final provider call intentionally occurs before opening the transaction.
        result=await self.validate_entry(command)
        now=self._clock()
        signal=Signal(None,command.signalist_id,command.creation_token,command.mode,
            result.canonical_symbol,command.base_asset,command.quote_asset,command.selected_exchange,
            command.direction,command.entry_price,command.leverage,command.take_profits,
            command.stop_loss,result.reference_price,result.price_source_type,
            SignalLifecycleStatus.ACTIVE,tuple(result.native_symbols.items()),now,now)
        try:
            async with self._transactions() as tx:
                existing=await tx.signals.get_by_creation_token(command.creation_token)
                if existing is not None: return existing
                actor=await tx.signalists.get_by_id_fresh(command.signalist_id)
                if actor is None or actor.status is not SignalistStatus.ACTIVE:
                    raise SignalistCannotCreateSignalError("an active signalist is required")
                created=await tx.signals.add(signal)
                await tx.commit()
                return created
        except (EntryValidationError, SignalistCannotCreateSignalError): raise
        except SignalCreationError: raise
        except Exception as exc: raise SignalCreationError("signal could not be recorded") from exc
