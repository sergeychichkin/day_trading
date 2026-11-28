import json
from datetime import date

from playwright.sync_api import expect

from tests.test_backtest_5m import replay_files


def capture_price(page):
    page.add_init_script("""
        Object.defineProperty(window, 'LightweightCharts', {
            configurable: true,
            set(library) {
                Object.defineProperty(window, 'LightweightCharts', {value: {
                    ...library,
                    createChart(element, options) {
                        const chart = library.createChart(element, options);
                        if (element.id !== 'price') return chart;
                        window.spyPriceChart = chart;
                        window.spyPriceLines = [];
                        window.spyPriceLineOptions = [];
                        window.spyNamedSeries = {};
                        window.spyOrderSeries = new Set();
                        const makeCandle = chart.addCandlestickSeries.bind(chart);
                        const makeLine = chart.addLineSeries.bind(chart);
                        const makeVolume = chart.addHistogramSeries.bind(chart);
                        const remove = chart.removeSeries.bind(chart);
                        chart.removeSeries = series => {
                            window.spyOrderSeries.delete(series);
                            return remove(series);
                        };
                        chart.addCandlestickSeries = opts => {
                            const series = makeCandle(opts);
                            const setData = series.setData.bind(series);
                            series.setData = points => {
                                window.spyCandles = points;
                                return setData(points);
                            };
                            const setMarkers = series.setMarkers.bind(series);
                            series.setMarkers = markers => {
                                window.spyMarkers = markers;
                                return setMarkers(markers);
                            };
                            return series;
                        };
                        chart.addHistogramSeries = opts => {
                            const series = makeVolume(opts);
                            const setData = series.setData.bind(series);
                            series.setData = points => {
                                window.spyVolume = {points, scale: series.options().priceScaleId};
                                return setData(points);
                            };
                            return series;
                        };
                        chart.addLineSeries = opts => {
                            const series = makeLine(opts);
                            if (opts.title) window.spyNamedSeries[opts.title] = series;
                            if (opts.title === 'Buy stop') window.spyOrderSeries.add(series);
                            const setData = series.setData.bind(series);
                            series.setData = points => {
                                window.spyPriceLines.push(points);
                                window.spyPriceLineOptions.push(series.options());
                                return setData(points);
                            };
                            return series;
                        };
                        return chart;
                    },
                }});
            },
        });
    """)


