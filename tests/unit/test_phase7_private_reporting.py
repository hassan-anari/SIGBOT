from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from chainura.application.reporting import _projection, summarize, SignalistTradeHistoryItem, DestinationTradeHistoryItem
from chainura.domain.monitoring import MonitorStage, ResultKind
from chainura.domain.signal import SignalEntryType, SignalLifecycleStatus, SignalMode, TradeDirection
from chainura.telegram.presentation import texts


def signal(status=SignalLifecycleStatus.ACTIVE):
    return SimpleNamespace(id=7, signalist_id=91, canonical_symbol="BTCUSDT",
        direction=TradeDirection.LONG, entry_type=SignalEntryType.MARKET,
        mode=SignalMode.COMPOSITE, status=status, created_at=datetime.now(timezone.utc))

def monitor(result=ResultKind.WIN, stage=MonitorStage.POST_TP1):
    return SimpleNamespace(current_result_kind=result,current_leveraged_roi=Decimal("999"),
        highest_tp_index=1,stage=stage)

def position(value,is_closed):
    return SimpleNamespace(realized_weighted_roi=Decimal(value),is_closed=is_closed)

def test_weighted_roi_is_authoritative_and_exact():
    win=_projection(signal(),monitor(ResultKind.LOSS),position("5.123456789",True))
    loss=_projection(signal(),monitor(ResultKind.WIN),position("-1.25",True))
    neutral=_projection(signal(),monitor(ResultKind.WIN),position("0",True))
    summary=summarize((win,loss,neutral))
    assert (win.result_kind,loss.result_kind,neutral.result_kind)==(ResultKind.WIN,ResultKind.LOSS,ResultKind.NEUTRAL)
    assert summary.total_roi==Decimal("3.873456789")

def test_open_tp_can_win_but_pre_tp_has_no_fake_zero_roi():
    tp=_projection(signal(),monitor(),position("0",False))
    pre=_projection(signal(),monitor(None,MonitorStage.PRE_TP1),position("0",False))
    assert tp.open and tp.result_kind is ResultKind.WIN and tp.report_roi is None
    assert pre.open and pre.result_kind is None and pre.report_roi is None

def test_cancelled_is_excluded_from_performance():
    item=_projection(signal(SignalLifecycleStatus.CANCELLED),monitor(),position("20",True))
    summary=summarize((item,))
    assert summary.cancelled_signals==1 and summary.resulted_signals==0 and summary.total_roi==0

def test_private_formatters_structurally_have_no_cross_identity_fields():
    now=datetime.now(timezone.utc)
    personal=SignalistTradeHistoryItem(1,"BTCUSDT","long","market","Composite",now,"Loss",1,Decimal("-2"))
    destination=DestinationTradeHistoryItem(1,"BTCUSDT","long","market","Composite","ourbit",now,"Loss",1,Decimal("-2"))
    assert not hasattr(personal,"destination_id") and not hasattr(personal,"confirmed_published")
    assert not hasattr(destination,"signalist_display_name") and not hasattr(destination,"owner_signalist_id")
    page=SimpleNamespace(items=(personal,),page=1,pages=1)
    rendered=texts.private_history_text(page)
    for forbidden in ("Destination","مقصد","انتشار","ارسال"):
        assert forbidden not in rendered
