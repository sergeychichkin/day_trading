from pathlib import Path

from playwright.sync_api import expect

from qqq_viewer.opening_range_report import write_report
from tests.browser.test_viewer import page  # noqa: F401

DATE = "2020-01-02"
START = 1577975400


def report_fixture(path: Path) -> None:
    def stamp(i):
        return START + i * 60

    def event(action, order_id, minute, signal, entry, stop, previous=None):
        return dict(action=action, date=DATE, order_id=order_id, time=stamp(minute),
                    signal_time=stamp(signal), entry_type=entry, entry_trigger=106, initial_stop=stop,
                    shares=10, prior_order_id=previous, reason="noon" if action == "canceled" else None)

    def trade(order_id, signal, fill, exit_minute, entry, stop):
        return dict(date=DATE, order_id=order_id, entry_type=entry, signal_time=stamp(signal),
                    entry_time=stamp(fill), exit_time=stamp(exit_minute), shares=10, entry_price=106,
                    exit_price=107, pnl=8, commission=2, slippage=.2, atr_close_pct=1,
                    mfe_pct=.5, initial_stop=stop, entry_trigger=106, exit_reason="stop",
                    stop_updates=[] if order_id == 2 else [[stamp(fill + 1), stop + 1]], ambiguous=False)

    events = [
        event("placed", 1, 5, 4, "entry2", 101),
        event("replaced", 2, 16, 15, "entry1", 102, 1),
        event("filled", 2, 17, 15, "entry1", 102),
        event("placed", 3, 26, 25, "entry2", 103),
        event("filled", 3, 27, 25, "entry2", 103),
        event("placed", 4, 41, 40, "entry1", 104),
        event("canceled", 4, 150, 40, "entry1", 104),
    ]
    trades = [trade(2, 15, 17, 20, "entry1", 102), trade(3, 25, 27, 30, "entry2", 103)]
    initial = [{"time": START, "value": 100_000}]
    base = dict(id="both-base", label="Both entries", entries=["entry1", "entry2"],
                slippage_multiplier=1, trades=trades, events=events, equity=initial + [
                    {"time": stamp(20), "value": 100008}, {"time": stamp(30), "value": 100016},
                ], stats=dict(trades=2, wins=2, net=16, ending_balance=100016, return_pct=.016,
                              max_drawdown_pct=0, profit_factor=None, avg_trade=8, commission=4,
                              slippage=.4, ambiguous=0), trades_without_daily_atr=0)
    stress = base | dict(id="both-stress", slippage_multiplier=2, trades=trades[:1], events=events[:3],
                         equity=initial + [{"time": stamp(20), "value": 100008}],
                         stats=base["stats"] | {"trades": 1, "ending_balance": 100008})
    write_report(path, dict(
        ticker="QQQ", start=DATE, end=DATE, settings=dict(starting_balance=100_000, risk_pct=.25,
        leverage=4, slippage_bps=1, slippage_floor=.01), benchmark=initial, benchmark_pct=0,
        bars=210, input_bars=210, input_sha256="fixture", sessions=1, scenarios=[base, stress],
        months={"2020-01": [dict(date=DATE, close=stamp(210), range_high=110, range_low=100,
                                 bars=[[stamp(i), 105, 110, 100, 105, 1000, 105] for i in range(210)])]},
    ))


def test_report_selects_distinct_same_day_trades_and_order_windows(tmp_path, page):
    path = tmp_path / "report.html"
    report_fixture(path)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    expect(page.locator("#run-select")).to_have_value("both-base")
    assert page.locator("#trades th").all_text_contents() == [
        "Number", "Date", "Entry Time", "Entry Price", "Exit Time", "Exit Price",
        "Shares", "PnL", "Comm", "Slippage", "ATR/Close %", "MFE %",
    ]
    rows = page.locator("#trades tbody tr")
    rows.nth(0).click()
    expect(page.locator("#selected-trade")).to_contain_text("Entry 1")
    expect(page.locator("#selected-trade")).to_contain_text("$102.00")
    expect(page.locator("#selected-trade")).to_contain_text("Buy 09:47 at $106.0000")
    assert page.evaluate("stopLine.data().map(p => p.value)") == [102, 102]
    assert page.evaluate("rangeLines.map(series => series.data()[0].time)") == [START + 900] * 2
    rows.nth(1).click()
    expect(page.locator("#selected-trade")).to_contain_text("Entry 2")
    expect(page.locator("#selected-trade")).to_contain_text("$103.00")
    assert page.evaluate("stopLine.data().map(p => p.value)") == [103, 104, 104]
    page.locator("#order-select").select_option(f"{DATE}:1")
    expect(page.locator("#selected-trade")).to_contain_text("09:35–09:46")
    expect(page.locator("#selected-trade")).to_contain_text("Replaced")
    expect(page.locator("#selected-trade")).to_contain_text("Buy stop $106.00")
    page.locator("#order-select").select_option(f"{DATE}:4")
    expect(page.locator("#selected-trade")).to_contain_text("10:11–12:00")
    expect(page.locator("#selected-trade")).to_contain_text("Canceled")
    page.locator("#run-select").select_option("both-stress")
    expect(rows).to_have_count(1)
    expect(page.locator("#summary")).to_contain_text("$100,008.00")
    assert page.evaluate("equityLine.data().at(-1).value") == 100008
    assert not errors
