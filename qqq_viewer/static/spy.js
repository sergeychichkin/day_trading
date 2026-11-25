"use strict";

const monthPicker = document.querySelector("#month");
const chartElement = document.querySelector("#chart");
const coverage = document.querySelector("#coverage");
const empty = document.querySelector("#empty");
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
let through;

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
    timeScale: {borderColor: "#273347"},
});
const candles = chart.addCandlestickSeries({
    upColor: green, downColor: red, borderUpColor: green, borderDownColor: red,
    wickUpColor: green, wickDownColor: red,
});
const volumeOptions = {priceFormat: {type: "volume"}, priceScaleId: "volume"};
const quiet = {lastValueVisible: false, priceLineVisible: false};
const lineOptions = {...quiet, lineWidth: 2, crosshairMarkerVisible: false};
const volume = chart.addHistogramSeries({...quiet, ...volumeOptions});
const sma = chart.addLineSeries({...lineOptions, ...volumeOptions, color: blue});
chart.priceScale("volume").applyOptions({scaleMargins: {top: 0.78, bottom: 0.02}});
const ema = chart.addLineSeries({...lineOptions, color: emaColors.neutral});
const upper = chart.addLineSeries({...lineOptions, color: upperColor});
const lower = chart.addLineSeries({...lineOptions, color: lowerColor});

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

function showMonth() {
    const month = monthPicker.value;
    const range = monthRanges.get(month);
    const missing = missingByMonth.get(month) || 0;
    coverage.dataset.status = missing ? "incomplete" : "";
    if (range) {
        showEmpty("");
        chart.timeScale().setVisibleLogicalRange({
            from: range.first === range.last ? range.first - 0.5 : range.first,
            to: range.first === range.last ? range.last + 0.5 : range.last,
        });
        showLegend(range.openingBar);
    } else {
        showEmpty(month ? "No cached bars for this month." : "Select a month.");
    }
    const count = range ? range.last - range.first + 1 : 0;
    coverage.textContent = [
        month ? `${count} / ${count + missing} sessions` : "No month selected",
        missing ? `${missing} missing` : null,
        `Cache checked through ${through}`,
    ].filter(Boolean).join(" · ");
}

function lineData(bars, field, colorFor) {
    return bars.flatMap(bar => bar[field] == null ? [] : [{
        time: bar.time, value: bar[field], ...(colorFor ? {color: colorFor(bar)} : {}),
    }]);
}

async function load() {
    try {
        const response = await fetch("/api/spy/daily");
        if (!response.ok) throw new Error("The local SPY cache could not be read.");
        const {bars, missing_sessions: missing, through: lastChecked} = await response.json();
        if (!bars.length) {
            showEmpty("No SPY history cached. Run python -m qqq_viewer.spy_daily to download it.");
            coverage.textContent = "No cached data";
            return;
        }

        through = lastChecked;
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

        candles.setData(bars.map(({time, open, high, low, close}) => ({time, open, high, low, close})));
        volume.setData(bars.map(bar => ({
            time: bar.time, value: bar.volume, color: (bar.close >= bar.open ? green : red) + "80",
        })));
        sma.setData(lineData(bars, "volume_sma_20"));
        ema.setData(lineData(bars, "ema_20", bar => emaColors[bar.ema_direction]));
        upper.setData(lineData(bars, "upper"));
        lower.setData(lineData(bars, "lower"));

        monthPicker.value = bars.at(-1).time.slice(0, 7);
        monthPicker.max = through.slice(0, 7);
        monthPicker.disabled = false;
        showMonth();
    } catch (error) {
        coverage.dataset.status = "error";
        coverage.textContent = error.message;
        showEmpty("SPY chart unavailable.");
    }
}

monthPicker.addEventListener("change", showMonth);
load();
