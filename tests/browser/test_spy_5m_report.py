import json
from datetime import date

from playwright.sync_api import expect

from tests.browser.test_viewer import page
from tests.test_backtest_spy_5m import replay_files


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
                        const makeCandle = chart.addCandlestickSeries.bind(chart);
                        const makeLine = chart.addLineSeries.bind(chart);
                        const makeVolume = chart.addHistogramSeries.bind(chart);
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


def test_report_switches_exit_modes_and_charts_trade_and_tail_exit(page, replay_files, tmp_path):
    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.spy_5m_report import write_report

    _, _, config = replay_files
    friday = date(2025, 11, 28)
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
    expect(page.locator("#ending-equity")).to_have_text(f"${expected_equity[1]:,.2f}")
    expect(page.locator("#trades .positive")).to_have_css("color", "rgb(88, 218, 186)")
    page.get_by_role("button", name="Show entry").click()
    expect(page.locator("#month")).to_have_value("2025-11")
    expect(page.locator("#trade-chart")).to_be_in_viewport()
    expect(page.locator("#chart-legend")).to_contain_text("Upper 101.00")
    expect(page.locator("#chart-legend")).to_contain_text("Vol 10 shares")
    assert "Buy" in page.evaluate("spyMarkers.map(marker => marker.text)")
    trigger = data["scenarios"][1]["trades"][0]["initial_stop"]
    assert any(len(points) > 1 and all(point["value"] == trigger for point in points) and options["lineStyle"] == 2
               for points, options in page.evaluate("spyPriceLines.map((p, i) => [p, spyPriceLineOptions[i]])"))
    page.get_by_role("button", name="Show event").first.click()
    expect(page.locator("#selected-trade")).to_contain_text("Order event")
    expect(page.locator("#trade-chart")).to_be_in_viewport()
    page.get_by_role("button", name="Show exit").click()
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
    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.spy_5m_report import write_report

    _, _, config = replay_files
    first, last = "2025-11-26", "2025-11-28"
    data = run(load_settings(config), date.fromisoformat(first), date.fromisoformat(last))
    trades = data["scenarios"][0]["trades"]
    trades[0].update(pnl=100, exit_reason="same_day_close", prior_daily=None)
    trades[1].update(pnl=20, exit_reason="protective_stop", prior_daily={"ema20_streak": -5})
    output = tmp_path / "sortable.html"
    write_report(output, data)
    page.goto(output.as_uri())
    log = page.locator("#trades")
    assert log.locator("th").all_text_contents()[-4:] == ["Exit reason", "Commission", "Slippage", "Shares"]
    expect(log.locator("th button")).to_have_count(19)
    expect(log.get_by_role("columnheader", name="Daily ATR20 / Close (%)").locator("button br")).to_have_count(1)
    sessions = log.locator("tbody td:first-child")
    expect(sessions).to_have_text([first, last])

    for name, ascending, descending in (
        ("Net P&L", [last, first], [first, last]),
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
    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.spy_5m_report import write_report
    from qqq_viewer.spy_5m_store import sessions
    from tests.test_spy_5m_replay import tape

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
    assert page.locator("#trades tbody td").all_text_contents()[10:14] == ["+1", "1.25", "0.50", "1.75%"]
    prior, _ = sessions(date(2025, 11, 26), friday)
    for control, left, right in [("Show entry", prior, following[-2]), ("Show exit", sessions(friday, friday)[0], following[-1])]:
        page.get_by_role("button", name=control).click()
        page.wait_for_function("""([from, to]) => {
            const range = spyPriceChart.timeScale().getVisibleRange();
            return range?.from === from && range?.to === to;
        }""", arg=[left.open_utc, right.close_utc - 300])
        expect(page.locator("#selected-trade")).not_to_contain_text("Cache coverage")


def test_report_handles_no_trades(page, replay_files, tmp_path):
    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.spy_5m_report import write_report

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
    expect(page.locator("#trades tbody td")).to_have_attribute("colspan", "19")
    expect(page.locator("#ending-equity")).to_have_text("$100,000.00")
    expect(page.locator("#scenarios tbody tr").first.get_by_role("cell", name="—", exact=True)).to_have_count(2)


def test_stop_and_ambiguity_have_distinct_chart_markers(page, replay_files, tmp_path):
    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.spy_5m_report import write_report
    from qqq_viewer.spy_5m_store import sessions

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

    from qqq_viewer.backtest_spy_5m import ChartSettings, load_settings, run
    from qqq_viewer.spy_5m_report import write_report

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


def test_report_uses_next_calendar_month_across_new_year(page, replay_files, tmp_path):
    from copy import deepcopy
    from datetime import datetime

    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.sessions import NEW_YORK
    from qqq_viewer.spy_5m_report import write_report

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

    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.spy_5m_report import write_report
    from qqq_viewer.spy_5m_store import sessions

    store, daily, config = replay_files
    friday, monday = sessions(date(2025, 11, 28), date(2025, 12, 1))
    with store._connection(write=True) as db:
        db.execute("UPDATE bars SET open=99,high=100.7,low=98.8,close=100.1 WHERE time=?",
                   (friday.close_utc - 600,))
        db.execute("UPDATE bars SET open=99,high=100.8,low=99,close=100.2 WHERE time=?",
                   (friday.close_utc - 300,))
        db.execute("UPDATE bars SET open=97,high=105,low=96,close=104 WHERE time=?", (monday.open_utc,))
    daily_data = json.loads(daily.read_bytes())
    daily_data["bars"][1].update(open=101, lower=100.9)
    daily.write_text(json.dumps(daily_data), encoding="utf-8")
    settings = replace(load_settings(config), trailing_stop=True, same_day_close=False, next_session_open=False)
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
    assert cells[7:9] == [f"{trade['mfe_r']:.2f}", f"{trade['mae_r']:.2f}"]
    expect(page.locator("#event-month")).to_contain_text("2025-11 through 2025-12")
    rows = page.locator("#orders tbody tr").filter(has_text="Protective stop increase")
    expect(rows).to_have_count(2)
    for row, trigger, effective in (
        (rows.nth(0), "12:50", "Nov 28, 2025, 12:55"),
        (rows.nth(1), "12:55", "Dec 1, 2025, 09:30"),
    ):
        expect(row).to_contain_text(f"Triggering candle Nov 28, 2025, {trigger}")
        expect(row).to_contain_text(f"Effective {effective}")
        expect(row).to_contain_text("2025-11-26 100.9 (verified)")
        expect(row).not_to_contain_text("2025-11-25")
        row.get_by_role("button", name="Show event").click()
        expect(page.locator("#chart-legend")).to_contain_text(f"Nov 28, 2025, {trigger}")
    page.get_by_role("button", name="Show exit").click()
    expect(page.locator("#month")).to_have_value("2025-11")
    points = [{"time": monday.open_utc, "value": trade["stop_adjustments"][-1]["new_stop"]}]
    assert points in page.evaluate("spyPriceLines")
    expect(page.locator("#open-position")).to_contain_text("Unrealized P&L")
    expect(page.locator("#open-position")).to_contain_text(scenario["open_position"]["entry_date"])
    expect(page.locator("#summary")).to_contain_text("Ending closed balance")
    expect(page.locator("#summary")).to_contain_text("Realized net P&L")
    expect(page.locator("#summary")).to_contain_text("Max closed-balance drawdown")
    expect(page.locator("#provenance")).to_contain_text('"fingerprinted_bars"')


def test_report_labels_skips_as_new_entry_outcomes(page, replay_files, tmp_path):
    from qqq_viewer.backtest_spy_5m import load_settings, run
    from qqq_viewer.spy_5m_report import write_report

    _, _, config = replay_files
    payload = run(load_settings(config))
    payload["scenarios"][0]["skips"]["no_signal"] = 1
    output = tmp_path / "entry-skips.html"
    write_report(output, payload)
    page.goto(output.as_uri())
    expect(page.locator("#skips")).to_contain_text("New-entry skips")
