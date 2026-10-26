"""SQLite batch upgrade preserves existing linked history and guards downgrade."""
import sqlite3
import subprocess
import unittest
from sqlalchemy import select, text
from chainura.domain.monitoring import SignalEventKind
from chainura.domain.position import PositionActionKind
from chainura.domain.publication import PublicationStatus
from chainura.persistence.models import SignalEventRecord, SignalPositionActionRecord, SignalPositionActionPublicationRecord
from integration_tests import test_phase4b_ordering as ordering
from integration_tests.support import migrate, create_safe_engine

class IndependentCloseMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_populated_upgrade_preserves_history_and_new_records_block_downgrade(self):
        fixture=ordering.Phase4BOrderingIntegrationTests()
        await fixture.asyncSetUp()
        try:
            await fixture.engine.dispose(); migrate('20260818_03',fixture.safe,downgrade=True)
            def snapshot():
                with sqlite3.connect(fixture.safe.path) as db:
                    return {name:db.execute(f'SELECT * FROM "{name}"').fetchall() for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='alembic_version'").fetchall()}
            before=snapshot(); migrate('head',fixture.safe)
            self.assertEqual(snapshot(),before)
            fixture.engine=create_safe_engine(fixture.safe)
            from chainura.persistence.database import create_session_factory
            fixture.sessions=create_session_factory(fixture.engine)
            async with fixture.sessions() as s,s.begin():
                event=SignalEventRecord(signal_id=fixture.signal,kind=SignalEventKind.MANUAL_CLOSE,observed_reference_price=ordering.Decimal('105'),roi_basis_price=ordering.Decimal('105'),leveraged_roi=ordering.Decimal('50'),price_source_type=ordering.PriceSourceType.COMPOSITE,occurred_at=ordering.NOW,created_at=ordering.NOW)
                s.add(event);await s.flush()
                action=SignalPositionActionRecord(signal_id=fixture.signal,event_id=event.id,action_kind=PositionActionKind.FULL_CLOSE,remaining_fraction_before=ordering.Decimal('1'),closed_fraction=ordering.Decimal('1'),remaining_fraction_after=ordering.Decimal('0'),execution_price=ordering.Decimal('105'),leveraged_roi=ordering.Decimal('50'),weighted_roi_delta=ordering.Decimal('50'),realized_weighted_roi_after=ordering.Decimal('50'),created_at=ordering.NOW)
                s.add(action);await s.flush()
                s.add(SignalPositionActionPublicationRecord(signal_id=fixture.signal,action_id=action.id,destination_id=fixture.dest,status=PublicationStatus.PENDING,created_at=ordering.NOW,updated_at=ordering.NOW))
            with sqlite3.connect(fixture.safe.path) as db:self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
            await fixture.engine.dispose(); durable=snapshot()
            with self.assertRaises(subprocess.CalledProcessError):migrate('20260818_03',fixture.safe,downgrade=True)
            self.assertEqual(snapshot(),durable)
        finally:await fixture.asyncTearDown()
