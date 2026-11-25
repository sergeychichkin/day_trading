import json

from playwright.sync_api import expect

from tests.browser.test_viewer import page
from tests.test_spy_server import running_spy_server, snapshot


def capture_series(page):
    def hook(route):
        response = route.fetch()
        route.fulfill(response=response, body=response.text() + """
            const original = window.LightweightCharts;
            window.LightweightCharts = {...original, createChart(...args) {
                const chart = original.createChart(...args);
                window.chartUnderTest = chart;
                window.seriesUnderTest = [];
                for (const method of ['addCandlestickSeries', 'addHistogramSeries', 'addLineSeries']) {
                    const create = chart[method].bind(chart);
                    chart[method] = (...options) => {
                        const series = create(...options);
                        window.seriesUnderTest.push(series);
                        return series;
                    };
                }
                return chart;
            }};
        """)
    page.route("**/static/vendor/lightweight-charts.js", hook)


def test_spy_month_picker_and_indicator_series(page, snapshot):
    cache, _ = snapshot
    capture_series(page)
    with running_spy_server(cache) as url:
        page.goto(f"{url}/spy")
        picker = page.locator("#month")
        expect(picker).to_have_attribute("type", "month")
        expect(picker).to_have_attribute("min", "2000-01")
        expect(picker).to_have_value("2000-04")
        expect(page.locator("#legend-time")).to_contain_text("Apr 3, 2000")
        expect(page.locator("#coverage")).to_contain_text("1 missing")
        page.wait_for_function("""
            () => {
                const range = chartUnderTest.timeScale().getVisibleRange();
                return range?.from === "2000-04-03" && range?.to === "2000-04-04";
            }
        """)
        series = page.evaluate("""seriesUnderTest.map(s => ({
            type: s.seriesType(), scale: s.options().priceScaleId, data: s.data(),
        }))""")
        price = next(s for s in series if s["type"] == "Candlestick")
        volume = next(s for s in series if s["type"] == "Histogram")
        average, = [s for s in series if s["type"] == "Line" and s["scale"] == volume["scale"]]
        assert volume["scale"] != price["scale"]
        assert average["data"][0]["value"] == 750
        price_lines = [s["data"] for s in series if s["type"] == "Line" and s["scale"] == price["scale"]]
        assert sorted(data[0]["value"] for data in price_lines) == [98, 101, 104]

        picker.fill("2000-02")
        expect(page.locator("#legend-time")).to_contain_text("Feb 1, 2000")
        expect(page.locator("#legend-upper")).to_have_text("104.00")
        picker.fill("2000-01")
        expect(page.locator("#legend-time")).to_contain_text("Jan 31, 2000")
        picker.fill("2000-03")
        expect(page.locator("#empty")).to_contain_text("No cached bars")
        picker.fill("")
        expect(page.locator("#empty")).to_contain_text("Select a month")


def test_spy_missing_cache_explains_how_to_download(page, tmp_path):
    with running_spy_server(tmp_path / "absent.json") as url:
        page.goto(f"{url}/spy")
        expect(page.locator("#month")).to_be_disabled()
        expect(page.locator("#empty")).to_contain_text("qqq_viewer.spy_daily")


def capture_strokes(page):
    capture_series(page)
    page.add_init_script("""
        window.spyStrokes = [];
        const ctx = CanvasRenderingContext2D.prototype;
        for (const [name, record] of Object.entries({
            beginPath() { this.strokeXs = []; },
            moveTo(x) { this.strokeXs?.push(x); },
            lineTo(x) { this.strokeXs?.push(x); },
            stroke() {
                if (this.strokeXs?.length >= 2) {
                    window.spyStrokes.push({color: this.strokeStyle, xs: [...this.strokeXs]});
                }
            },
        })) {
            const original = ctx[name];
            ctx[name] = function (...args) {
                record.apply(this, args);
                return original.apply(this, args);
            };
        }
    """)


def april_bars(snapshot):
    cache, payload = snapshot
    days = ["2000-04-03", "2000-04-04", "2000-04-05", "2000-04-06", "2000-04-07"]
    payload["bars"] = [{**payload["bars"][0], "time": day} for day in days]
    payload["through"] = days[-1]
    payload["missing_sessions"] = []
    return cache, payload, days


def show_april(page, url, days):
    page.goto(f"{url}/spy")
    page.wait_for_function("""
        days => chartUnderTest.timeScale().getVisibleRange()?.to === days.at(-1)
          && !!spyStrokes.find(stroke => stroke.color === "#f0bf7f")
    """, arg=days, timeout=5000)
    # Let the range update and its canvas paint finish before reading strokes.
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def test_ema_stroke_color_matches_move_into_each_session(page, snapshot):
    cache, payload, days = april_bars(snapshot)
    for bar, value, direction in zip(payload["bars"], [101, 100, 102, 103, 104],
                                     ["neutral", "down", "up", "up", "up"], strict=True):
        bar.update(ema_20=value, ema_direction=direction)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    capture_strokes(page)
    with running_spy_server(cache) as url:
        show_april(page, url, days)
        colors = page.evaluate("""days => {
            const centers = days.map(day => chartUnderTest.timeScale().timeToCoordinate(day));
            return centers.slice(0, 2).map((x, i) => {
                const end = centers[i + 1];
                return spyStrokes.filter(({color, xs}) =>
                    ["#10b981", "#ef4444", "#aebbd0"].includes(color)
                    && xs.some(p => Math.abs(p - x) < 2)
                    && xs.some(p => Math.abs(p - end) < 2)
                ).map(({color}) => color);
            });
        }""", days)
        assert colors[0] and set(colors[0]) == {"#ef4444"}
        assert colors[1] and set(colors[1]) == {"#10b981"}


def test_chart_does_not_draw_indicators_across_unavailable_values(page, snapshot):
    cache, payload, days = april_bars(snapshot)
    for i, bar in enumerate(payload["bars"]):
        # Red EMA strokes are distinguishable from the green candle price line.
        bar.update(ema_20=104 - i, ema_direction="down")
    payload["bars"][2].update(upper=None, lower=None, ema_20=None, ema_direction=None, volume_sma_20=None)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    capture_strokes(page)
    with running_spy_server(cache) as url:
        show_april(page, url, days)
        crossing = page.evaluate("""day => {
            const x = chartUnderTest.timeScale().timeToCoordinate(day);
            return spyStrokes.filter(({color, xs}) =>
                ["#f0bf7f", "#c6a1fa", "#ef4444", "#38bdf8"].includes(color)
                && Math.min(...xs) < x - 2 && Math.max(...xs) > x + 2
            ).map(({color}) => color);
        }""", days[2])
        assert crossing == []