def test_report_draws_buy_stop_through_overnight_fill_or_expiration(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report
    from equity_backtest.history_5m import sessions

    store, _, config = replay_files
    friday, monday = date(2025, 11, 28), date(2025, 12, 1)
    before, after = sessions(friday, monday)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=102,high=104,close=103 WHERE time=?", (before.open_utc + 1200,))
        db.execute("UPDATE bars SET open=105,high=106,close=105 WHERE time=?", (after.open_utc,))
    data = run(load_settings(config), friday, monday)
    filled, unfilled = data["scenarios"]
    submitted = next(event for event in filled["events"] if event["action"] == "submitted")
    unfilled["events"] = [event for event in filled["events"] if event.get("order_id") == submitted["order_id"]
                          or (event["action"] == "signal" and event["date"] == str(friday))]
    unfilled["events"][-1] = {**unfilled["events"][-1], "action": "expired", "time": after.open_utc + 300}
    unfilled["trades"] = []
    output = tmp_path / "order-lifetime.html"
    write_report(output, data)
    capture_price(page)
    page.goto(output.as_uri())
    page.locator("#month").select_option("2025-11")

    def order_points():
        return page.evaluate("[...spyOrderSeries].map(series => series.data())")

    points, = [points for points in order_points() if points[0]["time"] == submitted["time"]]
    assert points[0] == {"time": submitted["time"], "value": submitted["trigger"]}
    assert points[-1]["time"] == after.open_utc
    assert {("Signal", before.open_utc + 600), ("Buy", after.open_utc)} <= {
        (marker["text"], marker["time"]) for marker in page.evaluate("spyMarkers")
    }
    expect(page.locator("#trades tbody tr").first.locator("td").nth(1)).to_contain_text("Nov 28, 2025")
    page.locator("#scenario").select_option("next_session_open")
    points, = order_points()
    assert points[-1]["time"] == after.open_utc + 300
    assert all(point["value"] == submitted["trigger"] for point in points)
    assert before.open_utc + 600 in page.evaluate("spyMarkers.filter(m => m.text === 'Signal').map(m => m.time)")


def test_report_shows_pending_order_submitted_on_closing_candle(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report
    from equity_backtest.history_5m import sessions

    store, _, config = replay_files
    friday = date(2025, 11, 28)
    session, = sessions(friday, friday)
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET close=open WHERE time=?", (session.open_utc + 900,))
        db.execute("UPDATE bars SET open=102,high=105 WHERE time=?", (session.close_utc - 300,))
    data = run(load_settings(config), friday, friday)
    output = tmp_path / "pending-close.html"
    write_report(output, data)
    capture_price(page)
    page.goto(output.as_uri())
    expect(page.locator("#pending-entry")).to_contain_text("Dec 1, 2025, 09:35")
    assert page.evaluate("[...spyOrderSeries][0].data().map(p => p.time)") == [session.close_utc]
    page.wait_for_function("close => spyPriceChart.timeScale().getVisibleRange()?.to === close", arg=session.close_utc)


def test_report_switches_exit_modes_and_charts_trade_and_tail_exit(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report
    from equity_backtest.history_5m import sessions

    store, _, config = replay_files
    friday = date(2025, 11, 28)
    monday, = sessions(date(2025, 12, 1), date(2025, 12, 1))
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=106,high=106,close=106 WHERE time=?", (monday.open_utc,))
    with config.open("a", encoding="utf-8") as stream:
        stream.write("[account]\nslippage_floor=0.005\n")
    data = run(load_settings(config), friday, friday)
    output = tmp_path / "offline.html"
    write_report(output, data)
    page.context.set_offline(True)
    capture_price(page)
    page.goto(output.as_uri())
    expect(page.locator("#scenarios tbody tr")).to_have_count(2)
    expect(page.locator("#trades tbody tr")).to_have_count(1)
    expect(page.locator("#scenario")).to_have_value("same_day_close")
    expect(page.locator("#month option")).to_have_text(["2025-11"])
    expect(page.locator("#execution")).to_contain_text("$0.005")
    expect(page.locator("#execution")).to_contain_text("Initial stop uses the signal session's daily Lower")
    expect(page.locator("#trades tbody td").nth(4)).to_have_text("98.00")
    expect(page.locator("#trades th").nth(7)).to_have_text("Days")
    expect(page.locator("#trades tbody td").nth(7)).to_have_text("1")
    expect(page.locator("#trades th button").first).to_have_css("cursor", "pointer")
    expect(page.get_by_role("columnheader", name="Bear candle")).to_have_count(0)
    expect(page.locator("#trades .negative")).to_have_css("color", "rgb(255, 138, 138)")
    expect(page.get_by_role("columnheader", name="Profit factor")).to_be_visible()
    for scenario, factor in [("Same-day close", "0.00"), ("Next-session open", "∞")]:
        expect(page.get_by_role("row").filter(has_text=scenario).get_by_role("cell", name=factor, exact=True)).to_be_visible()
    expected_equity = [case["stats"]["ending_equity"] for case in data["scenarios"]]
    assert expected_equity[0] != expected_equity[1]
    expect(page.locator("#ending-equity")).to_have_text(f"${expected_equity[0]:,.2f}")
    page.locator("#scenario").select_option("next_session_open")
    expect(page.locator("#trades tbody td").nth(7)).to_have_text("2")
    expect(page.locator("#ending-equity")).to_have_text(f"${expected_equity[1]:,.2f}")
    expect(page.locator("#trades .positive")).to_have_css("color", "rgb(88, 218, 186)")
    page.get_by_role("button", name="Show entry").press("Enter")
    expect(page.locator("#month")).to_have_value("2025-11")
    expect(page.locator("#trade-chart")).to_be_in_viewport()
    expect(page.locator("#chart-legend")).to_contain_text("Upper 101.00")
    expect(page.locator("#chart-legend")).to_contain_text("Nov 28, 2025")
    expect(page.locator("#chart-legend")).to_contain_text("Vol 10 shares")
    assert "Buy" in page.evaluate("spyMarkers.map(marker => marker.text)")
    trigger = data["scenarios"][1]["trades"][0]["initial_stop"]
    assert any(len(points) > 1 and all(point["value"] == trigger for point in points) and options["lineStyle"] == 2
               for points, options in page.evaluate("spyPriceLines.map((p, i) => [p, spyPriceLineOptions[i]])"))
    page.get_by_role("button", name="Show event").first.click()
    expect(page.locator("#selected-trade")).to_contain_text("Order event")
    expect(page.locator("#trade-chart")).to_be_in_viewport()
    page.mouse.move(0, 0)
    page.get_by_role("button", name="Show exit").press("Enter")
    expect(page.locator("#month")).to_have_value("2025-11")
    expect(page.locator("#chart-legend")).to_contain_text("Dec 1, 2025")
    expect(page.locator("#selected-trade")).to_contain_text("0 of 20 following sessions")
    assert "Next open" in page.evaluate("spyMarkers.map(marker => marker.text)")
    page.locator("#scenario").select_option("same_day_close")
    expect(page.locator("#month option")).to_have_text(["2025-11"])
    page.locator("#scenario").select_option("next_session_open")
    page.get_by_role("button", name="Show exit").click()
    expect(page.locator("#month")).to_have_value("2025-11")
    expect(page.locator("#execution")).to_contain_text("Ambiguous")
    expect(page.locator("#trades thead")).to_contain_text("Commission")
    expect(page.locator("#trades thead")).not_to_contain_text("Adverse gap vs. trigger/stop")
    labels = page.evaluate("""() => {
        const format = spyPriceChart.timeScale().options().tickMarkFormatter;
        return [0 + Date.UTC(2025, 0, 6, 14, 30)/1000, Date.UTC(2025, 6, 7, 13, 30)/1000]
            .map(time => format?.(time, 3, "en-US"));
    }""")
    assert all(label and "09:30" in label for label in labels)


def test_trade_log_sorts_numbers_text_dates_and_missing_values(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report

    _, _, config = replay_files
    first, last = "2025-11-26", "2025-11-28"
    data = run(load_settings(config), date.fromisoformat(first), date.fromisoformat(last))
    trades = data["scenarios"][0]["trades"]
    trades[0].update(pnl=100, exit_reason="same_day_close", prior_daily=None, holding_days=10)
    trades[1].update(pnl=20, exit_reason="protective_stop", prior_daily={"ema20_streak": -5})
    output = tmp_path / "sortable.html"
    write_report(output, data)
    page.goto(output.as_uri())
    log = page.locator("#trades")
    headings = log.locator("th").all_text_contents()
    assert headings[10:12] == ["Net P&L", "Exit reason"]
    assert headings[-4:] == ["Chart", "Commission", "Slippage", "Shares"]
    expect(log.locator("th button")).to_have_count(20)
    expect(log.get_by_role("columnheader", name="Daily ATR20 / Close (%)").locator("button br")).to_have_count(1)
    sessions = log.locator("tbody td:first-child")
    expect(sessions).to_have_text([first, last])

    for name, ascending, descending in (
        ("Net P&L", [last, first], [first, last]),
        ("Days", [last, first], [first, last]),
        ("Exit reason", [last, first], [first, last]),
        ("Daily EMA20 streak", [last, first], [last, first]),
        ("Chart", [first, last], [last, first]),
    ):
        header = log.get_by_role("columnheader", name=name, exact=True)
        control = header.get_by_role("button")
        control.click()
        expect(header).to_have_attribute("aria-sort", "ascending")
        expect(sessions).to_have_text(ascending)
        control.press("Enter")
        expect(header).to_have_attribute("aria-sort", "descending")
        expect(sessions).to_have_text(descending)
        expect(log.locator("th[aria-sort]")).to_have_count(1)

    log.get_by_role("button", name="Show entry", exact=True).first.click()
    expect(page.locator("#selected-trade")).to_contain_text("Nov 28, 2025")
    page.locator("#scenario").select_option("next_session_open")
    expect(sessions).to_have_text([last, first])


def test_trade_chart_shows_full_cached_window_outside_replay(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report
    from equity_backtest.history_5m import sessions
    from tests.test_replay_5m import tape

    store, _, config = replay_files
    friday = date(2025, 11, 28)
    following = sessions(date(2025, 12, 1), date(2026, 1, 31))[:21]
    with store._connection(write=True) as db:
        db.execute("DELETE FROM metadata WHERE key='verified_contiguous_start'")
    for session in following[1:]:
        _, bars = tape(day=session.date)
        store.record(session, "complete", bars)
    store.set_target(following[-1].date)
    with store._connection(write=True) as db:
        db.execute("INSERT INTO metadata VALUES ('verified_contiguous_start', '2025-11-25')")
    data = run(load_settings(config), friday, friday)
    trade = data["scenarios"][1]["trades"][0]
    trade["prior_daily"].update(ema20_streak=1, volume_sma20_ratio=1.25,
                                body_atr20_ratio=0.5, atr20_close_pct=1.75)
    output = tmp_path / "trade-window.html"
    write_report(output, data)
    capture_price(page)
    page.goto(output.as_uri())
    page.locator("#scenario").select_option("next_session_open")
    assert page.locator("#trades tbody td").all_text_contents()[12:16] == ["+1", "1.25", "0.50", "1.75%"]
    prior, _ = sessions(date(2025, 11, 26), friday)
    for control, left, right in [("Show entry", prior, following[-2]), ("Show exit", sessions(friday, friday)[0], following[-1])]:
        page.get_by_role("button", name=control).click()
        page.wait_for_function("""([from, to]) => {
            const range = spyPriceChart.timeScale().getVisibleRange();
            return range?.from === from && range?.to === to;
        }""", arg=[left.open_utc, right.close_utc - 300])
        expect(page.locator("#selected-trade")).not_to_contain_text("Cache coverage")


def test_report_handles_no_trades(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report

    _, daily, config = replay_files
    payload = json.loads(daily.read_bytes())
    for row in payload["bars"]:
        row["upper"] = 120
    daily.write_text(json.dumps(payload), encoding="utf-8")
    day = date(2025, 11, 28)
    output = tmp_path / "no-trades.html"
    write_report(output, run(load_settings(config), day, day))
    page.goto(output.as_uri())
    expect(page.locator("#trades")).to_contain_text("No trades for this exit scenario")
    expect(page.locator("#trades tbody td")).to_have_attribute("colspan", "20")
    expect(page.locator("#ending-equity")).to_have_text("$100,000.00")
    expect(page.locator("#scenarios tbody tr").first.get_by_role("cell", name="—", exact=True)).to_have_count(2)


def test_stop_and_ambiguity_have_distinct_chart_markers(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report
    from equity_backtest.history_5m import sessions

    store, _, config = replay_files
    day = date(2025, 11, 28)
    session, = sessions(day, day)
    timestamp = session.close_utc - 300
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=100,high=101,low=99,close=100 WHERE time BETWEEN ? AND ?",
                   (session.open_utc + 900, timestamp - 600))
        db.execute("UPDATE bars SET open=101.5,high=102,low=100,close=101 WHERE time=?", (timestamp - 300,))
        db.execute("UPDATE bars SET open=101,high=105,low=96,close=104 WHERE time=?", (timestamp,))
    output = tmp_path / "ambiguous.html"
    data = run(load_settings(config), day, day)
    write_report(output, data)
    capture_price(page)
    page.goto(output.as_uri())
    expect(page.locator("#trade-count")).to_have_text("1")
    markers = page.evaluate("spyMarkers")
    assert {m["text"] for m in markers if m["time"] == timestamp} == {"Buy", "Stop · ambiguous"}
    stop = data["scenarios"][0]["trades"][0]["initial_stop"]
    assert any(points == [{"time": timestamp, "value": stop}] and options.get("pointMarkersVisible")
               for points, options in page.evaluate("spyPriceLines.map((p, i) => [p, spyPriceLineOptions[i]])"))


def test_report_two_months_and_independent_overlays(page, replay_files, tmp_path):
    from dataclasses import replace

    from equity_backtest.backtest_5m import ChartSettings, load_settings, run
    from equity_backtest.report_5m import write_report

    store, _, config = replay_files
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET volume=40 WHERE date='2025-12-01'")
    settings = replace(load_settings(config), chart=ChartSettings(0.75, 1.25))
    payload = run(settings)
    november = payload["months"]["2025-11"]
    december = payload["months"]["2025-12"]
    output = tmp_path / "overlays.html"
    write_report(output, payload)
    capture_price(page)
    page.goto(output.as_uri())
    picker = page.locator("#month")
    picker.select_option("2025-11")
    assert page.evaluate("spyCandles[0].time") == november[0]["open_utc"]
    assert page.evaluate("spyCandles.at(-1).time") == december[-1]["close_utc"] - 300
    high = page.get_by_role("checkbox", name="High + 0.75 ATR", exact=True)
    low = page.get_by_role("checkbox", name="Low − 1.25 ATR", exact=True)
    expect(high).not_to_be_checked()
    expect(low).not_to_be_checked()
    titles = ["High + 0.75 ATR", "Low − 1.25 ATR"]
    visibility = "titles => titles.map(title => spyNamedSeries[title].options().visible)"
    assert page.evaluate(visibility, titles) == [False, False]
    assert page.evaluate("spyVolume.points.length") == page.evaluate("spyCandles.length")
    assert page.evaluate("""() => spyPriceLineOptions.some(o => o.priceScaleId === spyVolume.scale
        && o.title === 'Volume SMA(20)')""")
    assert page.evaluate("""time => spyPriceLines[spyPriceLineOptions.findLastIndex(
        o => o.title === 'Volume SMA(20)')].find(point => point.time === time).value""",
                         december[0]["open_utc"]) == 11.5
    high.check()
    expect(low).not_to_be_checked()
    assert page.evaluate(visibility, titles) == [True, False]
    active_bar = november[0]["bars"][19]
    assert page.evaluate("""() => spyPriceLines[spyPriceLineOptions.findLastIndex(
        o => o.title === 'High + 0.75 ATR')][0]""") == {
        "time": active_bar["time"], "value": active_bar["high"] + 0.75 * active_bar["atr"],
    }
    low.check()
    high.uncheck()
    expect(low).to_be_checked()
    assert page.evaluate(visibility, titles) == [False, True]
    assert page.evaluate("""() => spyPriceLines[spyPriceLineOptions.findLastIndex(
        o => o.title === 'Low − 1.25 ATR')][0].value""") == active_bar["low"] - 1.25 * active_bar["atr"]
    boundary = page.evaluate("time => spyCandles.findIndex(bar => bar.time === time)", december[0]["open_utc"])
    chart = page.locator("#price")
    chart.scroll_into_view_if_needed()
    page.evaluate("index => spyPriceChart.timeScale().setVisibleLogicalRange({from: index - 60, to: index - 1})",
                  boundary)
    page.wait_for_function("index => spyPriceChart.timeScale().getVisibleLogicalRange().to < index", arg=boundary)
    box = chart.bounding_box()
    x, y = box["x"], box["y"] + box["height"] / 3
    page.mouse.move(x + 0.75 * box["width"], y)
    page.mouse.down()
    page.mouse.move(x + 0.25 * box["width"], y, steps=10)
    page.mouse.up()
    page.wait_for_function("index => spyPriceChart.timeScale().getVisibleLogicalRange().to > index", arg=boundary)
    expect(picker).to_have_value("2025-11")
    picker.select_option("2025-12")
    assert page.evaluate("spyCandles[0].time") == december[0]["open_utc"]
    assert page.evaluate("""() => spyPriceLines[spyPriceLineOptions.findLastIndex(
        o => o.title === 'Volume SMA(20)')][0].value""") == december[0]["bars"][0]["volume_sma_20"]

    high_input = page.get_by_role("spinbutton", name="High ATR multiplier")
    low_input = page.get_by_role("spinbutton", name="Low ATR multiplier")
    expect(high_input).to_have_value("0.75")
    expect(low_input).to_have_value("1.25")
    high_input.fill("0.625")
    low_input.fill("0")
    expect(page.locator("#high-atr")).not_to_be_checked()
    legend = page.locator("#chart-legend")
    expect(legend).not_to_contain_text("High +")
    expect(legend).to_contain_text("Low − 0 ATR (buffer 0.00)")
    page.locator("#high-atr").check()
    expect(legend).to_contain_text("High + 0.625 ATR (buffer")
    bar = december[0]["bars"][0]
    assert page.evaluate("titles => titles.map(title => spyNamedSeries[title].data()[0].value)", titles) == [
        bar["high"] + 0.625 * bar["atr"], bar["low"],
    ]
    high_input.fill("-2")
    high_input.press("Tab")
    expect(high_input).to_have_value("0.625")
    page.locator("#low-atr").uncheck()
    expect(legend).not_to_contain_text("Low −")
    page.locator("#scenario").select_option("next_session_open")
    expect(high_input).to_have_value("0.625")
    expect(page.locator("#high-atr")).to_be_checked()


def test_report_uses_next_calendar_month_across_new_year(page, replay_files, tmp_path):
    from copy import deepcopy
    from datetime import datetime

    from equity_backtest.backtest_5m import load_settings, run
    from qqq_viewer.sessions import NEW_YORK
    from equity_backtest.report_5m import write_report

    _, _, config = replay_files
    payload = run(load_settings(config))
    december, = payload["months"]["2025-12"]
    for month in (1, 3):
        day = datetime(2026, month, 2, 9, 30, tzinfo=NEW_YORK)
        session = deepcopy(december)
        offset = int(day.timestamp()) - december["open_utc"]
        session.update(date=day.date().isoformat(), open_utc=december["open_utc"] + offset,
                       close_utc=december["close_utc"] + offset)
        for bar in session["bars"]:
            bar["time"] += offset
        payload["months"][session["date"][:7]] = [session]
    output = tmp_path / "new-year.html"
    write_report(output, payload)
    capture_price(page)
    page.goto(output.as_uri())
    january, = payload["months"]["2026-01"]
    assert page.evaluate("spyCandles[0].time") == december["open_utc"]
    assert page.evaluate("spyCandles.at(-1).time") == january["close_utc"] - 300
    page.locator("#month").select_option("2026-01")
    assert page.evaluate("spyCandles[0].time") == january["open_utc"]
    assert page.evaluate("spyCandles.at(-1).time") == january["close_utc"] - 300


def test_report_exit3_stop_lines_log_and_open_position(page, replay_files, tmp_path):
    from dataclasses import replace
    from datetime import time

    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report
    from equity_backtest.history_5m import sessions

    store, daily, config = replay_files
    friday, monday = sessions(date(2025, 11, 28), date(2025, 12, 1))
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET low=99 WHERE time=?", (friday.open_utc,))
        db.execute("UPDATE bars SET low=99 WHERE time=?", (monday.open_utc,))
        db.executemany("UPDATE bars SET open=?,high=?,low=?,close=? WHERE time=?", [
            (99.9, 100.8, 99.8, 100.6, monday.open_utc + 900),
            (97, 105, 96, 104, monday.open_utc + 1200),
            (104, 105, 99, 101, monday.open_utc + 1500),
            (107, 108, 100, 107, monday.open_utc + 1800),
        ])
    daily_data = json.loads(daily.read_bytes())
    daily_data["bars"][2].update(open=101, lower=100.9)
    daily.write_text(json.dumps(daily_data), encoding="utf-8")
    settings = replace(load_settings(config), trailing_stop=True, same_day_close=False, next_session_open=False)
    settings = replace(settings, entry=replace(settings.entry, entry1_end_time=time(10, 0)))
    payload = run(settings, date(2025, 11, 26), monday.date)
    scenario, = payload["scenarios"]
    trade, = scenario["trades"]
    output = tmp_path / "exit3.html"
    write_report(output, payload)
    capture_price(page)
    page.goto(output.as_uri())
    page.locator("#month").select_option("2025-11")
    headings = page.locator("#trades th").all_text_contents()
    assert {"MFE (R)", "MAE (R)"}.issubset(headings)
    assert not {"Trigger", "Adverse gap vs. trigger/stop", "Ambiguous"}.intersection(headings)
    cells = page.locator("#trades tbody td").all_text_contents()
    assert cells[0] == "2025-11-26"
    assert cells[1:3] == ["09:40", "09:50"]
    assert "Dec 1, 2025" in cells[5]
    assert cells[7:10] == ["3", f"{trade['mfe_r']:.2f}", f"{trade['mae_r']:.2f}"]
    assert cells[11] == "Exit 3"
    expect(page.locator("#event-month")).to_contain_text("2025-11 through 2025-12")
    row = page.locator("#orders tbody tr").filter(has_text="Exit 3 raised stop")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Triggering candle Dec 1, 2025, 09:45")
    expect(row).to_contain_text("Effective Dec 1, 2025, 09:50")
    expect(row).to_contain_text("2025-11-28 100.9 (verified) / 2025-11-26 98 (verified)")
    expect(row).not_to_contain_text("2025-11-25")
    row.get_by_role("button", name="Show event").press("Enter")
    expect(page.locator("#chart-legend")).to_contain_text("Dec 1, 2025, 09:45")
    page.get_by_role("button", name="Show exit").click()
    expect(page.locator("#month")).to_have_value("2025-11")
    points = [{"time": trade["exit_time"], "value": trade["stop_adjustments"][-1]["new_stop"]}]
    assert points in page.evaluate("spyPriceLines")
    expect(page.locator("#open-position")).to_contain_text("Unrealized P&L")
    expect(page.locator("#open-position")).to_contain_text(scenario["open_position"]["entry_date"])
    expect(page.locator("#summary")).to_contain_text("Ending closed balance")
    expect(page.locator("#summary")).to_contain_text("Realized net P&L")
    expect(page.locator("#summary")).to_contain_text("Max closed-balance drawdown")
    expect(page.locator("#provenance")).to_contain_text('"fingerprinted_bars"')


def test_report_sorts_stop_sources_and_marks_daily_raise_at_effective_open(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report

    _, _, config = replay_files
    payload = run(load_settings(config))
    payload["settings"].update(trailing_stop=True, exit3_max_holding_days=2)
    case = payload["scenarios"][0]
    friday, monday = payload["months"]["2025-11"][-1], payload["months"]["2025-12"][0]
    adjustment = {"candle_time": friday["close_utc"], "effective_time": monday["open_utc"],
                  "old_stop": 98, "new_stop": 100.23, "prior_session_count": 0, "source": 4}
    trade = case["trades"][1]
    case["trades"] = [{**trade, "exit_reason": "protective_stop", "stop_source": source,
                       "exit_time": monday["open_utc"], "exit_date": monday["date"],
                       "stop_adjustments": [adjustment] if source == 4 else []}
                      for source in (5, 3, None, 4)]
    case["events"].append({"time": monday["open_utc"], "date": monday["date"], "action": "stop_adjusted",
                           "detail": "Exit 4 raised stop from 98 to 100.23", "adjustment": adjustment,
                           "trigger": None, "stop": 100.23, "shares": 100})
    output = tmp_path / "shared-stop-reasons.html"
    write_report(output, payload)
    capture_price(page)
    page.goto(output.as_uri())
    expect(page.locator("#execution")).to_contain_text("Exit 3 has no eligible holding days")
    page.locator("#month").select_option("2025-11")
    log = page.locator("#trades")
    reasons = log.locator("tbody td:nth-child(12)")
    expect(reasons).to_have_text(["Exit 5", "Exit 3", "Initial SL", "Exit 4"])
    log.get_by_role("button", name="Exit reason").press("Enter")
    expect(reasons).to_have_text(["Exit 3", "Exit 4", "Exit 5", "Initial SL"])
    row = page.locator("#orders tbody tr").filter(has_text="Exit 4 raised stop")
    expect(row).to_have_count(1)
    expect(row).to_contain_text("Triggering daily close Nov 28, 2025, 13:00")
    expect(row).to_contain_text("Effective Dec 1, 2025, 09:30")
    assert [(marker["time"], marker["text"]) for marker in page.evaluate("spyMarkers")
            if marker["text"].startswith("Exit 4 stop")] == [(monday["open_utc"], "Exit 4 stop 100.23")]


def test_report_labels_skips_as_new_entry_outcomes(page, replay_files, tmp_path):
    from equity_backtest.backtest_5m import load_settings, run
    from equity_backtest.report_5m import write_report

    _, _, config = replay_files
    payload = run(load_settings(config))
    payload["scenarios"][0]["skips"]["no_signal"] = 1
    output = tmp_path / "entry-skips.html"
    write_report(output, payload)
    page.goto(output.as_uri())
    expect(page.locator("#skips")).to_contain_text("New-entry skips")
