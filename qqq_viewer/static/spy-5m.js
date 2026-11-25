"use strict";

const picker = document.querySelector("#month");
const coverage = document.querySelector("#coverage");
const empty = document.querySelector("#empty");
const up = "#10b981", down = "#ef4444", vwap = "#c6a1fa", upper = "#f0bf7f", lower = "#38bdf8";
const zone = {timeZone: "America/New_York"};
const stamp = new Intl.DateTimeFormat("en-US", {
    ...zone, month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const clock = new Intl.DateTimeFormat("en-US", {...zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
const day = new Intl.DateTimeFormat("en-US", {...zone, month: "short", day: "numeric"});
const price = new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 4});
const quantity = new Intl.NumberFormat("en-US", {maximumFractionDigits: 2});
const fields = ["open", "high", "low", "close", "volume", "vwap", "upper", "lower"];
const chart = LightweightCharts.createChart(document.querySelector("#chart"), {
    autoSize: true,
    layout: {background: {color: "#101824"}, textColor: "#aebbd0", fontFamily: "Segoe UI, sans-serif"},
    grid: {vertLines: {visible: false}, horzLines: {color: "#202b3d"}},
    rightPriceScale: {borderColor: "#273347", scaleMargins: {top: 0.07, bottom: 0.08}},
    crosshair: {mode: LightweightCharts.CrosshairMode.Normal},
    localization: {timeFormatter: time => stamp.format(time * 1000)},
    timeScale: {
        timeVisible: true, secondsVisible: false, lockVisibleTimeRangeOnResize: true, borderColor: "#273347",
        tickMarkFormatter: time => clock.format(time * 1000) === "09:30"
            ? day.format(time * 1000) : clock.format(time * 1000),
    },
});
const candles = chart.addCandlestickSeries({
    upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down,
    lastValueVisible: false, priceLineVisible: false,
});
const lines = [];
let barByTime = new Map(), sessionByTime = new Map(), conflictTimes = new Set();
let sessionOpens = [];
let selection = 0;

candles.attachPrimitive({
    paneViews: () => [{
        zOrder: () => "bottom",
        renderer: () => ({
            draw(target) {
                target.useMediaCoordinateSpace(({context, mediaSize}) => {
                    context.strokeStyle = "#5e718c";
                    context.lineWidth = 1;
                    context.setLineDash([4, 4]);
                    context.beginPath();
                    for (const time of sessionOpens) {
                        const x = chart.timeScale().timeToCoordinate(time);
                        if (x === null || x < 0 || x > mediaSize.width) continue;
                        context.moveTo(Math.round(x) + 0.5, 0);
                        context.lineTo(Math.round(x) + 0.5, mediaSize.height);
                    }
                    context.stroke();
                    context.setLineDash([]);
                });
            },
        }),
    }],
});

function updateLegend(time) {
    const bar = barByTime.get(time);
    const session = sessionByTime.get(time);
    const values = {...bar, upper: session?.upper, lower: session?.lower};
    document.querySelector("#legend-time").textContent = time == null ? "—" : stamp.format(time * 1000);
    document.querySelector("#legend-conflict").hidden = !conflictTimes.has(time);
    for (const field of fields) {
        const format = field === "volume" ? quantity : price;
        document.querySelector("#legend-" + field).textContent = values[field] == null ? "—" : format.format(values[field]);
    }
}

chart.subscribeCrosshairMove(({time}) => {
    if (time != null) updateLegend(time);
});

function setEmpty(message) {
    empty.hidden = !message;
    empty.textContent = message;
}

function status(message, kind = "") {
    coverage.textContent = message;
    coverage.dataset.status = kind;
}

function clear() {
    barByTime.clear();
    sessionByTime.clear();
    conflictTimes.clear();
    sessionOpens = [];
    candles.setMarkers([]);
    candles.setData([]);
    for (const series of lines.splice(0)) chart.removeSeries(series);
    updateLegend();
}

function addLine(points, color) {
    if (!points.length) return;
    const series = chart.addLineSeries({
        color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
    });
    series.setData(points);
    lines.push(series);
}

function drawMonth(data) {
    const prices = [];
    let firstSession = null;
    for (const session of data.sessions) {
        const start = prices.length;
        sessionOpens.push(session.open_utc);
        const byTime = new Map(session.bars.map(bar => [bar.time, bar]));
        for (const time of session.conflict_times) conflictTimes.add(time);
        for (let time = session.open_utc; time < session.close_utc; time += 300) {
            const bar = byTime.get(time);
            sessionByTime.set(time, session);
            if (bar) barByTime.set(time, bar);
            prices.push(bar ?? {time});
        }
        // A close-boundary whitespace point is not a candle or an expected bar.
        prices.push({time: session.close_utc});
        sessionByTime.set(session.close_utc, session);
        if (session.bars.length && firstSession === null) {
            firstSession = {start, end: prices.length - 1, open: session.open_utc};
        }
    }
    const counts = data.coverage;
    const parts = [`${quantity.format(counts.received)} / ${quantity.format(counts.expected)} five-minute bars`];
    if (counts.missing) parts.push(`${counts.missing} missing`);
    if (counts.unverified_sessions) parts.push(`${counts.unverified_sessions} unverified sessions`);
    if (counts.conflicting) parts.push(`${counts.conflicting} conflicting bars`);
    if (data.through) parts.push(`Checked through ${data.through}`);
    const mismatch = data.daily_status === "price_mismatch";
    parts.push(mismatch ? "Daily levels hidden on sessions with a different price scale"
        : data.daily_status === "available" ? "Levels: SPY daily snapshot" : "Daily levels unavailable");
    status(parts.join(" · "), mismatch || counts.missing || counts.unverified_sessions || counts.conflicting
        ? "incomplete" : "");

    if (firstSession === null) {
        setEmpty("No cached SPY five-minute bars for this month.");
        return;
    }
    candles.setData(prices);
    candles.setMarkers([...conflictTimes].sort((a, b) => a - b).map(time => ({
        time, position: "aboveBar", shape: "circle", color: "#f0bf7f",
    })));
    for (const session of data.sessions) {
        if (!session.bars.length) continue;
        addLine(session.bars.filter(bar => bar.vwap != null).map(bar => ({time: bar.time, value: bar.vwap})), vwap);
        for (const [field, color] of [["upper", upper], ["lower", lower]]) {
            const value = session[field];
            if (value != null) addLine([{time: session.open_utc, value}, {time: session.close_utc, value}], color);
        }
    }
    setEmpty("");
    chart.priceScale("right").applyOptions({autoScale: true});
    chart.timeScale().setVisibleLogicalRange({from: firstSession.start, to: firstSession.end});
    updateLegend(firstSession.open);
}

async function getJson(path) {
    const response = await fetch(path);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "The SPY five-minute cache is unavailable.");
    return data;
}

async function loadMonth() {
    const id = ++selection;
    const month = picker.value;
    clear();
    if (!month || !picker.validity.valid) {
        setEmpty("Select a cached month.");
        status("No month selected");
        return;
    }
    setEmpty("");
    status(`Loading ${month}…`);
    try {
        const data = await getJson(`/api/spy/5m/month/${month}`);
        if (id === selection) drawMonth(data);
    } catch (error) {
        if (id === selection) {
            setEmpty("SPY five-minute chart unavailable.");
            status(error.message, "error");
        }
    }
}

async function start() {
    try {
        const {months, default_month: latest} = await getJson("/api/spy/5m/months");
        if (!months.length) {
            setEmpty("No SPY five-minute cache. Run python -m qqq_viewer.spy_5m_backfill --volume-unit shares --probe.");
            status("No cached months");
            return;
        }
        picker.max = months.at(-1);
        picker.value = latest || picker.max;
        picker.disabled = false;
        await loadMonth();
    } catch (error) {
        setEmpty("SPY five-minute chart unavailable.");
        status(error.message, "error");
    }
}

picker.addEventListener("change", loadMonth);
document.querySelector("#legend-upper").style.color = upper;
document.querySelector("#legend-lower").style.color = lower;
start();
