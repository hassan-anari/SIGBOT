from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from chainura.domain.destination import Exchange
from chainura.domain.signal import (PriceSourceType, Signal, SignalLifecycleStatus,
    SignalMode, TradeDirection, entry_is_within_tolerance)
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (DuplicateSignalCreationTokenError, EntryOutsideToleranceError,
    EntryValidationError, InconsistentReferencePriceError, ReferencePriceUnavailableError,
    SignalCreationError, SignalistCannotCreateSignalError)

@dataclass(frozen=True, slots=True)
class ReferencePriceResult:
    reference_price: Decimal | None
    price_source_type: PriceSourceType
    source_prices: tuple[tuple[Exchange, Decimal], ...]
    healthy: bool
    canonical_symbol: str
    native_symbols: tuple[tuple[Exchange, str], ...]
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

@dataclass(frozen=True, slots=True)
class EntryValidationRequest:
    mode: SignalMode
    canonical_symbol: str
    selected_exchange: Exchange | None
    native_symbol: str | None
    entry_price: Decimal

class SignalService:
    def __init__(self, transactions, reference_prices: ReferencePriceProvider, clock=lambda: datetime.now(timezone.utc)):
        self._transactions=transactions; self._prices=reference_prices; self._clock=clock

    async def validate_entry(self, request: EntryValidationRequest | CreateSignalCommand) -> ReferencePriceResult:
        validation = (request if isinstance(request, EntryValidationRequest) else
                      EntryValidationRequest(request.mode, request.canonical_symbol,
                          request.selected_exchange, request.native_symbol,
                          request.entry_price))
        try:
            result=await self._prices.get_reference_price(mode=validation.mode,
                canonical_symbol=validation.canonical_symbol, selected_exchange=validation.selected_exchange,
                native_symbol=validation.native_symbol)
        except Exception as exc:
            raise ReferencePriceUnavailableError("reference price is temporarily unavailable") from exc
        if not result.healthy or not _positive_decimal(result.reference_price):
            raise ReferencePriceUnavailableError("a healthy current reference price is required")
        self._validate_result(validation, result)
        if not entry_is_within_tolerance(validation.entry_price, result.reference_price):
            raise EntryOutsideToleranceError("entry differs from reference by more than 0.05 percent")
        return result

    async def get_by_creation_token(self, creation_token: str,
                                    signalist_id: int) -> Signal | None:
        """Read an idempotent result without accessing market providers."""
        try:
            async with self._transactions() as tx:
                signal = await tx.signals.get_by_creation_token(creation_token)
        except SignalCreationError:
            raise
        except Exception as exc:
            raise SignalCreationError("signal could not be read") from exc
        if signal is None or signal.signalist_id != signalist_id:
            return None
        return signal

    @staticmethod
    def _validate_result(command: EntryValidationRequest | CreateSignalCommand, result: ReferencePriceResult) -> None:
        prices = result.source_prices
        mappings = result.native_symbols
        if not isinstance(prices, tuple) or not isinstance(mappings, tuple):
            raise InconsistentReferencePriceError("reference result must be immutable")
        exchanges = tuple(item[0] for item in prices)
        mapping_exchanges = tuple(item[0] for item in mappings)
        supported = set(Exchange)
        if (len(set(exchanges)) != len(exchanges) or any(e not in supported or not _positive_decimal(p) for e,p in prices)
                or len(set(mapping_exchanges)) != len(mapping_exchanges)
                or any(e not in supported or not isinstance(s,str) or not s or not s.isascii() for e,s in mappings)
                or not isinstance(result.canonical_symbol,str) or not result.canonical_symbol or not result.canonical_symbol.isascii()):
            raise InconsistentReferencePriceError("reference result is inconsistent")
        if command.mode is SignalMode.COMPOSITE:
            if (command.selected_exchange is not None or result.price_source_type is not PriceSourceType.COMPOSITE
                    or result.canonical_symbol != command.canonical_symbol
                    or len(prices) < 2 or not set(exchanges).issubset(set(mapping_exchanges))
                    or set(mapping_exchanges) != set(Exchange)):
                raise InconsistentReferencePriceError("composite reference result is inconsistent")
        else:
            expected = {Exchange.OURBIT:PriceSourceType.OURBIT_ONLY, Exchange.TOOBIT:PriceSourceType.TOOBIT_ONLY, Exchange.LBANK:PriceSourceType.LBANK_ONLY}
            selected = command.selected_exchange
            if (selected not in expected or result.price_source_type is not expected[selected]
                    or result.canonical_symbol != command.canonical_symbol
                    or exchanges != (selected,) or mapping_exchanges != (selected,)
                    or command.native_symbol != mappings[0][1]):
                raise InconsistentReferencePriceError("single-exchange reference result is inconsistent")

    async def create(self, command: CreateSignalCommand) -> Signal:
        # A successful retry is independent of current market availability.
        async with self._transactions() as lookup:
            existing = await lookup.signals.get_by_creation_token(command.creation_token)
            if existing is not None:
                return existing
        # This final provider call intentionally occurs before the write transaction.
        result=await self.validate_entry(command)
        return await self.create_validated(command, result)

    async def create_validated(self, command: CreateSignalCommand,
                               result: ReferencePriceResult) -> Signal:
        """Persist a just-validated result; callers must stale-check after their await."""
        if result.healthy is not True or not _positive_decimal(result.reference_price):
            raise ReferencePriceUnavailableError(
                "a healthy current reference price is required")
        self._validate_result(command, result)
        if not entry_is_within_tolerance(command.entry_price, result.reference_price):
            raise EntryOutsideToleranceError("entry differs from reference by more than 0.05 percent")
        async with self._transactions() as lookup:
            existing = await lookup.signals.get_by_creation_token(command.creation_token)
            if existing is not None:
                return existing
        now=self._clock()
        signal=Signal(None,command.signalist_id,command.creation_token,command.mode,
            result.canonical_symbol,command.base_asset,command.quote_asset,command.selected_exchange,
            command.direction,command.entry_price,command.leverage,command.take_profits,
            command.stop_loss,result.reference_price,result.price_source_type,
            SignalLifecycleStatus.ACTIVE,result.native_symbols,now,now)
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
        except DuplicateSignalCreationTokenError:
            # The failed transaction has closed/rolled back. Reload in a fresh one.
            async with self._transactions() as retry:
                existing = await retry.signals.get_by_creation_token(command.creation_token)
                if existing is not None:
                    return existing
            raise SignalCreationError("idempotent signal could not be reloaded")
        except (EntryValidationError, SignalistCannotCreateSignalError): raise
        except SignalCreationError: raise
        except Exception as exc: raise SignalCreationError("signal could not be recorded") from exc


def _positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0
