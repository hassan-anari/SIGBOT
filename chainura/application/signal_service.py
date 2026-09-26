from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from chainura.domain.destination import Exchange
from chainura.domain.signal import (PriceSourceType, Signal, SignalLifecycleStatus,
    SignalMode, TradeDirection, initial_entry_drift_is_acceptable,
    MAX_NEW_SIGNAL_LEVERAGE)
from chainura.domain.signalist import SignalistStatus
from chainura.errors import (DuplicateSignalCreationTokenError,
    InconsistentReferencePriceError, InitialEntryDriftExceededError,
    PendingSignalStateError, ReferencePriceUnavailableError, SignalCreationError,
    SignalistCannotCreateSignalError)

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
class SnapshotRequest:
    mode: SignalMode
    canonical_symbol: str
    selected_exchange: Exchange | None
    native_symbol: str | None

@dataclass(frozen=True, slots=True)
class CreatePendingSignalCommand:
    signalist_id: int
    creation_token: str
    mode: SignalMode
    canonical_symbol: str
    base_asset: str
    quote_asset: str
    selected_exchange: Exchange | None
    native_symbol: str | None
    direction: TradeDirection
    leverage: int
    p0: ReferencePriceResult

@dataclass(frozen=True, slots=True)
class FinalizeSignalDetailsCommand:
    signal_id: int
    signalist_id: int
    take_profits: tuple[Decimal, ...]
    stop_loss: Decimal

# Compatibility command for importing/rehydrating the already-accepted Phase-6
# ACTIVE path. Telegram creation no longer constructs this command.
@dataclass(frozen=True, slots=True)
class CreateSignalCommand:
    signalist_id:int;creation_token:str;mode:SignalMode;canonical_symbol:str
    base_asset:str;quote_asset:str;selected_exchange:Exchange|None;native_symbol:str|None
    direction:TradeDirection;entry_price:Decimal;leverage:int
    take_profits:tuple[Decimal,...];stop_loss:Decimal

