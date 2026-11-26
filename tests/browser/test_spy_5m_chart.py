import json

from playwright.sync_api import expect

from qqq_viewer.spy_5m_store import FiveMinuteStore
from tests.browser.test_spy_daily_chart import capture_series
from tests.browser.test_viewer import page
from tests.test_spy_5m_view import running_intraday, spy_files


def test_intraday_month_plot_has_separate_levels_and_blank_gap(page, spy_files):
    store, daily, (first, early, _) = spy_files
    capture_series(page)
    with running_intraday(store, daily) as url:
        page.goto(url + "/spy/5m")
        picker = page.locator("#month")
        expect(picker).to_have_attribute("type", "month")
        expect(picker).to_have_value("2025-12")
        picker.fill("2025-11")
        expect(page.locator("#legend-time")).to_contain_text("09:30")
        expect(page.locator("#legend-upper")).to_have_text("110.1235")
        expect(page.locator("#coverage")).to_contain_text("missing")
        price, lines = page.evaluate("""() => [
            seriesUnderTest.find(s => s.seriesType() === "Candlestick").data(),
            seriesUnderTest.filter(s => s.seriesType() === "Line")
              .flatMap(s => { try { return [s.data()]; } catch { return []; } })
        ]""")
        for session, upper in [(first, 110.123456), (early, 113)]:
            assert any(line[0]["time"] == session.open_utc and line[-1]["time"] == session.close_utc
                       and {point["value"] for point in line} == {upper} for line in lines if line)
        assert any(len(line) == 3 and line[-1]["time"] == first.open_utc + 600 for line in lines)
        gap = first.open_utc + 900
        assert not any(point["time"] == gap and "open" in point for point in price)
        assert page.evaluate("t => chartUnderTest.timeScale().timeToCoordinate(t)", gap) is not None


def test_delayed_old_intraday_month_does_not_replace_newer_selection(page, spy_files):
    store, daily, _ = spy_files
    pending = []
    with running_intraday(store, daily) as url:
        page.route("**/api/spy/5m/month/2025-11", lambda route: pending.append(route))
        page.goto(url + "/spy/5m")
        expect(page.locator("#legend-upper")).to_have_text("120.00")
        page.locator("#month").fill("2025-11")
        expect(page.locator("#coverage")).to_contain_text("Loading 2025-11")
        page.locator("#month").fill("2025-12")
        expect(page.locator("#legend-upper")).to_have_text("120.00")
        assert pending
        pending[0].continue_()
        page.wait_for_load_state("networkidle")
        expect(page.locator("#legend-upper")).to_have_text("120.00")


def test_intraday_page_explains_absent_cache(page, tmp_path):
    store = FiveMinuteStore(tmp_path / "absent.db", read_only=True)
    with running_intraday(store, tmp_path / "daily.json") as url:
        page.goto(url + "/spy/5m")
        expect(page.locator("#month")).to_be_disabled()
        expect(page.locator("#empty")).to_contain_text("qqq_viewer.spy_5m_contiguous")


def test_session_levels_remain_in_view_when_zoomed_between_endpoints(page, spy_files):
    store, daily, _ = spy_files
    capture_series(page)
    with running_intraday(store, daily) as url:
        page.goto(url + "/spy/5m")
        expect(page.locator("#legend-upper")).to_have_text("120.00")
        page.evaluate("chartUnderTest.timeScale().setVisibleLogicalRange({from: 24, to: 44})")
        page.wait_for_function("Math.abs(chartUnderTest.timeScale().getVisibleLogicalRange()?.from - 24) < 0.01")
        coordinates = page.evaluate("""() => [120, 90].map(value =>
            seriesUnderTest[0].priceToCoordinate(value))""")
        height = page.locator("#chart").bounding_box()["height"]
        assert all(y is not None and 0 <= y < height for y in coordinates)


def test_published_picker_and_session_specific_missing_reason(page, spy_files):
    store, daily, (_, early, december) = spy_files
    with store._connection(write=True) as db:
        db.execute("DELETE FROM bars WHERE date < ?", (early.date.isoformat(),))
        db.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("verified_contiguous_start", early.date.isoformat()),
            ("verified_contiguous_end", december.date.isoformat()),
            ("requested_through", "2025-12-03"),
        ])
    document = json.loads(daily.read_bytes())
    document["bars"].pop(2)
    daily.write_text(json.dumps(document), encoding="utf-8")
    with running_intraday(store, daily) as url:
        page.goto(url + "/spy/5m")
        picker = page.locator("#month")
        expect(picker).to_have_value("2025-12")
        expect(picker).to_have_attribute("min", "2025-11")
        expect(page.locator("#coverage")).to_contain_text("Published 2025-11-28 through 2025-12-01")
        expect(page.locator("#coverage")).to_contain_text("Requested through 2025-12-03")
        expect(page.locator("#legend-level-status")).to_contain_text("No SPY daily row for this session")
        picker.fill("2025-11")
        expect(page.locator("#coverage")).to_contain_text("42 / 42 five-minute bars")
        expect(page.locator("#legend-level-status")).to_contain_text("Levels from SPY daily snapshot")
