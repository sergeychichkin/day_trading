"use strict";

const monthPicker = document.querySelector("#month");
const chartElement = document.querySelector("#chart");
const coverageElement = document.querySelector("#coverage");
const emptyElement = document.querySelector("#empty");
const unitElement = document.querySelector("#unit");

const dash = "—";
const up = "#10b981";
const down = "#ef4444";
const volumeSma = "#38bdf8";
const east = "America/New_York";
const priceText = new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 4});
const volumeText = new Intl.NumberFormat("en-US", {maximumFractionDigits: 2});
const dateText = new Intl.DateTimeFormat("en-US", {timeZone: east, month: "short", day: "numeric"});
const timeText = new Intl.DateTimeFormat("en-US", {
    timeZone: east, hour: "2-digit", minute: "2-digit", hourCycle: "h23"
});
const stampText = new Intl.DateTimeFormat("en-US", {
    timeZone: east, weekday: "short", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23"
});

const chart = LightweightCharts.createChart(chartElement, {
    autoSize: true,
    layout: {background: {color: "#101824"}, textColor: "#aebbd0", fontFamily: "Segoe UI, sans-serif"},
    grid: {vertLines: {visible: false}, horzLines: {color: "#202b3d"}},
    rightPriceScale: {borderColor: "#273347", scaleMargins: {top: 0.07, bottom: 0.27}},
    crosshair: {mode: LightweightCharts.CrosshairMode.Normal},
    localization: {timeFormatter: timestamp => stampText.format(timestamp * 1000)},
    timeScale: {
        borderColor: "#273347",
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: timestamp => {
            const time = timeText.format(timestamp * 1000);
            return time === "09:30" ? dateText.format(timestamp * 1000) : time;
        },
    },
});

const candles = chart.addCandlestickSeries({
    upColor: up, downColor: down, borderUpColor: up, borderDownColor: down,
    wickUpColor: up, wickDownColor: down,
});
const volume = chart.addHistogramSeries({
    priceFormat: {type: "volume"},
    priceScaleId: "volume",
    lastValueVisible: false,
    priceLineVisible: false,
});
const sma = chart.addLineSeries({
    color: volumeSma,
    lineWidth: 2,
    priceFormat: {type: "volume"},
    priceScaleId: "volume",
    lastValueVisible: false,
    priceLineVisible: false,
    crosshairMarkerVisible: false,
});
chart.priceScale("volume").applyOptions({scaleMargins: {top: 0.78, bottom: 0.02}});

let plottedBars = new Map();
let displayedSessions = [];
let selection = 0;

const sessionDividers = {
    paneViews() {
        return [{
            zOrder: () => "bottom",
            renderer: () => ({
                draw(target) {
                    target.useMediaCoordinateSpace(({context, mediaSize}) => {
                        context.strokeStyle = "#5e718c";
                        context.lineWidth = 1;
                        context.setLineDash([4, 4]);
                        context.beginPath();
                        for (const session of displayedSessions) {
                            const x = chart.timeScale().timeToCoordinate(session.open_utc);
                            if (x === null || x < 0 || x > mediaSize.width) continue;
                            const aligned = Math.round(x) + 0.5;
                            context.moveTo(aligned, 0);
                            context.lineTo(aligned, mediaSize.height);
                        }
                        context.stroke();
                        context.setLineDash([]);
                    });
                },
            }),
        }];
    },
};
candles.attachPrimitive(sessionDividers);

function updateLegend(timestamp) {
    const bar = plottedBars.get(timestamp);
    document.querySelector("#legend-time").textContent = timestamp ? stampText.format(timestamp * 1000) : dash;
    for (const field of ["open", "high", "low", "close", "volume", "sma"]) {
        const value = field === "sma" ? bar?.volume_sma_20 : bar?.[field];
        const formatter = field === "volume" || field === "sma" ? volumeText : priceText;
        document.querySelector("#legend-" + field).textContent = value == null ? dash : formatter.format(value);
    }
}

