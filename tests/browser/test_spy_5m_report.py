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
                        chart.addCandlestickSeries = opts => {
                            const series = makeCandle(opts);
                            const setMarkers = series.setMarkers.bind(series);
                            series.setMarkers = markers => {
                                window.spyMarkers = markers;
                                return setMarkers(markers);
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
    expect(page.locator("#selected-trade")).to_contain_text("Order event:")
    expect(page.locator("#trade-chart")).to_be_in_viewport()
    page.get_by_role("button", name="Show exit").click()
    expect(page.locator("#month")).to_have_value("2025-12")
    expect(page.locator("#chart-legend")).to_contain_text("Dec 1, 2025")
    assert "Next open" in page.evaluate("spyMarkers.map(marker => marker.text)")
    page.locator("#scenario").select_option("same_day_close")
    expect(page.locator("#month option")).to_have_text(["2025-11"])
    page.locator("#scenario").select_option("next_session_open")
    page.get_by_role("button", name="Show exit").click()
    expect(page.locator("#month")).to_have_value("2025-12")
    expect(page.locator("#execution")).to_contain_text("Ambiguous")
    expect(page.locator("#trades thead")).to_contain_text("Commission")
    expect(page.locator("#trades thead")).to_contain_text("Adverse gap vs. trigger/stop")
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
