from datetime import timezone
from decimal import Decimal

from exchanges.weex import WEEXAdapter
from market import composite_price_analysis


def test_weex_catalog_uses_market_contracts_not_api_trading_allowlist():
    info={"data":{"symbols":[
        {"symbol":"BTCUSDT","baseAsset":"BTC","quoteAsset":"USDT","marginAsset":"USDT","contractType":"PERPETUAL","forwardContractFlag":True},
        {"symbol":"ETHUSDT","baseAsset":"ETH","quoteAsset":"USDT","marginAsset":"USDT","contractType":"PERPETUAL","forwardContractFlag":True},
        {"symbol":"BTCUSDC","baseAsset":"BTC","quoteAsset":"USDC","marginAsset":"USDC","contractType":"PERPETUAL","forwardContractFlag":True},
        {"symbol":"XAUUSDT_TRADIFI","baseAsset":"XAU","quoteAsset":"USDT","marginAsset":"USDT","contractType":"PERPETUAL","forwardContractFlag":True},
    ]}}
    rows=WEEXAdapter.parse_instruments(info)
    assert [(x.exchange_symbol,x.canonical_symbol) for x in rows]==[
        ("BTCUSDT","BTCUSDT"),
        ("ETHUSDT","ETHUSDT"),
    ]


def test_weex_bulk_mark_prices_are_decimal_timestamped_and_isolated():
    payload={"data":[
        {"symbol":"BTCUSDT","markPrice":"67240.125","indexPrice":"1","time":1788220800000},
        {"symbol":"ETHUSDT","markPrice":"NaN","time":1788220800000},
        {"symbol":"SOLUSDT","markPrice":"0","time":1788220800000},
        {"symbol":"BADUSDT","markPrice":"1","time":"bad"},
    ]}
    rows=WEEXAdapter.parse_prices(payload,{"BTCUSDT":"BTCUSDT","ETHUSDT":"ETHUSDT","SOLUSDT":"SOLUSDT","BADUSDT":"BADUSDT"})
    assert len(rows)==1
    assert rows[0].last_price==Decimal("67240.125")
    assert rows[0].source=="rest_mark"
    assert rows[0].received_at.tzinfo is timezone.utc


def test_four_source_robust_policy_and_fallbacks():
    agreeing=composite_price_analysis({"ourbit":Decimal("100"),"toobit":Decimal("101"),"lbank":Decimal("102"),"weex":Decimal("103")},Decimal("5"))
    assert agreeing["reference_price"]==Decimal("101.5")
    outlier=composite_price_analysis({"ourbit":Decimal("100"),"toobit":Decimal("101"),"lbank":Decimal("102"),"weex":Decimal("500")},Decimal("5"))
    assert outlier["reference_price"]==Decimal("101") and outlier["outlier_source"]=="weex"
    split=composite_price_analysis({"ourbit":Decimal("100"),"toobit":Decimal("101"),"lbank":Decimal("200"),"weex":Decimal("202")},Decimal("5"))
    assert split["status"]=="unresolved" and split["reference_price"] is None
    assert composite_price_analysis({"ourbit":Decimal("100"),"toobit":Decimal("102")},Decimal("5"))["reference_price"]==Decimal("101")
    assert composite_price_analysis({"ourbit":Decimal("100")},Decimal("5"))["reference_price"] is None
