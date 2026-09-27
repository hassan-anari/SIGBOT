from decimal import Decimal
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from chainura.domain.signal import Signal
from chainura.errors import DuplicateSignalCreationTokenError, SignalCreationError
from chainura.persistence.models import (SignalExchangeSymbolRecord, SignalRecord,
                                          SignalTakeProfitRecord, SignalInitialPriceRecord,
                                          InitialPublicationRequestRecord,
                                          SignalMonitorStateRecord, DestinationPublicationRecord,
                                          SignalDetailsPublicationRecord, SignalPositionStateRecord)
from chainura.domain.signal import SignalLifecycleStatus
from chainura.domain.publication import PublicationStatus
from chainura.domain.monitoring import MonitorStage

class SqlAlchemySignalRepository:
    def __init__(self, session): self._session = session

    async def get_by_creation_token(self, token: str):
        try:
            row = (await self._session.scalars(select(SignalRecord).where(SignalRecord.creation_token == token))).one_or_none()
            return await self._load(row) if row else None
        except SignalCreationError: raise
        except Exception as exc: raise SignalCreationError("signal storage is unavailable") from exc

    async def get_by_id(self, signal_id: int):
        try:
            row = await self._session.get(SignalRecord, signal_id)
            return await self._load(row) if row else None
        except SignalCreationError: raise
        except Exception as exc: raise SignalCreationError("signal storage is unavailable") from exc

    async def add(self, signal: Signal):
        row = SignalRecord(signalist_id=signal.signalist_id, creation_token=signal.creation_token,
            mode=signal.mode, canonical_symbol=signal.canonical_symbol, base_asset=signal.base_asset,
            quote_asset=signal.quote_asset, selected_exchange=signal.selected_exchange,
            direction=signal.direction, entry_price=signal.entry_price, leverage=signal.leverage,
            stop_loss=signal.stop_loss, reference_price=signal.reference_price,
            price_source_type=signal.price_source_type, status=signal.status,
            created_at=signal.created_at, updated_at=signal.updated_at,cancelled_at=signal.cancelled_at)
        self._session.add(row)
        try:
            await self._session.flush()
            self._session.add_all([SignalTakeProfitRecord(signal_id=row.id, position=i, price=p) for i,p in enumerate(signal.take_profits,1)])
            self._session.add_all([SignalExchangeSymbolRecord(signal_id=row.id, exchange=e, native_symbol=s) for e,s in signal.exchange_symbols])
            self._session.add_all([SignalInitialPriceRecord(signal_id=row.id, exchange=e, price=p) for e,p in signal.initial_source_prices])
            self._session.add(InitialPublicationRequestRecord(signal_id=row.id))
            if signal.status is SignalLifecycleStatus.ACTIVE:
                self._session.add(SignalMonitorStateRecord(signal_id=row.id,
                    stage=MonitorStage.PRE_TP1,created_at=signal.created_at,
                    updated_at=signal.created_at))
                self._session.add(SignalPositionStateRecord(signal_id=row.id,
                    remaining_fraction=Decimal('1'),
                    realized_weighted_roi=Decimal('0'),
                    risk_free_used=False,risk_free_active=False,
                    save_profit_50_used=False,effective_stop_price=signal.stop_loss,
                    is_closed=False,version=1,created_at=signal.created_at,
                    updated_at=signal.created_at))
            await self._session.flush()
        except IntegrityError as exc:
            raw = str(exc.orig)
            constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_signals_creation_token" or "signals.creation_token" in raw:
                raise DuplicateSignalCreationTokenError("creation token already exists") from exc
            raise SignalCreationError("signal could not be recorded") from exc
        except SQLAlchemyError as exc:
            raise SignalCreationError("signal could not be recorded") from exc
        return Signal(row.id, signal.signalist_id, signal.creation_token, signal.mode, signal.canonical_symbol,
            signal.base_asset, signal.quote_asset, signal.selected_exchange, signal.direction,
            signal.entry_price, signal.leverage, signal.take_profits, signal.stop_loss,
            signal.reference_price, signal.price_source_type, signal.status, signal.exchange_symbols,
            signal.created_at, signal.updated_at, signal.initial_source_prices,signal.cancelled_at)

    async def activate_pending(self, signal: Signal) -> bool:
        changed=(await self._session.execute(update(SignalRecord).where(
            SignalRecord.id==signal.id,
            SignalRecord.status==SignalLifecycleStatus.PENDING_DETAILS).values(
            status=SignalLifecycleStatus.ACTIVE,stop_loss=signal.stop_loss,
            updated_at=signal.updated_at))).rowcount
        if changed != 1:return False
        self._session.add_all([SignalTakeProfitRecord(signal_id=signal.id,position=i,price=p)
                               for i,p in enumerate(signal.take_profits,1)])
        self._session.add(SignalMonitorStateRecord(signal_id=signal.id,
            stage=MonitorStage.PRE_TP1,created_at=signal.updated_at,updated_at=signal.updated_at))
        self._session.add(SignalPositionStateRecord(signal_id=signal.id,
            remaining_fraction=Decimal('1'),
            realized_weighted_roi=Decimal('0'),
            risk_free_used=False,risk_free_active=False,save_profit_50_used=False,
            effective_stop_price=signal.stop_loss,is_closed=False,version=1,
            created_at=signal.updated_at,updated_at=signal.updated_at))
        # Only destinations with an actually-sent initial photo receive details.
        initial=(await self._session.execute(select(
            DestinationPublicationRecord.destination_id,
            DestinationPublicationRecord.telegram_message_id).where(
            DestinationPublicationRecord.signal_id==signal.id,
            DestinationPublicationRecord.status==PublicationStatus.SENT))).all()
        self._session.add_all([SignalDetailsPublicationRecord(signal_id=signal.id,
            destination_id=destination_id,status=PublicationStatus.PENDING,
            reply_to_message_id=message_id,created_at=signal.updated_at,
            updated_at=signal.updated_at) for destination_id,message_id in initial])
        await self._session.flush()
        return True

    async def _load(self, row):
        try:
            tps = tuple((await self._session.scalars(select(SignalTakeProfitRecord).where(SignalTakeProfitRecord.signal_id == row.id).order_by(SignalTakeProfitRecord.position))).all())
            maps = tuple((await self._session.scalars(select(SignalExchangeSymbolRecord).where(SignalExchangeSymbolRecord.signal_id == row.id).order_by(SignalExchangeSymbolRecord.exchange))).all())
            prices = tuple((await self._session.scalars(select(SignalInitialPriceRecord).where(SignalInitialPriceRecord.signal_id == row.id).order_by(SignalInitialPriceRecord.exchange))).all())
            return Signal(row.id,row.signalist_id,row.creation_token,row.mode,row.canonical_symbol,row.base_asset,row.quote_asset,row.selected_exchange,row.direction,row.entry_price,row.leverage,tuple(x.price for x in tps),row.stop_loss,row.reference_price,row.price_source_type,row.status,tuple((x.exchange,x.native_symbol) for x in maps),row.created_at,row.updated_at,tuple((x.exchange,x.price) for x in prices),row.cancelled_at)
        except SignalCreationError: raise
        except Exception as exc: raise SignalCreationError("signal aggregate could not be loaded") from exc
