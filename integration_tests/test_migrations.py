from __future__ import annotations

import unittest

from sqlalchemy import text

from integration_tests.support import create_safe_engine, migrate, require_safe_test_database


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.safe.cleanup()

    async def test_empty_upgrade_downgrade_and_reupgrade(self) -> None:
        migrate("head", self.safe)

    async def test_phase6_rows_survive_phase7_upgrade(self) -> None:
        """Upgrade realistic pre-Phase-7 durable data, not only an empty schema."""
        migrate("base", self.safe); migrate("20260809_01", self.safe)
        engine = create_safe_engine(self.safe)
        now = "2026-08-08T12:00:00+00:00"
        async with engine.begin() as connection:
            signalist = (await connection.execute(text(
                "INSERT INTO signalists(telegram_user_id,display_name,status) VALUES(901,'legacy','active') RETURNING id"))).scalar_one()
            destination = (await connection.execute(text(
                "INSERT INTO destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit) "
                "VALUES('legacy-destination',-901,'channel','public','active',10) RETURNING id"))).scalar_one()
            await connection.execute(text("INSERT INTO destination_signalists(destination_id,signalist_id) VALUES(:d,:s)"), {"d": destination, "s": signalist})
            signal = (await connection.execute(text(
                "INSERT INTO signals(signalist_id,creation_token,mode,canonical_symbol,base_asset,quote_asset,direction,entry_price,leverage,stop_loss,reference_price,price_source_type,status) "
                "VALUES(:s,'phase6-preserved','composite','BTCUSDT','BTC','USDT','long','100',10,'90','100','composite','active') RETURNING id"), {"s": signalist})).scalar_one()
            await connection.execute(text("INSERT INTO signal_take_profits(signal_id,position,price) VALUES(:s,1,'110')"), {"s": signal})
            for exchange in ("ourbit", "toobit", "lbank"):
                await connection.execute(text("INSERT INTO signal_exchange_symbols(signal_id,exchange,native_symbol) VALUES(:s,:e,'BTCUSDT')"), {"s": signal, "e": exchange})
                await connection.execute(text("INSERT INTO signal_initial_prices(signal_id,exchange,price) VALUES(:s,:e,'100')"), {"s": signal, "e": exchange})
            await connection.execute(text("INSERT INTO initial_publication_requests(signal_id,status,created_at,updated_at,planned_at,completed_at) VALUES(:s,'completed',:n,:n,:n,:n)"), {"s": signal, "n": now})
            await connection.execute(text("INSERT INTO destination_publications(signal_id,destination_id,event_kind,selected_exchange,status,telegram_message_id,attempt_count,created_at,updated_at,sent_at) VALUES(:s,:d,'initial','ourbit','sent',100,1,:n,:n,:n)"), {"s": signal, "d": destination, "n": now})
            await connection.execute(text("INSERT INTO signal_monitor_states(signal_id,stage,next_tp_index,highest_tp_index,current_result_kind,current_result_price,current_leveraged_roi,created_at,updated_at) VALUES(:s,'post_tp1',2,1,'win','110','100',:n,:n)"), {"s": signal, "n": now})
            event = (await connection.execute(text("INSERT INTO signal_events(signal_id,kind,tp_index,threshold_price,observed_reference_price,roi_basis_price,leveraged_roi,price_source_type,occurred_at,created_at) VALUES(:s,'tp_hit',1,'110','111','110','100','composite',:n,:n) RETURNING id"), {"s": signal, "n": now})).scalar_one()
            await connection.execute(text("INSERT INTO signal_event_source_prices(event_id,exchange,price) VALUES(:e,'ourbit','112')"), {"e": event})
        await engine.dispose(); migrate("head", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.connect() as connection:
            row = (await connection.execute(text("SELECT status,cancelled_at FROM signals WHERE creation_token='phase6-preserved'"))).one()
            self.assertEqual(row, ("active", None))
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signal_take_profits WHERE signal_id=:s"), {"s": signal}), 1)
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signal_initial_prices WHERE signal_id=:s"), {"s": signal}), 3)
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signal_monitor_states WHERE signal_id=:s"), {"s": signal}), 1)
            self.assertEqual(await connection.scalar(text("SELECT telegram_message_id FROM destination_publications WHERE signal_id=:s"), {"s": signal}), 100)
            self.assertEqual(await connection.scalar(text("SELECT roi_basis_price FROM signal_events WHERE signal_id=:s"), {"s": signal}), "110")
        await engine.dispose()

    async def test_phase4_downgrade_preserves_signalist(self) -> None:
        migrate("20260731_01", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO signalists (telegram_user_id,display_name) VALUES (123,'existing')"))
        await engine.dispose()
        migrate("head", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.begin() as connection:
            await connection.execute(text("INSERT INTO destinations (internal_name,telegram_chat_id,destination_type,access_level,daily_limit) VALUES ('d',-1,'channel','private',1)"))
        await engine.dispose()
        migrate("-13", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.connect() as connection:
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signalists WHERE telegram_user_id=123")),1)
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='destinations'")),0)
        await engine.dispose(); migrate("head", self.safe)
        engine = create_safe_engine(self.safe)
        try:
            async with engine.connect() as connection:
                table_sql = await connection.scalar(text(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='signalists'"
                ))
                self.assertIn("signalist_status", table_sql)
                self.assertIn("uq_signalists_telegram_user_id", table_sql)
                indexes = set((await connection.execute(text(
                    "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='signalists'"
                ))).scalars())
                self.assertIn("ix_signalists_status", indexes)
        finally:
            await engine.dispose()
        migrate("base", self.safe)
        engine = create_safe_engine(self.safe)
        try:
            async with engine.connect() as connection:
                exists = await connection.scalar(text(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='signalists'"
                ))
                self.assertEqual(exists, 0)
        finally:
            await engine.dispose()
        migrate("head", self.safe)
