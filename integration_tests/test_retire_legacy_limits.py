from __future__ import annotations

from contextlib import closing
from io import StringIO
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

from chainura.application.limit_orders import LimitOrderService
from chainura.application.limit_publication import LimitCancellationPublicationService, LimitPendingPublicationService
from chainura.application.message_templates import DestinationMessageTemplateService
from chainura.application.monitoring import MonitoringService
from chainura.application.publication import (
    CancellationPublicationService, InitialPublicationService, PositionActionPublicationService,
    SignalDetailsPublicationService, UpdatePublicationService,
)
from chainura.application.signal_comments import SignalCommentPublicationService
from chainura.application.signalist_notification import SignalistNotificationService
from chainura.persistence.database import create_session_factory
from chainura.persistence.transaction import SqlAlchemySignalistTransactionFactory
from integration_tests.support import create_safe_engine, migrate, require_safe_test_database
from scripts import retire_legacy_limits as utility

NOW = "2026-11-23T08:00:00.000000+00:00"
UNRELATED = 801
SCRIPT = Path(utility.__file__)
LOCKS_BY_SIGNAL = {551: 0, 556: 6, 572: 7, 703: 8, 798: 8, 800: 9}


class RetireLegacyLimitsTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = require_safe_test_database()
        cls.addClassCleanup(cls.template.cleanup)
        migrate("head", cls.template)

    def setUp(self):
        self.safe = require_safe_test_database()
        self.addCleanup(self.safe.cleanup)
        self.backup_path = self.safe.path.with_name("before.db")
        self.proc_root = self.safe.path.with_name("proc")
        (self.proc_root / "self/fd").mkdir(parents=True)
        require_stopped = utility.require_stopped
        process_view = patch.object(utility, "require_stopped",
                                    side_effect=lambda path: require_stopped(path, self.proc_root))
        process_view.start()
        self.addCleanup(process_view.stop)
        utility.backup(self.template.path, self.safe.path)
        with closing(utility.connect(self.safe.path, writable=True)) as db:
            db.execute("BEGIN")
            db.execute("INSERT INTO signalists(id, telegram_user_id, display_name, status, created_at, updated_at) "
                       "VALUES (1, 1111, 'Fixture owner', 'active', ?, ?)", (NOW, NOW))
            for destination in range(1, 10):
                db.execute("INSERT INTO destinations(id, internal_name, telegram_chat_id, destination_type, "
                           "access_level, status, daily_limit, created_at, updated_at) "
                           "VALUES (?, ?, ?, 'channel', 'private', 'active', 15, ?, ?)",
                           (destination, f"fixture-{destination}", -1000 - destination, NOW, NOW))
            for signal_id, symbol in utility.TARGETS.items():
                self.seed_signal(db, signal_id, symbol, LOCKS_BY_SIGNAL[signal_id])
            self.seed_signal(db, UNRELATED, "ETHUSDT", 1)
            db.commit()

    def seed_signal(self, db, signal_id, symbol, locks):
        db.execute("INSERT INTO signals(id, signalist_id, creation_token, mode, canonical_symbol, base_asset, "
                   "quote_asset, selected_exchange, direction, entry_price, leverage, stop_loss, reference_price, "
                   "price_source_type, status, created_at, updated_at, entry_type) "
                   "VALUES (?, 1, ?, 'single_exchange', ?, ?, 'USDT', 'ourbit', 'long', '90', 10, '85', "
                   "'100', 'ourbit_only', 'entry_triggered', ?, ?, 'limit')",
                   (signal_id, f"seed-{signal_id}", symbol, symbol.removesuffix("USDT"), NOW, NOW))
        db.execute("INSERT INTO signal_take_profits VALUES (?, 1, '95')", (signal_id,))
        db.execute("INSERT INTO signal_exchange_symbols VALUES (?, 'ourbit', ?)", (signal_id, symbol))
        db.execute("INSERT INTO signal_initial_prices VALUES (?, 'ourbit', '100')", (signal_id,))
        db.execute("INSERT INTO limit_entry_triggers VALUES (?, '90', '89', 'ourbit_only', ?, ?)",
                   (signal_id, NOW, NOW))
        db.execute("INSERT INTO limit_entry_trigger_source_prices VALUES (?, 'ourbit', '89')", (signal_id,))
        db.execute("INSERT INTO signalist_notifications(kind, signalist_id, status, attempt_count, "
                   "telegram_message_id, retry_at, sent_at, created_at, updated_at, limit_trigger_signal_id) "
                   "VALUES ('limit_trigger', 1, 'sent', 2, ?, ?, ?, ?, ?, ?)",
                   (10000 + signal_id, NOW, NOW, NOW, NOW, signal_id))
        db.execute("INSERT INTO limit_admission_requests(signal_id, status, created_at, updated_at, planned_at) "
                   "VALUES (?, 'planned', ?, ?, ?)", (signal_id, NOW, NOW, NOW))
        for destination in range(1, locks + 1):
            db.execute("INSERT INTO destination_trade_locks VALUES (?, ?, ?, ?, ?)",
                       (destination, symbol, signal_id, NOW, NOW))
            unsent = signal_id == 800 and destination == 9
            pending_id = db.execute(
                "INSERT INTO limit_pending_publications(signal_id, destination_id, status, telegram_message_id, "
                "attempt_count, sent_at, created_at, updated_at, selected_exchange) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ourbit') RETURNING id",
                (signal_id, destination, "pending" if unsent else "sent",
                 None if unsent else signal_id * 10 + destination, 0 if unsent else 1,
                 None if unsent else NOW, NOW, NOW)).fetchone()[0]
            db.execute("INSERT INTO initial_publication_daily_slots(destination_id, local_date, slot_number, "
                       "limit_publication_id, created_at) VALUES (?, '2026-11-23', ?, ?, ?)",
                       (destination, signal_id, pending_id, NOW))

    def rows(self, sql, *params, path=None):
        with closing(utility.connect(path or self.safe.path)) as db:
            return [tuple(row) for row in db.execute(sql, params)]

    def execute(self, sql, *params):
        with closing(utility.connect(self.safe.path, writable=True)) as db:
            db.execute(sql, params)

    def business_snapshot(self):
        with closing(utility.connect(self.safe.path)) as db:
            return list(db.iterdump())

    async def test_retires_audited_signals_silently_and_survives_worker_recovery(self):
        unrelated = self.rows("SELECT * FROM signals WHERE id=?", UNRELATED)
        others_locks = self.rows("SELECT * FROM destination_trade_locks WHERE signal_id=?", UNRELATED)
        pending_history = self.rows("SELECT * FROM limit_pending_publications WHERE status='sent' ORDER BY id")
        triggers = self.rows("SELECT * FROM limit_entry_triggers ORDER BY signal_id")
        notes = self.rows("SELECT * FROM signalist_notifications ORDER BY id")

        before = self.business_snapshot()
        self.assertEqual(utility.retire(self.safe.path)["result"], "dry_run")
        self.assertEqual(self.business_snapshot(), before)
        with patch.object(socket, "create_connection", side_effect=AssertionError("offline maintenance")), \
                patch.object(socket, "getaddrinfo", side_effect=AssertionError("offline maintenance")):
            result = utility.retire(self.safe.path, backup_path=self.backup_path)
        self.assertEqual(result["result"], "retired")
        self.assertEqual(result["trade_locks_released"], 38)
        self.assertEqual([row["id"] for row in result["after"]], list(utility.TARGETS))
        self.assertEqual(self.rows("SELECT id FROM signals WHERE status='cancelled'"), [(i,) for i in utility.TARGETS])
        self.assertEqual(self.rows("SELECT * FROM signals WHERE id=?", UNRELATED), unrelated)
        self.assertEqual(self.rows("SELECT * FROM destination_trade_locks"), others_locks)
        self.assertEqual(self.rows("SELECT * FROM limit_pending_publications WHERE status='sent' ORDER BY id"), pending_history)
        self.assertEqual(self.rows("SELECT * FROM limit_entry_triggers ORDER BY signal_id"), triggers)
        self.assertEqual(self.rows("SELECT * FROM signalist_notifications ORDER BY id"), notes)
        self.assertEqual(self.rows("SELECT status, attempt_count FROM limit_pending_publications WHERE status<>'sent'"),
                         [("superseded", 0)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM initial_publication_daily_slots"), [(len(pending_history),)])
        for table in ("signal_cancellation_publications", "limit_cancellation_publications"):
            self.assertEqual(self.rows(f"SELECT COUNT(*) FROM {table}"), [(0,)])
        utility.validate_backup(self.backup_path)
        self.assertEqual(self.backup_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM signals WHERE status='entry_triggered'", path=self.backup_path),
                         [(7,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM destination_trade_locks", path=self.backup_path), [(39,)])
        self.assertEqual(self.rows("PRAGMA integrity_check"), [("ok",)])
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])

        after = self.business_snapshot()
        self.assertEqual(utility.retire(self.safe.path, backup_path=self.backup_path)["result"], "already_retired")
        self.assertEqual(self.business_snapshot(), after)

        engine = create_safe_engine(self.safe)
        self.addAsyncCleanup(engine.dispose)
        sessions = create_session_factory(engine)
        tx = SqlAlchemySignalistTransactionFactory(sessions)
        templates = DestinationMessageTemplateService(tx)
        transport = Mock()
        for method in ("send", "send_standalone_text", "send_initial", "send_text_update", "send_details"):
            setattr(transport, method, AsyncMock(side_effect=AssertionError("unexpected outbound work")))
        services = (
            LimitPendingPublicationService(tx, transport, templates),
            LimitCancellationPublicationService(tx, transport, templates),
            InitialPublicationService(tx, None, transport),
            CancellationPublicationService(tx, transport),
            SignalDetailsPublicationService(tx, transport),
            UpdatePublicationService(tx, None, transport),
            PositionActionPublicationService(tx, transport, templates),
            SignalCommentPublicationService(tx, transport),
        )
        for service in services:
            await service.recover()
            if isinstance(service, UpdatePublicationService):
                await service.sweep()
            elif hasattr(service, "plan_pending"):
                await service.plan_pending()
            self.assertEqual(await service.pending_ids(), (), type(service).__name__)
        self.assertFalse(await services[2].notify_completed())
        notifications = SignalistNotificationService(sessions, transport)
        await notifications.recover()
        self.assertEqual(await notifications.pending(), ())
        self.assertEqual(await LimitOrderService(sessions).load_work(), ())
        monitoring = MonitoringService(sessions, None)
        await monitoring.reconcile()
        self.assertEqual(await monitoring.load_work(100), ())
        self.assertFalse(transport.mock_calls)
        self.assertEqual(self.business_snapshot(), after)

    def assert_refused_without_mutation(self, detail):
        before = self.business_snapshot()
        with self.assertRaisesRegex(utility.RetirementRefused, detail):
            utility.retire(self.safe.path, backup_path=self.backup_path)
        self.assertEqual(self.business_snapshot(), before)
        self.assertFalse(self.backup_path.exists())

    def test_unexpected_lifecycle_refuses_all_six(self):
        self.execute("UPDATE signals SET status='pending_entry' WHERE id=800")
        self.assert_refused_without_mutation("lifecycle states")

    def test_wrong_symbol_with_same_id_refuses_all_six(self):
        self.execute("UPDATE signals SET canonical_symbol='WRONGUSDT' WHERE id=800")
        self.assert_refused_without_mutation("IDs or symbols")

    def test_inconsistent_trigger_owner_refuses_all_six(self):
        self.execute("UPDATE signalist_notifications SET status='delivery_unknown' WHERE limit_trigger_signal_id=800")
        self.assert_refused_without_mutation("definitely delivered")

    def test_unsafe_pending_delivery_states_are_refused(self):
        for status in ("sending", "rendering", "retry_wait", "delivery_unknown"):
            with self.subTest(status=status):
                self.execute("UPDATE limit_pending_publications SET status=? WHERE signal_id=800 AND destination_id=9", status)
                self.assert_refused_without_mutation("unsafe Pending delivery")

    def test_existing_cancellation_intent_is_refused(self):
        self.execute("INSERT INTO limit_cancellation_publications(signal_id, destination_id, "
                     "pending_publication_id, reply_to_message_id, status, created_at, updated_at) "
                     "SELECT signal_id, destination_id, id, telegram_message_id, 'pending', ?, ? "
                     "FROM limit_pending_publications WHERE signal_id=800 AND destination_id=1", NOW, NOW)
        self.assert_refused_without_mutation("limit_cancellation_publications")

    def test_inconsistent_lock_or_slot_is_refused(self):
        self.execute("UPDATE initial_publication_daily_slots SET destination_id=1, slot_number=1 "
                     "WHERE limit_publication_id = (SELECT id FROM limit_pending_publications "
                     "WHERE signal_id=800 AND destination_id=9)")
        self.assert_refused_without_mutation("mismatched daily slot")

    def test_unexpected_revision_or_dependency_refuses_apply(self):
        self.execute("CREATE TABLE unknown_outbox(trade_id INTEGER PRIMARY KEY)")
        self.assert_refused_without_mutation("unexpected SQLite tables")
        self.execute("DROP TABLE unknown_outbox")
        self.execute("CREATE TRIGGER unexpected AFTER UPDATE ON signals BEGIN SELECT 1; END")
        self.assert_refused_without_mutation("unexpected SQLite trigger")
        self.execute("DROP TRIGGER unexpected")
        self.execute("UPDATE alembic_version SET version_num='other'")
        self.assert_refused_without_mutation("unsupported Alembic revision")

    def test_active_database_holder_refuses_apply(self):
        program = "import sys; db = open(sys.argv[1], 'rb'); print('ready', flush=True); sys.stdin.read()"
        with subprocess.Popen([sys.executable, "-c", program, str(self.safe.path)],
                              text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE) as holder:
            try:
                self.assertEqual(holder.stdout.readline().strip(), "ready")
                (self.proc_root / str(holder.pid)).symlink_to(Path("/proc") / str(holder.pid))
                self.assert_refused_without_mutation("open in another process")
            finally:
                holder.stdin.close()
                holder.wait(timeout=5)

    def test_competing_writer_refuses_and_leaves_no_backup(self):
        with closing(utility.connect(self.safe.path, writable=True)) as writer:
            writer.execute("BEGIN IMMEDIATE")
            with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                utility.retire(self.safe.path, backup_path=self.backup_path)
        self.assertFalse(self.backup_path.exists())
        self.assertEqual(utility.retire(self.safe.path)["result"], "dry_run")

    def test_postflight_failure_rolls_back_every_business_change(self):
        before = self.business_snapshot()
        with closing(utility.connect(self.safe.path)) as db:
            preflight = utility.inspect(db)
        with patch.object(utility, "inspect", side_effect=[preflight,
                utility.RetirementRefused("postflight failure")]):
            with self.assertRaisesRegex(utility.RetirementRefused, "postflight failure"):
                utility.retire(self.safe.path, backup_path=self.backup_path)
        self.assertEqual(self.business_snapshot(), before)
        utility.validate_backup(self.backup_path)

    def test_cli_confirmation_and_legacy_schema(self):
        migrate(utility.LEGACY_REVISION, self.safe, downgrade=True)
        prefix = [sys.executable, str(SCRIPT), str(self.safe.path)]
        denied = subprocess.run([*prefix, "--apply", "--backup", str(self.backup_path)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(denied.returncode, 2)
        self.assertFalse(self.backup_path.exists())
        with patch("sys.stdout", new_callable=StringIO) as report:
            self.assertEqual(utility.main([str(self.safe.path), "--apply",
                "--backup", str(self.backup_path), "--maintenance-window", "--confirm",
                utility.CONFIRMATION]), 0)
        self.assertIn('"result": "retired"', report.getvalue())
        self.assertEqual(utility.retire(self.safe.path)["result"], "already_retired")