class SignalService:
    def __init__(self, transactions, reference_prices: ReferencePriceProvider,
                 clock=lambda: datetime.now(timezone.utc), publication_wake=None,
                 monitoring_wake=None, details_publication_wake=None):
        self._transactions=transactions; self._prices=reference_prices; self._clock=clock
        self._publication_wake=publication_wake
        self._monitoring_wake=monitoring_wake
        self._details_publication_wake=details_publication_wake

    async def capture_snapshot(self, request: SnapshotRequest) -> ReferencePriceResult:
        """Fetch one healthy coherent market snapshot (used independently for P0/P1)."""
        try:
            result=await self._prices.get_reference_price(mode=request.mode,
                canonical_symbol=request.canonical_symbol,
                selected_exchange=request.selected_exchange,native_symbol=request.native_symbol)
        except Exception as exc:
            raise ReferencePriceUnavailableError("reference price is temporarily unavailable") from exc
        if not result.healthy or not _positive_decimal(result.reference_price):
            raise ReferencePriceUnavailableError("a healthy current reference price is required")
        self._validate_result(request,result)
        return result

    async def get_by_creation_token(self, creation_token: str, signalist_id: int) -> Signal | None:
        async with self._transactions() as tx:
            signal=await tx.signals.get_by_creation_token(creation_token)
        return signal if signal is not None and signal.signalist_id==signalist_id else None

    async def get_pending_for_owner(self, signal_id: int, signalist_id: int) -> Signal:
        async with self._transactions() as tx: signal=await tx.signals.get_by_id(signal_id)
        if signal is None or signal.signalist_id != signalist_id:
            raise PendingSignalStateError("pending signal is not owned by actor")
        if signal.status is not SignalLifecycleStatus.PENDING_DETAILS:
            raise PendingSignalStateError("signal is not pending details")
        return signal

    async def create_pending(self, command: CreatePendingSignalCommand) -> Signal:
        async with self._transactions() as tx:
            existing=await tx.signals.get_by_creation_token(command.creation_token)
            if existing is not None:return existing
        self._validate_new_leverage(command.leverage)
        self._validate_result(SnapshotRequest(command.mode,command.canonical_symbol,
            command.selected_exchange,command.native_symbol),command.p0)
        # This is deliberately a second provider call. P0 is never replaced.
        p1=await self.capture_snapshot(SnapshotRequest(command.mode,command.canonical_symbol,
            command.selected_exchange,command.native_symbol))
        if not initial_entry_drift_is_acceptable(command.p0.reference_price,p1.reference_price):
            raise InitialEntryDriftExceededError("P0 to P1 drift exceeds 0.70 percent")
        now=self._clock()
        signal=Signal(None,command.signalist_id,command.creation_token,command.mode,
            command.p0.canonical_symbol,command.base_asset,command.quote_asset,
            command.selected_exchange,command.direction,command.p0.reference_price,
            command.leverage,(),None,command.p0.reference_price,
            command.p0.price_source_type,SignalLifecycleStatus.PENDING_DETAILS,
            command.p0.native_symbols,now,now,p1.source_prices)
        try:
            async with self._transactions() as tx:
                existing=await tx.signals.get_by_creation_token(command.creation_token)
                if existing is not None:return existing
                actor=await tx.signalists.get_by_id_fresh(command.signalist_id)
                if actor is None or actor.status is not SignalistStatus.ACTIVE:
                    raise SignalistCannotCreateSignalError("an active signalist is required")
                created=await tx.signals.add(signal);await tx.commit()
        except DuplicateSignalCreationTokenError:
            async with self._transactions() as tx:
                existing=await tx.signals.get_by_creation_token(command.creation_token)
                if existing is not None:return existing
            raise
        if self._publication_wake:self._publication_wake()
        return created

    async def create(self, command: CreateSignalCommand) -> Signal:
        """Compatibility-only creation of complete ACTIVE Phase-6 aggregates."""
        async with self._transactions() as tx:
            existing=await tx.signals.get_by_creation_token(command.creation_token)
            if existing is not None:return existing
        self._validate_new_leverage(command.leverage)
        snapshot=await self.capture_snapshot(SnapshotRequest(command.mode,
            command.canonical_symbol,command.selected_exchange,command.native_symbol))
        now=self._clock()
        signal=Signal(None,command.signalist_id,command.creation_token,command.mode,
            snapshot.canonical_symbol,command.base_asset,command.quote_asset,
            command.selected_exchange,command.direction,command.entry_price,
            command.leverage,command.take_profits,command.stop_loss,
            snapshot.reference_price,snapshot.price_source_type,
            SignalLifecycleStatus.ACTIVE,snapshot.native_symbols,now,now,snapshot.source_prices)
        try:
            async with self._transactions() as tx:
                actor=await tx.signalists.get_by_id_fresh(command.signalist_id)
                if actor is None or actor.status is not SignalistStatus.ACTIVE:
                    raise SignalistCannotCreateSignalError("an active signalist is required")
                created=await tx.signals.add(signal);await tx.commit()
        except DuplicateSignalCreationTokenError:
            async with self._transactions() as tx:
                created=await tx.signals.get_by_creation_token(command.creation_token)
            if created is None:raise
        if self._publication_wake:self._publication_wake()
        return created

    async def finalize_details(self, command: FinalizeSignalDetailsCommand) -> Signal:
        # Repository performs lifecycle CAS and levels insert in one transaction.
        async with self._transactions() as tx:
            current=await tx.signals.get_by_id(command.signal_id)
            if current is None or current.signalist_id != command.signalist_id:
                raise PendingSignalStateError("pending signal is not owned by actor")
            if current.status is not SignalLifecycleStatus.PENDING_DETAILS:
                raise PendingSignalStateError("signal is not pending details")
            candidate=Signal(current.id,current.signalist_id,current.creation_token,current.mode,
                current.canonical_symbol,current.base_asset,current.quote_asset,current.selected_exchange,
                current.direction,current.entry_price,current.leverage,command.take_profits,
                command.stop_loss,current.reference_price,current.price_source_type,
                SignalLifecycleStatus.ACTIVE,current.exchange_symbols,current.created_at,
                self._clock(),current.initial_source_prices)
            activated=await tx.signals.activate_pending(candidate)
            if not activated:raise PendingSignalStateError("signal was already finalized")
            await tx.commit()
        if self._details_publication_wake:self._details_publication_wake()
        if self._monitoring_wake:self._monitoring_wake()
        return candidate

    @staticmethod
    def _validate_new_leverage(leverage: int) -> None:
        # Deliberately enforced only at creation; legacy persisted rows above the
        # current product maximum must remain readable.
        if (isinstance(leverage, bool) or not isinstance(leverage, int)
                or not 1 <= leverage <= MAX_NEW_SIGNAL_LEVERAGE):
            raise SignalCreationError("new signal leverage must be between 1 and 50")

    @staticmethod
    def _validate_result(request: SnapshotRequest, result: ReferencePriceResult) -> None:
        prices=result.source_prices;mappings=result.native_symbols
        if not isinstance(prices,tuple) or not isinstance(mappings,tuple):
            raise InconsistentReferencePriceError("reference result must be immutable")
        exchanges=tuple(x[0] for x in prices);mapped=tuple(x[0] for x in mappings)
        if (len(set(exchanges))!=len(exchanges) or any(not isinstance(e,Exchange) or not _positive_decimal(p) for e,p in prices)
            or len(set(mapped))!=len(mapped) or any(not isinstance(e,Exchange) or not isinstance(s,str) or not s or not s.isascii() for e,s in mappings)
            or result.canonical_symbol!=request.canonical_symbol):
            raise InconsistentReferencePriceError("reference result is inconsistent")
        if request.mode is SignalMode.COMPOSITE:
            if (request.selected_exchange is not None or result.price_source_type is not PriceSourceType.COMPOSITE
                or len(prices)<2 or set(mapped)!=set(Exchange) or not set(exchanges).issubset(set(mapped))):
                raise InconsistentReferencePriceError("composite reference result is inconsistent")
        else:
            expected={Exchange.OURBIT:PriceSourceType.OURBIT_ONLY,Exchange.TOOBIT:PriceSourceType.TOOBIT_ONLY,Exchange.LBANK:PriceSourceType.LBANK_ONLY}
            selected=request.selected_exchange
            if (selected not in expected or result.price_source_type is not expected[selected]
                or exchanges!=(selected,) or mapped!=(selected,) or request.native_symbol!=mappings[0][1]):
                raise InconsistentReferencePriceError("single-exchange reference result is inconsistent")

def _positive_decimal(value):
    return isinstance(value,Decimal) and value.is_finite() and value>0
