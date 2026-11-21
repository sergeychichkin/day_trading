import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_server import running_server


@pytest.fixture
def page():
    with sync_playwright() as playwright:
        channel = os.environ.get("QQQ_TEST_BROWSER_CHANNEL")
        if not channel and Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe").exists():
            channel = "msedge"
        browser = playwright.chromium.launch(channel=channel)
        yield browser.new_page(viewport={"width": 1280, "height": 820})
        browser.close()


def capture_chart(page):
    def hook(route):
        response = route.fetch()
        route.fulfill(response=response, body=response.text() + """
            const chartLibrary = window.LightweightCharts;
            window.LightweightCharts = {...chartLibrary, createChart(...args) {
                window.testChart = chartLibrary.createChart(...args);
                return window.testChart;
            }};
        """)
    page.route("**/static/vendor/lightweight-charts.js", hook)


def test_opening_window_hover_and_horizontal_scroll(page, market_cache):
    capture_chart(page)
    with running_server(market_cache) as url:
        page.goto(url)
        expect(page.locator("#month")).to_have_value("2020-02")
        expect(page.locator("#coverage")).to_contain_text("390")
        expect(page.locator("#chart canvas").first).to_be_visible()
        page.wait_for_function("""
            () => {
                const range = testChart.timeScale().getVisibleRange();
                return range?.from === 1580740200 && range?.to === 1580747340;
            }
        """)
        initial = page.evaluate("testChart.timeScale().getVisibleRange()")
        assert initial["from"] == 1580740200
        assert initial["to"] == 1580747340
        box = page.locator("#chart").bounding_box()
        x = page.evaluate("testChart.timeScale().timeToCoordinate(1580740200)")
        page.mouse.move(box["x"] + x, box["y"] + 160)
        expect(page.locator("#legend-time")).to_contain_text("09:30")
        expect(page.locator("#legend-open")).to_have_text("220.00")
        expect(page.locator("#legend-volume")).to_have_text("20")
        expect(page.locator("#legend-sma")).to_have_text("10.5")
        before = page.evaluate("testChart.timeScale().getVisibleLogicalRange().from")
        page.mouse.move(box["x"] + 850, box["y"] + 170)
        page.mouse.down()
        page.mouse.move(box["x"] + 350, box["y"] + 170, steps=12)
        page.mouse.up()
        page.wait_for_function("(before) => testChart.timeScale().getVisibleLogicalRange().from > before + 10", arg=before)


def test_late_response_cannot_replace_newer_month(page, market_cache):
    capture_chart(page)
    pending = []
    def defer_january(route):
        pending.append(route)
    with running_server(market_cache) as url:
        page.route("**/api/month/2020-01", defer_january)
        page.goto(url)
        expect(page.locator("#coverage")).to_contain_text("390")
        page.locator("#month").fill("2020-01")
        page.wait_for_function("document.querySelector('#coverage').textContent.includes('Loading')")
        page.locator("#month").fill("2020-03")
        expect(page.locator("#coverage")).to_contain_text("No cached bars")
        assert pending
        pending[0].continue_()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#month")).to_have_value("2020-03")
        expect(page.locator("#coverage")).to_contain_text("No cached bars")