chart.subscribeCrosshairMove(({time}) => {
    if (time) updateLegend(time);
});

function setStatus(message, status = "") {
    coverageElement.textContent = message;
    coverageElement.dataset.status = status;
}

function setEmpty(message) {
    emptyElement.textContent = message;
    emptyElement.hidden = !message;
}

function clearSeries() {
    plottedBars.clear();
    displayedSessions = [];
    candles.setData([]);
    volume.setData([]);
    sma.setData([]);
    unitElement.textContent = "";
    updateLegend();
}

function showMonth(data) {
    plottedBars = new Map(data.bars.map(bar => [bar.time, bar]));
    displayedSessions = data.sessions;
    unitElement.textContent = data.volume_unit ? " (" + data.volume_unit + ")" : "";

    const coverage = data.coverage;
    if (data.bars.length === 0) {
        clearSeries();
        setEmpty("No cached bars for this month.");
        setStatus(coverage.expected ? "No cached bars · " + volumeText.format(coverage.expected) +
            " expected minutes" : "No cached bars", coverage.status);
        return;
    }

    const prices = [], volumes = [], averages = [];
    let firstSessionIndex = null;
    for (const session of displayedSessions) {
        const sessionStart = prices.length;
        for (let time = session.open_utc; time < session.close_utc; time += 60) {
            const bar = plottedBars.get(time);
            prices.push(bar ?? {time});
            if (!bar) {
                averages.push({time});
                continue;
            }
            if (firstSessionIndex === null) firstSessionIndex = sessionStart;
            volumes.push({time, value: bar.volume, color: bar.close >= bar.open ? "#10b98180" : "#ef444480"});
            if (bar.volume_sma_20 != null) averages.push({time, value: bar.volume_sma_20});
        }
    }
    candles.setData(prices);
    volume.setData(volumes);
    sma.setData(averages);
    if (firstSessionIndex !== null) {
        chart.timeScale().setVisibleLogicalRange({from: firstSessionIndex, to: firstSessionIndex + 119});
    }
    updateLegend(data.bars[0].time);
    setEmpty("");

    const parts = [
        volumeText.format(coverage.received) + " / " + volumeText.format(coverage.expected) + " minutes",
    ];
    if (coverage.missing) parts.push(volumeText.format(coverage.missing) + " missing");
    if (coverage.unverified_sessions) parts.push(volumeText.format(coverage.unverified_sessions) + " unverified sessions");
    if (coverage.conflicting) parts.push(volumeText.format(coverage.conflicting) + " conflicting minutes");
    setStatus(parts.join(" · "), coverage.status);
}

async function getJson(path) {
    const response = await fetch(path);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The local cache is unavailable.");
    return data;
}

async function loadMonth(month) {
    const request = ++selection;
    clearSeries();
    setEmpty("");
    if (!/^[0-9]{4}-(0[1-9]|1[0-2])$/.test(month)) {
        setEmpty("Select a month.");
        setStatus("Select a month");
        return;
    }
    setStatus("Loading " + month + "…");
    try {
        const data = await getJson("/api/month/" + month);
        if (request === selection) showMonth(data);
    } catch (error) {
        if (request !== selection) return;
        setStatus(error.message, "error");
        setEmpty("Chart unavailable.");
    }
}

monthPicker.addEventListener("change", () => loadMonth(monthPicker.value));

async function start() {
    try {
        const {months, default_month: defaultMonth} = await getJson("/api/months");
        monthPicker.disabled = !months.length;
        if (months.length) monthPicker.max = months[months.length - 1];
        const initialMonth = defaultMonth || months.at(-1);
        if (initialMonth) {
            monthPicker.value = initialMonth;
            await loadMonth(initialMonth);
        } else {
            setEmpty("No QQQ data in the local cache.");
            setStatus("No cached months");
        }
    } catch (error) {
        setStatus(error.message, "error");
        setEmpty("Chart unavailable.");
    }
}

start();
