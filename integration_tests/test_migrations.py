from __future__ import annotations

import unittest

from sqlalchemy import text

from integration_tests.support import create_safe_engine, migrate, require_safe_test_database


class EmptyMigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.safe.cleanup()

    async def test_empty_upgrade_downgrade_and_reupgrade(self) -> None:
        migrate("head", self.safe)


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.safe = require_safe_test_database()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.safe.cleanup()

    async def test_phase4a_upgrade_constraints_downgrade_and_reupgrade(self) -> None:
        safe=require_safe_test_database()
        try:
            migrate("20260814_01",safe)
            engine=create_safe_engine(safe);now="2026-08-15T00:00:00+00:00"
            async with engine.begin() as connection:
                actor=(await connection.execute(text("INSERT INTO signalists(telegram_user_id,display_name,status,created_at,updated_at) VALUES(99001,'phase4a','active',:n,:n) RETURNING id"),{"n":now})).scalar_one()
                destination=(await connection.execute(text("INSERT INTO destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit,created_at,updated_at) VALUES('phase4a destination',-99001,'channel','private','active',5,:n,:n) RETURNING id"),{"n":now})).scalar_one()
                signal=(await connection.execute(text("INSERT INTO signals(signalist_id,creation_token,mode,canonical_symbol,base_asset,quote_asset,direction,entry_price,leverage,stop_loss,reference_price,price_source_type,status,created_at,updated_at) VALUES(:a,'phase4a-migration','composite','BTCUSDT','BTC','USDT','long','100',10,'90','100','composite','active',:n,:n) RETURNING id"),{"a":actor,"n":now})).scalar_one()
                await connection.execute(text("INSERT INTO signal_monitor_states(signal_id,stage,created_at,updated_at) VALUES(:s,'pre_tp1',:n,:n)"),{"s":signal,"n":now})
                await connection.execute(text("INSERT INTO destination_publications(signal_id,destination_id,event_kind,selected_exchange,status,telegram_message_id,attempt_count,created_at,updated_at,sent_at) VALUES(:s,:d,'initial','ourbit','sent',991,1,:n,:n,:n)"),{"s":signal,"d":destination,"n":now})
                await connection.execute(text("INSERT INTO destination_trade_locks(destination_id,canonical_symbol,signal_id,acquired_at,updated_at) VALUES(:d,'BTCUSDT',:s,:n,:n)"),{"d":destination,"s":signal,"n":now})
                event=(await connection.execute(text("INSERT INTO signal_events(signal_id,kind,tp_index,threshold_price,observed_reference_price,roi_basis_price,leveraged_roi,price_source_type,occurred_at,created_at) VALUES(:s,'tp_hit',1,'110','111','110','100','composite',:n,:n) RETURNING id"),{"s":signal,"n":now})).scalar_one()
                event2=(await connection.execute(text("INSERT INTO signal_events(signal_id,kind,tp_index,threshold_price,observed_reference_price,roi_basis_price,leveraged_roi,price_source_type,occurred_at,created_at) VALUES(:s,'tp_hit',2,'120','121','120','200','composite',:n,:n) RETURNING id"),{"s":signal,"n":now})).scalar_one()
                candidate=(await connection.execute(text("INSERT INTO signal_update_candidates(signal_id,event_id,signalist_id,status,created_at,updated_at) VALUES(:s,:e,:a,'awaiting_decision',:n,:n) RETURNING id"),{"s":signal,"e":event,"a":actor,"n":now})).scalar_one()
            await engine.dispose();migrate("20260815_01",safe);engine=create_safe_engine(safe)
            async with engine.begin() as connection:
                self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signals WHERE id=:s"),{"s":signal}),1)
                self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signal_monitor_states WHERE signal_id=:s"),{"s":signal}),1)
                self.assertEqual(await connection.scalar(text("SELECT telegram_message_id FROM destination_publications WHERE signal_id=:s"),{"s":signal}),991)
                self.assertEqual(await connection.scalar(text("SELECT count(*) FROM destination_trade_locks WHERE signal_id=:s"),{"s":signal}),1)
                tables=set((await connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars())
                self.assertTrue({"signal_position_states","signal_position_actions"} <= tables)
                indexes=set((await connection.execute(text("SELECT name FROM sqlite_master WHERE type='index'"))).scalars())
                self.assertTrue({"ix_signal_position_states_open","ix_signal_position_actions_signal_created"} <= indexes)
                await connection.execute(text("INSERT INTO signal_position_states(signal_id,remaining_fraction,realized_weighted_roi,risk_free_used,risk_free_active,save_profit_50_used,effective_stop_price,is_closed,version,created_at,updated_at) VALUES(:s,'1','0',0,0,0,'90',0,1,:n,:n)"),{"s":signal,"n":now})
                await connection.execute(text("INSERT INTO signal_position_actions(signal_id,event_id,candidate_id,action_kind,remaining_fraction_before,closed_fraction,remaining_fraction_after,execution_price,leveraged_roi,weighted_roi_delta,realized_weighted_roi_after,created_at) VALUES(:s,:e,:c,'normal_tp','1','0','1','110','100','0','0',:n)"),{"s":signal,"e":event,"c":candidate,"n":now})

            state_base="INSERT INTO signal_position_states(signal_id,remaining_fraction,realized_weighted_roi,risk_free_used,risk_free_active,save_profit_50_used,effective_stop_price,is_closed,closed_at,version,created_at,updated_at) VALUES(:s,:r,'0',0,0,0,'90',:closed,:closed_at,:version,:n,:n)"
            invalid_states=(
                {"s":signal,"r":"1","closed":0,"closed_at":None,"version":1},
                {"s":999999,"r":"1","closed":0,"closed_at":None,"version":1},
                {"s":signal,"r":"-0.1","closed":0,"closed_at":None,"version":1},
                {"s":signal,"r":"1.1","closed":0,"closed_at":None,"version":1},
                {"s":signal,"r":"1","closed":0,"closed_at":now,"version":1},
                {"s":signal,"r":"0.5","closed":1,"closed_at":now,"version":1},
                {"s":signal,"r":"0","closed":1,"closed_at":None,"version":1},
                {"s":signal,"r":"1","closed":0,"closed_at":None,"version":0},)
            for params in invalid_states:
                async with engine.connect() as connection:
                    transaction=await connection.begin()
                    with self.assertRaises(Exception):await connection.execute(text(state_base),dict(params,n=now))
                    await transaction.rollback()

            action_base="INSERT INTO signal_position_actions(signal_id,event_id,candidate_id,action_kind,remaining_fraction_before,closed_fraction,remaining_fraction_after,execution_price,leveraged_roi,weighted_roi_delta,realized_weighted_roi_after,created_at) VALUES(:s,:e,:c,:kind,:before,:closed,:after,'120','200',:delta,:realized,:n)"
            invalid_actions=(
                {"s":999999,"e":event2,"c":None,"kind":"full_close","before":"1","closed":"1","after":"0","delta":"200","realized":"200"},
                {"s":signal,"e":999999,"c":None,"kind":"full_close","before":"1","closed":"1","after":"0","delta":"200","realized":"200"},
                {"s":signal,"e":event2,"c":999999,"kind":"full_close","before":"1","closed":"1","after":"0","delta":"200","realized":"200"},
                {"s":signal,"e":event2,"c":candidate,"kind":"full_close","before":"1","closed":"1","after":"0","delta":"200","realized":"200"},
                {"s":signal,"e":event,"c":None,"kind":"normal_tp","before":"1","closed":"0","after":"1","delta":"0","realized":"0"},
                {"s":signal,"e":event2,"c":None,"kind":"save_profit_50","before":"1","closed":"0.5","after":"0.6","delta":"100","realized":"100"},)
            for params in invalid_actions:
                async with engine.connect() as connection:
                    transaction=await connection.begin()
                    with self.assertRaises(Exception):await connection.execute(text(action_base),dict(params,n=now))
                    await transaction.rollback()
            await engine.dispose();migrate("-1",safe);engine=create_safe_engine(safe)
            async with engine.connect() as connection:
                tables=set((await connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))).scalars())
                self.assertNotIn("signal_position_states",tables);self.assertNotIn("signal_position_actions",tables)
                self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signals WHERE id=:s"),{"s":signal}),1)
                self.assertEqual(await connection.scalar(text("SELECT count(*) FROM destination_trade_locks WHERE signal_id=:s"),{"s":signal}),1)
                self.assertEqual(await connection.scalar(text("SELECT telegram_message_id FROM destination_publications WHERE signal_id=:s"),{"s":signal}),991)
            await engine.dispose();migrate("head",safe)
        finally:
            safe.cleanup()

    async def test_phase3_trade_lock_migration_preserves_existing_data(self) -> None:
        migrate("base", self.safe);migrate("20260813_02", self.safe)
        engine=create_safe_engine(self.safe);now="2026-08-14T00:00:00+00:00"
        async with engine.begin() as connection:
            actor=(await connection.execute(text(
                "INSERT INTO signalists(telegram_user_id,display_name,status,created_at,updated_at) "
                "VALUES(8801,'legacy','active',:n,:n) RETURNING id"),{"n":now})).scalar_one()
            destination=(await connection.execute(text(
                "INSERT INTO destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit,created_at,updated_at) "
                "VALUES('phase3 legacy',-8801,'channel','private','active',5,:n,:n) RETURNING id"),{"n":now})).scalar_one()
            await connection.execute(text("INSERT INTO destination_signalists(destination_id,signalist_id,created_at) VALUES(:d,:a,:n)"),{"d":destination,"a":actor,"n":now})
            signal=(await connection.execute(text(
                "INSERT INTO signals(signalist_id,creation_token,mode,canonical_symbol,base_asset,quote_asset,direction,entry_price,leverage,reference_price,price_source_type,status,created_at,updated_at) "
                "VALUES(:a,'phase3-migration','composite','BTCUSDT','BTC','USDT','long','100',10,'100','composite','pending_details',:n,:n) RETURNING id"),{"a":actor,"n":now})).scalar_one()
            await connection.execute(text("INSERT INTO destination_publications(signal_id,destination_id,event_kind,selected_exchange,status,attempt_count,created_at,updated_at) VALUES(:s,:d,'initial','ourbit','pending',0,:n,:n)"),{"s":signal,"d":destination,"n":now})
        await engine.dispose();migrate("20260814_01",self.safe)
        engine=create_safe_engine(self.safe)
        async with engine.begin() as connection:
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signals WHERE creation_token='phase3-migration'")),1)
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM destination_publications WHERE signal_id=:s"),{"s":signal}),1)
            indexes=set((await connection.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='destination_trade_locks'"))).scalars())
            self.assertIn("ix_destination_trade_locks_signal_id",indexes)
            await connection.execute(text("INSERT INTO destination_trade_locks(destination_id,canonical_symbol,signal_id,acquired_at,updated_at) VALUES(:d,'BTCUSDT',:s,:n,:n)"),{"d":destination,"s":signal,"n":now})
        for statement,params in (
            ("INSERT INTO destination_trade_locks(destination_id,canonical_symbol,signal_id,acquired_at,updated_at) VALUES(:d,'BTCUSDT',:s,:n,:n)",{"d":destination,"s":signal,"n":now}),
            ("INSERT INTO destination_trade_locks(destination_id,canonical_symbol,signal_id,acquired_at,updated_at) VALUES(:d,'   ',:s,:n,:n)",{"d":destination,"s":signal,"n":now}),
            ("INSERT INTO destination_trade_locks(destination_id,canonical_symbol,signal_id,acquired_at,updated_at) VALUES(999999,'ETHUSDT',:s,:n,:n)",{"s":signal,"n":now}),
            ("INSERT INTO destination_trade_locks(destination_id,canonical_symbol,signal_id,acquired_at,updated_at) VALUES(:d,'ETHUSDT',999999,:n,:n)",{"d":destination,"n":now}),):
            async with engine.connect() as connection:
                transaction=await connection.begin()
                with self.assertRaises(Exception):await connection.execute(text(statement),params)
                await transaction.rollback()
        await engine.dispose();migrate("-1",self.safe)
        engine=create_safe_engine(self.safe)
        async with engine.begin() as connection:
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='destination_trade_locks'")),0)
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM signals WHERE creation_token='phase3-migration'")),1)
            self.assertEqual(await connection.scalar(text("SELECT count(*) FROM destination_publications WHERE signal_id=:s"),{"s":signal}),1)
            await connection.execute(text("DELETE FROM signals WHERE id=:s"),{"s":signal})
            await connection.execute(text("DELETE FROM destinations WHERE id=:d"),{"d":destination})
            await connection.execute(text("DELETE FROM signalists WHERE id=:a"),{"a":actor})
        await engine.dispose();migrate("head",self.safe)

    async def test_warmup_template_constraint_migration_preserves_rows_and_indexes(self) -> None:
        migrate("base", self.safe); migrate("20260813_01", self.safe)
        engine = create_safe_engine(self.safe)
        created = "2026-08-13T08:00:00+00:00"; updated = "2026-08-13T09:00:00+00:00"
        async with engine.begin() as connection:
            signalist = (await connection.execute(text(
                "INSERT INTO signalists(telegram_user_id,display_name,status,created_at,updated_at) "
                "VALUES(7001,'legacy','active',:c,:u) RETURNING id"), {"c":created,"u":updated})).scalar_one()
            destination = (await connection.execute(text(
                "INSERT INTO destinations(internal_name,telegram_chat_id,destination_type,access_level,status,daily_limit,created_at,updated_at) "
                "VALUES('legacy destination',-7001,'channel','public','active',5,:c,:u) RETURNING id"),
                {"c":created,"u":updated})).scalar_one()
            await connection.execute(text(
                "INSERT INTO destination_message_templates(message_kind,destination_id,template_text,created_at,updated_at) VALUES"
                "('signal_details',NULL,'global legacy',:c,:u),"
                "('tp_hit',:d,'destination legacy',:c,:u)"), {"d":destination,"c":created,"u":updated})
            original = (await connection.execute(text(
                "SELECT message_kind,destination_id,template_text,created_at,updated_at "
                "FROM destination_message_templates ORDER BY id"))).all()
            old_indexes = set((await connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='destination_message_templates' "
                "AND name NOT LIKE 'sqlite_%'"))).scalars())
        await engine.dispose(); migrate("20260813_02", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.begin() as connection:
            preserved = (await connection.execute(text(
                "SELECT message_kind,destination_id,template_text,created_at,updated_at "
                "FROM destination_message_templates ORDER BY id"))).all()
            self.assertEqual(preserved, original)
            upgraded_indexes = set((await connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='destination_message_templates' "
                "AND name NOT LIKE 'sqlite_%'"))).scalars())
            self.assertEqual(upgraded_indexes, old_indexes)
            await connection.execute(text(
                "INSERT INTO destination_message_templates(message_kind,destination_id,template_text,created_at,updated_at) "
                "VALUES('warmup',NULL,'global warmup',:c,:u),('warmup',:d,'destination warmup',:c,:u)"),
                {"d":destination,"c":created,"u":updated})
        # Isolate the deliberately rejected statement so it cannot roll back the
        # successful migration revision marker or the valid Warm-up fixtures.
        async with engine.connect() as connection:
            transaction = await connection.begin()
            with self.assertRaises(Exception):
                await connection.execute(text(
                    "INSERT INTO destination_message_templates(message_kind,template_text,created_at,updated_at) "
                    "VALUES('unsupported','bad',:c,:u)"), {"c":created,"u":updated})
            await transaction.rollback()
        async with engine.begin() as connection:
            self.assertEqual(await connection.scalar(text(
                "SELECT count(*) FROM destination_message_templates WHERE message_kind='warmup'")), 2)
            tables = set((await connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table'"))).scalars())
            self.assertTrue({"warmup_requests","destination_warmup_cooldowns","warmup_publications"} <= tables)
            indexes = set((await connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index'"))).scalars())
            self.assertIn("ix_warmup_publications_status_retry", indexes)
            warmup_sql = await connection.scalar(text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='warmup_publications'"))
            self.assertIn("ck_warmup_publications_sent", warmup_sql)
        await engine.dispose(); migrate("-1", self.safe)
        engine = create_safe_engine(self.safe)
        async with engine.connect() as connection:
            rows = (await connection.execute(text(
                "SELECT message_kind,destination_id,template_text,created_at,updated_at "
                "FROM destination_message_templates ORDER BY id"))).all()
            self.assertEqual(rows, original)
            indexes = set((await connection.execute(text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='destination_message_templates' "
                "AND name NOT LIKE 'sqlite_%'"))).scalars())
            self.assertEqual(indexes, old_indexes)
            table_sql = await connection.scalar(text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='destination_message_templates'"))
            self.assertNotIn("'warmup'", table_sql)
        await engine.dispose(); migrate("head", self.safe)

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
        migrate("-15", self.safe)
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
