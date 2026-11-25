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
        ema, = [data for data in price_lines if data[0]["value"] == 101]
        assert [point["color"] for point in ema[:2]] == ["#10b981", "#ef4444"]
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
