from datetime import datetime, timezone
from decimal import Decimal
import unittest

from chainura.domain.position import (InvalidPositionTransition,
    PositionActionKind as Kind, PositionState, apply_position_action,
    initial_position)

NOW=datetime(2026,8,15,tzinfo=timezone.utc)

class PositionReducerTests(unittest.TestCase):
    def apply(self,state,kind,roi="0",entry=None):
        return apply_position_action(state,kind,leveraged_roi=Decimal(roi),
            entry_price=Decimal(entry) if entry else None,occurred_at=NOW)

    def test_normal_intermediate_and_final_tp(self):
        state=initial_position(Decimal("90"))
        intermediate=self.apply(state,Kind.NORMAL_TP,"20")
        self.assertEqual((intermediate.closed_fraction,intermediate.weighted_roi_delta),(Decimal("0"),Decimal("0")))
        final=self.apply(intermediate.state,Kind.FINAL_TP_CLOSE,"40")
        self.assertEqual((final.state.remaining_fraction,final.state.realized_weighted_roi,final.state.is_closed),(Decimal("0"),Decimal("40"),True))

    def test_save50_then_final_tp_and_sl(self):
        saved=self.apply(initial_position(Decimal("90")),Kind.SAVE_PROFIT_50,"20")
        self.assertEqual((saved.closed_fraction,saved.state.remaining_fraction,saved.weighted_roi_delta,saved.state.realized_weighted_roi),(Decimal("0.5"),Decimal("0.5"),Decimal("10.0"),Decimal("10.0")))
        final=self.apply(saved.state,Kind.FINAL_TP_CLOSE,"40")
        self.assertEqual(final.state.realized_weighted_roi,Decimal("30.0"))
        stopped=self.apply(saved.state,Kind.STOP_LOSS_CLOSE,"-10")
        self.assertEqual(stopped.state.realized_weighted_roi,Decimal("5.0"))

    def test_risk_free_only_and_after_save50(self):
        rf=self.apply(initial_position(Decimal("90")),Kind.RISK_FREE,entry="100")
        self.assertEqual((rf.state.remaining_fraction,rf.state.realized_weighted_roi,rf.state.effective_stop_price),(Decimal("1"),Decimal("0"),Decimal("100")))
        hit=self.apply(rf.state,Kind.RISK_FREE_HIT_CLOSE)
        self.assertEqual(hit.state.realized_weighted_roi,Decimal("0"))
        saved=self.apply(initial_position(Decimal("90")),Kind.SAVE_PROFIT_50,"20")
        rf_after=self.apply(saved.state,Kind.RISK_FREE,entry="100")
        hit_after=self.apply(rf_after.state,Kind.RISK_FREE_HIT_CLOSE)
        self.assertEqual(hit_after.state.realized_weighted_roi,Decimal("10.0"))

    def test_risk_free_then_save50_are_independent(self):
        rf=self.apply(initial_position(Decimal("90")),Kind.RISK_FREE,entry="100")
        saved=self.apply(rf.state,Kind.SAVE_PROFIT_50,"20")
        self.assertTrue(saved.state.risk_free_active)
        self.assertTrue(saved.state.save_profit_50_used)
        self.assertEqual((saved.state.remaining_fraction,saved.state.realized_weighted_roi),(Decimal("0.5"),Decimal("10.0")))

    def test_full_close_and_save50_then_full_close(self):
        full=self.apply(initial_position(Decimal("90")),Kind.FULL_CLOSE,"25")
        self.assertEqual(full.state.realized_weighted_roi,Decimal("25"))
        saved=self.apply(initial_position(Decimal("90")),Kind.SAVE_PROFIT_50,"20")
        later=self.apply(saved.state,Kind.FULL_CLOSE,"30")
        self.assertEqual(later.state.realized_weighted_roi,Decimal("25.0"))

    def test_invalid_transitions(self):
        saved=self.apply(initial_position(Decimal("90")),Kind.SAVE_PROFIT_50,"20").state
        with self.assertRaises(InvalidPositionTransition):self.apply(saved,Kind.SAVE_PROFIT_50,"20")
        rf=self.apply(saved,Kind.RISK_FREE,entry="100").state
        with self.assertRaises(InvalidPositionTransition):self.apply(rf,Kind.RISK_FREE,entry="100")
        with self.assertRaises(InvalidPositionTransition):self.apply(rf,Kind.STOP_LOSS_CLOSE,"-10")
        with self.assertRaises(InvalidPositionTransition):self.apply(initial_position(Decimal("90")),Kind.RISK_FREE_HIT_CLOSE)
        closed=self.apply(rf,Kind.RISK_FREE_HIT_CLOSE).state
        with self.assertRaises(InvalidPositionTransition):self.apply(closed,Kind.NORMAL_TP,"20")

    def test_open_and_closed_shapes_are_exhaustive(self):
        base=(Decimal("0"),False,False,False,Decimal("90"))
        invalid=(
            (Decimal("1"),False,NOW),  # open with closed_at
            (Decimal("0"),True,None),  # closed without closed_at
            (Decimal(".5"),True,NOW),  # closed with remaining volume
            (Decimal("0"),False,None), # open without remaining volume
        )
        for remaining,is_closed,closed_at in invalid:
            with self.subTest(remaining=remaining,is_closed=is_closed,closed_at=closed_at):
                with self.assertRaises(InvalidPositionTransition):
                    PositionState(remaining,*base,is_closed=is_closed,closed_at=closed_at)
