from pathlib import Path

from playwright.sync_api import expect

from qqq_viewer.opening_range_report import write_report
from tests.browser.test_viewer import page  # noqa: F401

DATE = "2020-01-02"
START = 1577975400


def report_fixture(path: Path, *, tick_size=.01) -> None:
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
    if tick_size < .01:
        events[0]["entry_trigger"] = 100.004
        events[1]["entry_trigger"] = 100.003
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
    legacy_stress = stress | dict(trades=[], events=[], equity=initial,
                                  stats=stress["stats"] | {"trades": 0, "ending_balance": 100000})
    scenarios = [case | dict(id=f"{version}-{case['id']}", scenario_id=case["id"], rule_version=version)
                 for version, cases in (("legacy", [base, legacy_stress]), ("revised", [base, stress]))
                 for case in cases]
    bars = [[stamp(i), 105, 110, 100, 105, 1000, 105] for i in range(210)]
    bars[26] = [stamp(26), 105, 111, 102, 109.99, 1000, 105]
    feb = START + 32 * 86400
    write_report(path, dict(
        ticker="QQQ", start=DATE, end=DATE, settings=dict(starting_balance=100_000, risk_pct=.25,
        leverage=4, slippage_bps=1, slippage_floor=.01, tick_size=tick_size), benchmark=initial, benchmark_pct=0,
        bars=216, input_bars=216, input_sha256="fixture", sessions=3, scenarios=scenarios,
        months={
            "2020-01": [dict(date=DATE, close=stamp(210), range_high=110, range_low=100, bars=bars),
                        dict(date="2020-01-03", close=START + 86400 + 180, range_high=110, range_low=100,
                             bars=[[START + 86400 + i * 60, 105, 110, 100, 105, 1000, 105] for i in range(3)])],
            "2020-02": [dict(date="2020-02-03", close=feb + 180, range_high=110, range_low=100,
                             bars=[[feb + i * 60, 105, 110, 100, 105, 1000, 105] for i in range(3)])],
        },
    ))


def test_report_selects_distinct_same_day_trades_and_order_windows(tmp_path, page):
    path = tmp_path / "report.html"
    report_fixture(path)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(path.as_uri())
    expect(page.locator("#run-select")).to_have_value("both-base")
    assert page.locator("#trades th").all_text_contents() == [
        "Number", "Date", "Entry", "Entry Time", "Entry Price", "Exit Time", "Exit Price",
        "Shares", "PnL", "Comm", "Slippage", "ATR/Close %", "MFE %",
    ]
    rows = page.locator("#trades tbody tr")
    rows.nth(0).click()
    expect(page.locator("#selected-trade")).to_contain_text("Entry 1")
    expect(page.locator("#selected-trade")).to_contain_text("$102.00")
    expect(page.locator("#selected-trade")).to_contain_text("Buy 09:47 at $106.0000")
    assert page.evaluate("stopLine.data().map(p => p.value)") == [102, 102]
    expect(page.locator("#selected-trade")).to_contain_text("Working 09:46–09:47")
    assert page.evaluate("""() => {
        const windows = [equity, drawdown].map(chart => chart.options().localization.timeFormatter);
        return windows.every(format => format(1577976600).includes("09:50")
                                      && format(1577977200).includes("10:00"));
    }""")
    assert page.evaluate("""() => overlays.some(line => {
        const points = line.data();
        return line.options().title === "Initial stop" && points[0].value === 102
            && points[0].time === 1577976360 && points.at(-1).time === 1577976420;
    })""")
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



def test_report_keeps_subcent_order_triggers_distinct(tmp_path, page):
    path = tmp_path / "subcent.html"
    report_fixture(path, tick_size=.001)
    page.goto(path.as_uri())
    page.locator("#order-select").select_option(f"{DATE}:1")
    expect(page.locator("#selected-trade")).to_contain_text("Buy stop $100.004")
    assert page.evaluate("candles.options().priceFormat.minMove") == .001
    assert page.evaluate("candles.options().priceFormat.precision") == 3
    assert page.evaluate("orderPrice(100.004) !== orderPrice(100.003)")


def test_rule_switch_keeps_scenario_and_month_and_refreshes_views(tmp_path, page):
    path = tmp_path / "rules.html"
    report_fixture(path)
    page.goto(path.as_uri())
    rules = page.locator("#rules-select")
    expect(rules).to_have_value("revised")
    rows = page.locator("#trades tbody tr")
    entry = page.get_by_role("columnheader", name="Entry", exact=True)
    entry.click()
    assert page.locator("#trades tbody td:nth-child(3)").all_text_contents() == ["1", "2"]
    entry.click()
    assert page.locator("#trades tbody td:nth-child(3)").all_text_contents() == ["2", "1"]
    expect(rows.first.locator("td").nth(8)).to_have_class("positive")
    page.locator("#run-select").select_option("both-stress")
    expect(page.locator("#order-select option")).to_have_count(3)
    page.locator("#month").select_option("2020-02")
    rules.select_option("legacy")
    expect(page.locator("#month")).to_have_value("2020-02")
    expect(page.locator("#run-select")).to_have_value("both-stress")
    expect(page.locator("#summary")).to_contain_text("$100,000.00")
    expect(rows.first.locator("td")).to_have_attribute("colspan", "13")
    assert page.evaluate("equityLine.data().at(-1).value") == 100000
    page.locator("#month").select_option("2020-01")
    expect(page.locator("#order-select option")).to_have_count(1)


def test_rid_resets_and_visibility_persists(tmp_path, page):
    path = tmp_path / "rid.html"
    report_fixture(path)
    page.goto(path.as_uri())
    toggle = page.get_by_role("checkbox", name="RID [LazyBear]")
    expect(toggle).to_be_checked()
    gray_band_visible = """() => {
        const canvas = price.takeScreenshot();
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        for (let i = 0; i < pixels.length; i += 4) {
            if (pixels[i] === 144 && pixels[i + 1] === 164 && pixels[i + 2] === 174) return true;
        }
        return false;
    }"""
    page.wait_for_function(gray_band_visible)
    segments = page.evaluate("ridSegments")
    assert segments[:3] == [
        dict(first=START, last=START + 24 * 60, upper=110, lower=100),
        dict(first=START + 25 * 60, last=START + 25 * 60, upper=110, lower=100),
        dict(first=START + 26 * 60, last=START + 50 * 60, upper=111, lower=102),
    ]
    assert segments[-2]["last"] == START + 209 * 60
    assert segments[-1] == dict(first=START + 86400, last=START + 86400 + 120, upper=110, lower=100)
    assert page.evaluate("""() => [200.01, 200.02, 200.009].map((close, index) => {
        const [high, low] = index === 1 ? [201, 200.01] : [200.02, 199];
        return ridBands([{bars: [[0, low, high, low, low], [60, low, high, low, close]]}]).length;
    })""") == [2, 2, 1]
    toggle.uncheck()
    page.locator("#rules-select").select_option("legacy")
    expect(toggle).not_to_be_checked()
    assert page.evaluate("ridSegments") == segments
    page.wait_for_function(f"() => !({gray_band_visible})()")
    toggle.check()
    expect(toggle).to_be_checked()
    page.wait_for_function(gray_band_visible)
