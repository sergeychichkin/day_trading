"use strict";

const monthPicker = document.querySelector("#month");
const chartElement = document.querySelector("#chart");
const coverage = document.querySelector("#coverage");
const empty = document.querySelector("#empty");
const contextButton = document.querySelector("#session-context");
const green = "#10b981", red = "#ef4444", blue = "#38bdf8";
const emaColors = {up: green, down: red, neutral: "#aebbd0"};
const upperColor = "#f0bf7f", lowerColor = "#c6a1fa";
const dateFormat = new Intl.DateTimeFormat("en-US", {
    timeZone: "UTC", weekday: "short", year: "numeric", month: "short", day: "numeric",
});
const priceFormat = new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 4});
const volumeFormat = new Intl.NumberFormat("en-US", {maximumFractionDigits: 2});
const barByDate = new Map();
const monthRanges = new Map();
const missingByMonth = new Map();
const indicatorSeries = [];
let history, plottedBars, through, lastDate;
let trailingMissing = 0;
let showingContext = false;

function dateKey(time) {
    return typeof time === "string" ? time : [
        time.year, String(time.month).padStart(2, "0"), String(time.day).padStart(2, "0"),
    ].join("-");
}

const chart = LightweightCharts.createChart(chartElement, {
    autoSize: true,
    layout: {background: {color: "#101824"}, textColor: "#aebbd0", fontFamily: "Segoe UI, sans-serif"},
    grid: {vertLines: {visible: false}, horzLines: {color: "#202b3d"}},
    rightPriceScale: {borderColor: "#273347", scaleMargins: {top: 0.07, bottom: 0.27}},
    crosshair: {mode: LightweightCharts.CrosshairMode.Normal},
    localization: {timeFormatter: time => dateFormat.format(new Date(dateKey(time)))},
    handleScale: {axisPressedMouseMove: {time: true, price: false}},
    timeScale: {borderColor: "#273347", lockVisibleTimeRangeOnResize: true},
});
const quiet = {lastValueVisible: false, priceLineVisible: false};
const candles = chart.addCandlestickSeries({
    ...quiet,
    upColor: green, downColor: red, borderUpColor: green, borderDownColor: red,
    wickUpColor: green, wickDownColor: red,
});
const volumeOptions = {priceFormat: {type: "volume"}, priceScaleId: "volume"};
const lineOptions = {...quiet, lineWidth: 2, crosshairMarkerVisible: false};
const volume = chart.addHistogramSeries({...quiet, ...volumeOptions});
chart.priceScale("volume").applyOptions({scaleMargins: {top: 0.78, bottom: 0.02}});

function showLegend(bar) {
    document.querySelector("#legend-time").textContent = bar ? dateFormat.format(new Date(bar.time)) : "—";
    for (const field of ["open", "high", "low", "close", "volume", "volume_sma_20", "ema_20", "upper", "lower"]) {
        const cell = document.querySelector("#legend-" + field);
        const format = field.startsWith("volume") ? volumeFormat : priceFormat;
        cell.textContent = bar?.[field] == null ? "—" : format.format(bar[field]);
    }
    document.querySelector("#legend-ema_20").style.color = emaColors[bar?.ema_direction] ?? "";
    document.querySelector("#legend-upper").style.color = upperColor;
    document.querySelector("#legend-lower").style.color = lowerColor;
}

chart.subscribeCrosshairMove(({time}) => {
    if (time && empty.hidden) showLegend(barByDate.get(dateKey(time)));
});

function showEmpty(message) {
    empty.textContent = message;
    empty.hidden = !message;
    chartElement.style.visibility = message ? "hidden" : "";
    if (message) showLegend();
}

