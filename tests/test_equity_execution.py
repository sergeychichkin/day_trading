from types import SimpleNamespace


def test_execution_helpers_preserve_existing_formulas():
    from equity_backtest.execution import WilderATR, commission, order_price

    atr = WilderATR(2)
    assert atr.update(SimpleNamespace(high=102, low=100, close=101)) is None
    assert atr.update(SimpleNamespace(high=108, low=106, close=107)) == 4.5
    assert atr.update(SimpleNamespace(high=108, low=106, close=107)) == 3.25
    assert (commission(100, 100), commission(500, 100), commission(10, 0.05)) == (1, 2.5, 0.01)
    assert order_price(100.01000000000002, 0.01, up=True) == 100.01
    assert order_price(100.019, 0.01, up=True) == 100.02
    assert order_price(100.019, 0.01, up=False) == 100.01
