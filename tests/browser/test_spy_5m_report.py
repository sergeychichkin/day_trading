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
    expect(page.locator("#trades th").first).to_have_css("cursor", "default")
    expect(page.get_by_role("columnheader", name="Bear candle")).to_be_visible()
    expect(page.locator("#trades tbody td").nth(2)).to_contain_text("09:45")
    expect(page.get_by_role("columnheader", name="Profit factor")).to_be_visible()
    for scenario, factor in [("Same-day close", "0.00"), ("Next-session open", "∞")]:
        expect(page.get_by_role("row").filter(has_text=scenario).get_by_role("cell", name=factor, exact=True)).to_be_visible()
    expected_equity = [case["stats"]["ending_equity"] for case in data["scenarios"]]
    assert expected_equity[0] != expected_equity[1]
    expect(page.locator("#ending-equity")).to_have_text(f"${expected_equity[0]:,.2f}")
    page.locator("#scenario").select_option("next_session_open")
    expect(page.locator("#ending-equity")).to_have_text(f"${expected_equity[1]:,.2f}")
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
    expect(page.locator("#trades tbody td")).to_have_attribute("colspan", "16")
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
    assert page.evaluate("spyVolume.points.length") == page.evaluate("spyCandles.length")
    assert page.evaluate("""() => spyPriceLineOptions.some(o => o.priceScaleId === spyVolume.scale
        && o.title === 'Volume SMA(20)')""")
    assert page.evaluate("""time => spyPriceLines[spyPriceLineOptions.findLastIndex(
        o => o.title === 'Volume SMA(20)')].find(point => point.time === time).value""",
                         december[0]["open_utc"]) == 11.5
    high.check()
    expect(low).not_to_be_checked()
    active_bar = november[0]["bars"][19]
    assert page.evaluate("""() => spyPriceLines[spyPriceLineOptions.findLastIndex(
        o => o.title === 'High + 0.75 ATR')][0]""") == {
        "time": active_bar["time"], "value": active_bar["high"] + 0.75 * active_bar["atr"],
    }
    low.check()
    high.uncheck()
    expect(low).to_be_checked()
    assert page.evaluate("""() => spyPriceLines[spyPriceLineOptions.findLastIndex(
        o => o.title === 'Low − 1.25 ATR')][0].value""") == active_bar["low"] - 1.25 * active_bar["atr"]
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
        db.execute("UPDATE bars SET open=99,high=100.8,low=99,close=100.2 WHERE time=?",
                   (friday.close_utc - 300,))
        db.execute("UPDATE bars SET open=97,high=105,low=96,close=104 WHERE time=?", (monday.open_utc,))
    daily_data = json.loads(daily.read_bytes())
    daily_data["bars"][1].update(open=101, lower=100.9)
    daily.write_text(json.dumps(daily_data), encoding="utf-8")
    settings = replace(load_settings(config), trailing_stop=True, same_day_close=False, next_session_open=False)
    payload = run(settings, friday.date, monday.date)
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
    assert cells[1:4] == ["09:40", "09:45", "09:50"]
    assert "Dec 1, 2025" in cells[6]
    assert cells[10:12] == [f"{trade['mfe_r']:.2f}", f"{trade['mae_r']:.2f}"]
    expect(page.locator("#event-month")).to_contain_text("2025-11 through 2025-12")
    expect(page.locator("#orders tbody")).to_contain_text("Raised protective stop")
    page.get_by_role("button", name="Show exit").click()
    expect(page.locator("#month")).to_have_value("2025-11")
    points = [{"time": monday.open_utc, "value": trade["stop_adjustments"][-1]["new_stop"]}]
    assert points in page.evaluate("spyPriceLines")
    expect(page.locator("#open-position")).to_contain_text("Unrealized P&L")
    expect(page.locator("#open-position")).to_contain_text(scenario["open_position"]["entry_date"])


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