function showMonth(showContext = false) {
    const month = monthPicker.value;
    const range = monthRanges.get(month);
    const missing = missingByMonth.get(month) || 0;
    const singleton = range && range.first === range.last;
    const hasContext = singleton && history.length > 1;
    showingContext = Boolean(hasContext && showContext);
    contextButton.hidden = !hasContext;
    contextButton.textContent = showingContext ? "Show only selected session" : "Show adjacent sessions";
    coverage.dataset.status = missing || trailingMissing ? "incomplete" : "";
    if (range) {
        showEmpty("");
        chart.priceScale("right").applyOptions({autoScale: true});
        const timeScale = chart.timeScale();
        if (singleton && !showingContext) {
            // The bundled renderer fits at least two logical slots; remove neighbors
            // from this view so they cannot appear or affect price autoscaling.
            plotBars([range.openingBar]);
            timeScale.fitContent();
        } else {
            plotBars(history);
            timeScale.setVisibleLogicalRange(showingContext ? {
                from: Math.max(0, range.first - 2),
                to: Math.min(history.length - 1, range.last + 2),
            } : {from: range.first, to: range.last});
        }
        showLegend(range.openingBar);
    } else {
        showEmpty(month ? "No cached bars for this month." : "Select a month.");
    }
    const count = range ? range.last - range.first + 1 : 0;
    coverage.textContent = [
        month ? `${month}: ${count} / ${count + missing} sessions` : "No month selected",
        missing ? `${missing} missing` : null,
        `Last bar ${lastDate}`,
        `Cache checked through ${through}`,
        trailingMissing ? `${trailingMissing} missing after last bar` : null,
    ].filter(Boolean).join(" · ");
}

function plotLine(bars, field, options, colorFor) {
    let run = [];
    function finishRun() {
        // This chart library connects points within one series even across gaps.
        if (run.length) {
            const series = chart.addLineSeries({...lineOptions, ...options});
            series.setData(run);
            indicatorSeries.push(series);
        }
        run = [];
    }
    bars.forEach((bar, index) => {
        if (bar[field] == null) {
            finishRun();
            return;
        }
        const point = {time: bar.time, value: bar[field]};
        const next = bars[index + 1];
        // Point colors apply to the outgoing segment, whose slope is recorded on the next bar.
        if (colorFor) point.color = colorFor(next?.[field] == null ? bar : next);
        run.push(point);
    });
    finishRun();
}

function plotBars(bars) {
    if (bars === plottedBars) return;
    plottedBars = bars;
    for (const series of indicatorSeries) chart.removeSeries(series);
    indicatorSeries.length = 0;
    candles.setData(bars.map(({time, open, high, low, close}) => ({time, open, high, low, close})));
    volume.setData(bars.map(bar => ({
        time: bar.time, value: bar.volume, color: (bar.close >= bar.open ? green : red) + "80",
    })));
    plotLine(bars, "volume_sma_20", {...volumeOptions, color: blue});
    plotLine(bars, "ema_20", {color: emaColors.neutral}, bar => emaColors[bar.ema_direction]);
    plotLine(bars, "upper", {color: upperColor});
    plotLine(bars, "lower", {color: lowerColor});
}

async function load() {
    try {
        const response = await fetch("/api/spy/daily");
        if (!response.ok) throw new Error("The local SPY cache could not be read.");
        const {bars, missing_sessions: missing, through: lastChecked} = await response.json();
        if (!bars.length) {
            showEmpty("No SPY history cached. Run python -m qqq_viewer.spy_daily. " +
                "If the server uses --spy-cache, pass the same file path to the downloader with --cache.");
            coverage.textContent = "No cached data";
            return;
        }

        history = bars;
        through = lastChecked;
        lastDate = bars.at(-1).time;
        trailingMissing = missing.filter(day => day > lastDate).length;
        bars.forEach((bar, index) => {
            const month = bar.time.slice(0, 7);
            barByDate.set(bar.time, bar);
            const range = monthRanges.get(month);
            if (range) range.last = index;
            else monthRanges.set(month, {first: index, last: index, openingBar: bar});
        });
        for (const day of missing) {
            const month = day.slice(0, 7);
            missingByMonth.set(month, (missingByMonth.get(month) || 0) + 1);
        }

        monthPicker.value = lastDate.slice(0, 7);
        monthPicker.max = through.slice(0, 7);
        monthPicker.disabled = false;
        showMonth();
    } catch (error) {
        coverage.dataset.status = "error";
        coverage.textContent = error.message;
        showEmpty("SPY chart unavailable.");
    }
}

monthPicker.addEventListener("change", () => showMonth());
contextButton.addEventListener("click", () => showMonth(!showingContext));
load();
