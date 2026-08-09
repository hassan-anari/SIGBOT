from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from chainura.application.publication import (
    DeliveryAmbiguous,
    DeliveryRateLimited,
    UpdatePublicationService,
)
from chainura.domain.destination import (
    DestinationAccessLevel,
    DestinationStatus,
    DestinationType,
    Exchange,
)
from chainura.domain.monitoring import CandidateStatus, SignalEventKind
from chainura.domain.publication import PublicationRequestStatus, PublicationStatus
from chainura.domain.signal import PriceSourceType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.domain.signalist import SignalistStatus
from chainura.persistence.base import Base
from chainura.persistence.models import (
    DestinationPublicationRecord,
    DestinationRecord,
    DestinationSignalistRecord,
    DestinationUpdatePublicationRecord,
    SignalDetailsPublicationRecord,
    SignalExchangeSymbolRecord,
    SignalInitialPriceRecord,
    SignalEventRecord,
    SignalEventSourcePriceRecord,
    SignalRecord,
    SignalTakeProfitRecord,
    SignalUpdateCandidateRecord,
    SignalistRecord,
    UpdatePublicationRequestRecord,
)
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.domain.message_template import DestinationMessageKind

NOW = datetime(2026, 8, 9, 12, tzinfo=timezone.utc)


class RecordingPublisher:
    def __init__(self, text_failure=None):
        self.photos = []
        self.texts = []
        self.next_id = 101
        self.text_failure = text_failure

    async def send_update(self, chat_id, path, caption, reply_to_message_id):
        self.next_id += 1
        self.photos.append((chat_id, path, caption, reply_to_message_id, self.next_id))
        return self.next_id

    async def send_text_update(self, chat_id, text, reply_to_message_id):
        self.texts.append((chat_id, text, reply_to_message_id))
        if self.text_failure:
            raise self.text_failure
        self.next_id += 1
        return self.next_id

    async def send_details(self,chat_id,text,reply_to_message_id):
        self.texts.append((chat_id,text,reply_to_message_id));self.next_id+=1;return self.next_id

    async def send_initial(self,chat_id,path,caption):
        self.next_id+=1;self.photos.append((chat_id,path,caption,None,self.next_id));return self.next_id


class RecordingImages:
    class Output:
        file_path = "/tmp/update-test.png"

    def __init__(self, fail=False):
        self.calls = []
        self.cleaned = []
        self.fail = fail

    async def render_update_signal(self, signal, event, exchange, destination_id):
        self.calls.append((event.kind, event.roi_basis_price, event.leveraged_roi, exchange))
        if self.fail:
            raise AssertionError("SL must not render")
        return self.Output()

    async def render_initial_signal(self,signal,exchange,destination_id,now):
        self.calls.append(("initial",signal.entry_price,dict(signal.initial_source_prices)[exchange],exchange))
        return self.Output()

    def cleanup(self, path):
        self.cleaned.append(path)


class Phase72PublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.transactions = SqlAlchemySignalistTransactionFactory(self.sessions)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def seed(self, kind, *, details_status=None, observed=Decimal("111"), basis=Decimal("110"), roi=Decimal("100"), tp_index=1):
        async with self.sessions() as session, session.begin():
            actor = SignalistRecord(telegram_user_id=7001, display_name="S", status=SignalistStatus.ACTIVE, created_at=NOW, updated_at=NOW)
            destination = DestinationRecord(internal_name="D", telegram_chat_id=-1001, destination_type=DestinationType.CHANNEL, access_level=DestinationAccessLevel.PUBLIC, status=DestinationStatus.ACTIVE, daily_limit=10, created_at=NOW, updated_at=NOW)
            session.add_all((actor, destination)); await session.flush()
            signal = SignalRecord(signalist_id=actor.id, creation_token=f"token-{kind.value}", mode=SignalMode.SINGLE_EXCHANGE, canonical_symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", selected_exchange=Exchange.OURBIT, direction=TradeDirection.LONG, entry_price=Decimal("100"), leverage=10, stop_loss=Decimal("90"), reference_price=Decimal("100"), price_source_type=PriceSourceType.OURBIT_ONLY, status=SignalLifecycleStatus.ACTIVE, created_at=NOW, updated_at=NOW)
            session.add(signal); await session.flush()
            session.add(SignalExchangeSymbolRecord(signal_id=signal.id, exchange=Exchange.OURBIT, native_symbol="BTCUSDT"))
            session.add(SignalInitialPriceRecord(signal_id=signal.id, exchange=Exchange.OURBIT, price=Decimal("100")))
            session.add(SignalTakeProfitRecord(signal_id=signal.id, position=1, price=Decimal("110")))
            session.add(DestinationSignalistRecord(destination_id=destination.id, signalist_id=actor.id, created_at=NOW))
            session.add(DestinationPublicationRecord(signal_id=signal.id, destination_id=destination.id, event_kind="initial", selected_exchange=Exchange.OURBIT, status=PublicationStatus.SENT, telegram_message_id=100, sent_at=NOW, created_at=NOW, updated_at=NOW))
            if details_status is not None:
                session.add(SignalDetailsPublicationRecord(signal_id=signal.id, destination_id=destination.id, status=details_status, telegram_message_id=101 if details_status is PublicationStatus.SENT else None, reply_to_message_id=100, sent_at=NOW if details_status is PublicationStatus.SENT else None, created_at=NOW, updated_at=NOW))
            event = SignalEventRecord(signal_id=signal.id, kind=kind, tp_index=tp_index if kind is SignalEventKind.TP_HIT else None, threshold_price=basis, observed_reference_price=observed, roi_basis_price=basis, leveraged_roi=roi, price_source_type=PriceSourceType.OURBIT_ONLY, occurred_at=NOW, created_at=NOW)
            session.add(event); await session.flush()
            session.add(SignalEventSourcePriceRecord(event_id=event.id, exchange=Exchange.OURBIT, price=Decimal("112")))
            candidate = SignalUpdateCandidateRecord(signal_id=signal.id, event_id=event.id, signalist_id=actor.id, status=CandidateStatus.APPROVED, approved_at=NOW, created_at=NOW, updated_at=NOW)
            session.add(candidate); await session.flush()
            request = UpdatePublicationRequestRecord(candidate_id=candidate.id, signal_id=signal.id, event_id=event.id, status=PublicationRequestStatus.PLANNED, created_at=NOW, updated_at=NOW, planned_at=NOW)
            session.add(request); await session.flush()
            delivery = DestinationUpdatePublicationRecord(request_id=request.id, candidate_id=candidate.id, signal_id=signal.id, event_id=event.id, destination_id=destination.id, selected_exchange=Exchange.OURBIT, event_kind=kind, status=PublicationStatus.PENDING, created_at=NOW, updated_at=NOW)
            session.add(delivery); await session.flush()
            return delivery.id, signal.id, destination.id

    async def row(self, delivery_id):
        async with self.sessions() as session:
            return await session.get(DestinationUpdatePublicationRecord, delivery_id)

    async def test_tp_remains_photo_at_exact_threshold_and_details_parent(self):
        delivery_id, _, _ = await self.seed(SignalEventKind.TP_HIT, details_status=PublicationStatus.SENT)
        images = RecordingImages(); publisher = RecordingPublisher()
        await UpdatePublicationService(self.transactions, images, publisher, lambda: NOW).process(delivery_id)
        self.assertEqual(images.calls[0][1:3], (Decimal("110"), Decimal("100")))
        self.assertEqual(len(publisher.photos), 1); self.assertFalse(publisher.texts)
        self.assertEqual(publisher.photos[0][3], 101)
        self.assertEqual((await self.row(delivery_id)).reply_to_message_id, 101)

    async def test_sl_is_text_only_without_template_or_render_dependency(self):
        delivery_id, _, _ = await self.seed(SignalEventKind.SL_HIT, observed=Decimal("5.68"), basis=Decimal("5.70"), roi=Decimal("-43"), tp_index=None)
        images = RecordingImages(fail=True); publisher = RecordingPublisher()
        await UpdatePublicationService(self.transactions, images, publisher, lambda: NOW).process(delivery_id)
        self.assertFalse(images.calls); self.assertFalse(publisher.photos)
        self.assertEqual(len(publisher.texts), 1); self.assertIn("حد ضرر", publisher.texts[0][1])
        self.assertEqual((await self.row(delivery_id)).status, PublicationStatus.SENT)

    async def test_destination_tp_override_reaches_photo_with_exact_event_values(self):
        delivery_id, _, destination_id = await self.seed(SignalEventKind.TP_HIT, details_status=PublicationStatus.SENT,
                                                         observed=Decimal("111"), basis=Decimal("110"), roi=Decimal("100"))
        templates=DestinationMessageTemplateService(self.transactions)
        await templates.set_destination(DestinationMessageKind.TP_HIT,destination_id,
            "CUSTOM TP {tp_price} {execution_price} {roi}")
        images=RecordingImages();publisher=RecordingPublisher()
        await UpdatePublicationService(self.transactions,images,publisher,lambda:NOW,message_templates=templates).process(delivery_id)
        self.assertFalse(publisher.texts);self.assertEqual(len(publisher.photos),1)
        self.assertIn("CUSTOM TP",publisher.photos[0][2]);self.assertIn("110",publisher.photos[0][2])
        self.assertEqual(images.calls[0][1:3],(Decimal("110"),Decimal("100")))

    async def test_destination_sl_override_reaches_text_without_rendering(self):
        delivery_id, _, destination_id = await self.seed(SignalEventKind.SL_HIT, basis=Decimal("90"),roi=Decimal("-100"),tp_index=None)
        templates=DestinationMessageTemplateService(self.transactions)
        await templates.set_destination(DestinationMessageKind.SL_HIT,destination_id,
            "CUSTOM SL {stop_loss} {execution_price} {roi}")
        images=RecordingImages(fail=True);publisher=RecordingPublisher()
        await UpdatePublicationService(self.transactions,images,publisher,lambda:NOW,message_templates=templates).process(delivery_id)
        self.assertFalse(images.calls);self.assertFalse(publisher.photos);self.assertEqual(len(publisher.texts),1)
        self.assertIn("CUSTOM SL",publisher.texts[0][1]);self.assertIn("90",publisher.texts[0][1])

    async def test_destination_details_override_keeps_initial_reply_parent(self):
        _,signal_id,destination_id=await self.seed(SignalEventKind.TP_HIT,details_status=PublicationStatus.PENDING)
        templates=DestinationMessageTemplateService(self.transactions)
        await templates.set_destination(DestinationMessageKind.SIGNAL_DETAILS,destination_id,"CUSTOM DETAILS {symbol}")
        publisher=RecordingPublisher()
        from chainura.application.publication import SignalDetailsPublicationService
        await SignalDetailsPublicationService(self.transactions,publisher,lambda:NOW,message_templates=templates).process(1)
        self.assertIn("CUSTOM DETAILS",publisher.texts[0][1]);self.assertEqual(publisher.texts[0][2],100)

    async def test_destination_initial_override_keeps_photo_and_p0_p1_snapshot(self):
        _,signal_id,destination_id=await self.seed(SignalEventKind.TP_HIT)
        async with self.sessions() as session,session.begin():
            initial=await session.get(DestinationPublicationRecord,1);initial.status=PublicationStatus.PENDING;initial.telegram_message_id=None;initial.sent_at=None
            price=await session.get(SignalInitialPriceRecord,(signal_id,Exchange.OURBIT));price.price=Decimal("100.5")
        templates=DestinationMessageTemplateService(self.transactions)
        await templates.set_destination(DestinationMessageKind.INITIAL_PHOTO_CAPTION,destination_id,"CUSTOM INITIAL {entry} {last_price}")
        images=RecordingImages();publisher=RecordingPublisher()
        from chainura.application.publication import InitialPublicationService
        await InitialPublicationService(self.transactions,images,publisher,lambda:NOW,message_templates=templates).process(1)
        self.assertEqual(len(publisher.photos),1);self.assertIn("CUSTOM INITIAL",publisher.photos[0][2])
        self.assertEqual(images.calls[0][1:3],(Decimal("100"),Decimal("100.5")))

    async def test_details_pending_defers_then_sent_becomes_parent(self):
        delivery_id, signal_id, destination_id = await self.seed(SignalEventKind.TP_HIT, details_status=PublicationStatus.PENDING)
        images = RecordingImages(); publisher = RecordingPublisher()
        service = UpdatePublicationService(self.transactions, images, publisher, lambda: NOW)
        await service.process(delivery_id)
        self.assertFalse(images.calls); self.assertEqual((await self.row(delivery_id)).status, PublicationStatus.PENDING)
        async with self.sessions() as session, session.begin():
            details = await session.get(SignalDetailsPublicationRecord, 1)
            details.status=PublicationStatus.SENT; details.telegram_message_id=101; details.sent_at=NOW
        await service.process(delivery_id)
        self.assertEqual(publisher.photos[0][3], 101)

    async def test_details_terminal_failure_falls_back_to_initial(self):
        delivery_id, _, _ = await self.seed(SignalEventKind.TP_HIT, details_status=PublicationStatus.FAILED)
        publisher = RecordingPublisher()
        await UpdatePublicationService(self.transactions, RecordingImages(), publisher, lambda: NOW).process(delivery_id)
        self.assertEqual(publisher.photos[0][3], 100)

    async def test_sl_rate_limit_and_ambiguous_delivery_keep_durable_states(self):
        delivery_id, _, _ = await self.seed(SignalEventKind.SL_HIT, tp_index=None)
        limited = RecordingPublisher(DeliveryRateLimited(3))
        await UpdatePublicationService(self.transactions, RecordingImages(fail=True), limited, lambda: NOW).process(delivery_id)
        row = await self.row(delivery_id)
        self.assertEqual(row.status, PublicationStatus.RETRY_WAIT); self.assertEqual(row.attempt_count, 1)

        # A fresh database row avoids retry timing and exercises the ambiguous boundary.
        await self.asyncTearDown(); await self.asyncSetUp()
        delivery_id, _, _ = await self.seed(SignalEventKind.SL_HIT, tp_index=None)
        ambiguous = RecordingPublisher(DeliveryAmbiguous("network"))
        await UpdatePublicationService(self.transactions, RecordingImages(fail=True), ambiguous, lambda: NOW).process(delivery_id)
        self.assertEqual((await self.row(delivery_id)).status, PublicationStatus.DELIVERY_UNKNOWN)


if __name__ == "__main__":
    unittest.main()
