"use strict";

const run = JSON.parse(document.querySelector("#report-data").textContent);
const cfg = run.settings;
const formatMoney = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
const formatCount = new Intl.NumberFormat("en-US");
const [tickMantissa, tickExponent = "0"] = cfg.tick_size.toString().split("e");
const tickFraction = tickMantissa.split(".")[1] ?? "";
const pricePrecision = Math.max(2, tickFraction.length - Number(tickExponent));
const priceFormat = {type: "price", minMove: cfg.tick_size, precision: pricePrecision};
const priceCurrency = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: pricePrecision, maximumFractionDigits: pricePrecision,
});
const fillPrecision = Math.max(4, pricePrecision);
const fillPrice = new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", minimumFractionDigits: fillPrecision, maximumFractionDigits: fillPrecision,
});
const formatTime = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const formatDate = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", month: "short", day: "numeric",
});
const time = t => formatTime.format(t * 1000);
const money = n => n == null ? "—" : formatMoney.format(n);
const orderPrice = n => n == null ? "—" : priceCurrency.format(n);
const pct = n => n == null ? "—" : n.toFixed(2) + "%";
const byId = id => document.getElementById(id);

const entryLabel = id => id === "entry1" ? "Entry 1" : "Entry 2";
const keyOf = (date, id) => date + ":" + id;
const rulesSelect = byId("rules-select");
const runSelect = byId("run-select");
let selected;
const caseLabel = item => item.label + (item.slippage_multiplier === 1 ? " · Base" : " · 2× slippage");

byId("ticker").textContent = run.ticker;
byId("parameters").textContent = [
    run.start + " to " + run.end, money(cfg.starting_balance) + " starting balance",
    cfg.leverage + "× buying power", "New York time",
].join(" · ");
function showScenarios(preferredScenario) {
    const scenarios = run.scenarios.filter(item => item.rule_version === rulesSelect.value);
    const body = byId("scenarios").tBodies[0];
    runSelect.replaceChildren();
    body.replaceChildren();
    for (const item of scenarios) {
        runSelect.add(new Option(caseLabel(item), item.scenario_id));
        const stat = item.stats;
        const row = body.insertRow();
        for (const value of [caseLabel(item), stat.trades, money(stat.net), pct(stat.return_pct),
            pct(stat.max_drawdown_pct), money(stat.commission), money(stat.slippage)]) {
            row.insertCell().textContent = value;
        }
        row.dataset.run = item.id;
    }
    const initial = scenarios.find(item => item.scenario_id === preferredScenario) ?? scenarios[0];
    runSelect.value = initial.scenario_id;
}

function showSummary() {
    const stat = selected.stats;
    const summary = byId("summary");
    summary.replaceChildren();
    for (const [label, value] of [
        ["Ending balance", money(stat.ending_balance)], ["Net PnL", money(stat.net)],
        ["Account return", pct(stat.return_pct)], ["Closed balance drawdown", pct(stat.max_drawdown_pct)],
        ["Win rate", stat.trades ? pct(stat.wins / stat.trades * 100) : "—"],
        ["Profit factor", stat.profit_factor == null ? (stat.wins ? "∞" : "—") : stat.profit_factor.toFixed(2)],
        ["Average trade", money(stat.avg_trade)], ["Buy and hold", pct(run.benchmark_pct)],
    ]) {
        const card = document.createElement("div");
        card.className = "card";
        card.append(label);
        const strong = document.createElement("strong");
        strong.textContent = value;
        card.append(strong);
        summary.append(card);
    }
    for (const row of byId("scenarios").tBodies[0].rows) {
        row.classList.toggle("active-scenario", row.dataset.run === selected.id);
    }
    byId("coverage").textContent = formatCount.format(run.bars) + " verified regular-session minutes · " +
        run.sessions + " sessions · " + selected.trades_without_daily_atr + " trades during daily ATR warmup";
    const count = action => selected.events.filter(event => event.action === action).length;
    byId("execution").textContent = [
        count("replaced") + " replacements", count("canceled") + " canceled orders",
        count("skipped") + " skipped events", stat.ambiguous + " ambiguous entry/stop minutes",
        cfg.slippage_bps + " bp per fill with a " + money(cfg.slippage_floor) + "/share floor, scaled " +
            selected.slippage_multiplier + "× for this run",
    ].join(" · ");
    byId("provenance").textContent = JSON.stringify({
        ticker: run.ticker, start: run.start, end: run.end, settings: cfg,
        run: selected.id, rule_version: selected.rule_version, scenario: selected.scenario_id,
        warmup_inclusive_bars: run.input_bars, canonical_bars_sha256: run.input_sha256,
    }, null, 2);
}

const layout = {background: {color: "#101824"}, textColor: "#aebbd0"};
const grid = {vertLines: {visible: false}, horzLines: {color: "#223046"}};
const common = {autoSize: true, layout, grid, rightPriceScale: {borderColor: "#2e3f58"},
    timeScale: {borderColor: "#2e3f58"}};
const library = LightweightCharts;
const performanceTime = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const performanceOptions = {
    ...common,
    localization: {timeFormatter: stamp => performanceTime.format(stamp * 1000)},
    timeScale: {...common.timeScale, timeVisible: true, secondsVisible: false,
        tickMarkFormatter: (stamp, kind) => kind >= library.TickMarkType.Time
            ? time(stamp) : formatDate.format(stamp * 1000)},
};
const equity = library.createChart(byId("equity"), performanceOptions);
function addLine(chart, data, options) {
    const line = chart.addLineSeries({
        priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, ...options,
    });
    line.setData(data);
    return line;
}
const equityLine = addLine(equity, [], {color: "#58daba", lineWidth: 2, lineType: library.LineType.WithSteps});
addLine(equity, run.benchmark, {color: "#7eb8f1", lineWidth: 1});
const drawdown = library.createChart(byId("drawdown"), performanceOptions);
const drawdownLine = addLine(drawdown, [], {color: "#ff8a8a", lineWidth: 2,
    lineType: library.LineType.WithSteps, priceFormat: {type: "percent"}});

function showEquity() {
    byId("selected-equity").textContent = caseLabel(selected);
    equityLine.setData(selected.equity);
    let peak = cfg.starting_balance;
    drawdownLine.setData(selected.equity.map(({time, value}) => {
        peak = Math.max(peak, value);
        return {time, value: 100 * (value / peak - 1)};
    }));
    equity.timeScale().fitContent();
    drawdown.timeScale().fitContent();
}

const price = library.createChart(byId("price"), {
    ...common,
    rightPriceScale: {...common.rightPriceScale, scaleMargins: {top: 0.06, bottom: 0.23}},
    crosshair: {mode: library.CrosshairMode.Normal},
    timeScale: {...common.timeScale, timeVisible: true, secondsVisible: false,
        tickMarkFormatter: t => time(t) === "09:30" ? formatDate.format(t * 1000) : time(t)},
    localization: {timeFormatter: t => formatDate.format(t * 1000) + " " + time(t)},
});
const candles = price.addCandlestickSeries({
    priceFormat,
    upColor: "#10b981", downColor: "#ef4444", borderVisible: false,
    wickUpColor: "#10b981", wickDownColor: "#ef4444",
    lastValueVisible: false, priceLineVisible: false,
});
const volume = price.addHistogramSeries({
    priceScaleId: "vol", priceFormat: {type: "volume"}, lastValueVisible: false, priceLineVisible: false,
});
price.priceScale("vol").applyOptions({scaleMargins: {top: 0.8, bottom: 0.02}});
const month = byId("month");
const orderSelect = byId("order-select");
const toggleRid = byId("toggle-rid");
let ridSegments = [], requestRidUpdate;
let orderWindows = new Map(), ordersByDate = new Map(), tradesByKey = new Map();

function indexSelectedRun() {
    orderWindows = new Map();
    ordersByDate = new Map();
    tradesByKey = new Map(selected.trades.map((trade, i) => [
        keyOf(trade.date, trade.order_id), {...trade, number: i + 1},
    ]));
    for (const event of selected.events) {
        const key = keyOf(event.date, event.order_id);
        if (event.action === "replaced") {
            const old = orderWindows.get(keyOf(event.date, event.prior_order_id));
            old.end = event.time;
            old.status = "Replaced";
        }
        if (event.action === "placed" || event.action === "replaced") {
            const order = {...event, key, end: null, status: "Working"};
            orderWindows.set(key, order);
            if (!ordersByDate.has(event.date)) ordersByDate.set(event.date, []);
            ordersByDate.get(event.date).push(order);
        } else if (event.order_id != null) {
            const order = orderWindows.get(key);
            order.end = event.time;
            order.status = {filled: "Filled", canceled: "Canceled", skipped: "Skipped fill"}[event.action];
        }
    }
}
let overlays = [], levels = [], rangeLines = [], chartBars = new Map(), signalStops = new Map(), opens = [], shownMonth = null, stopLine = null;
candles.attachPrimitive({
    paneViews: () => [{
        zOrder: () => "bottom",
        renderer: () => ({draw(target) {
            target.useMediaCoordinateSpace(({context, mediaSize}) => {
                context.strokeStyle = "#5e718c";
                context.setLineDash([4, 4]);
                context.beginPath();
                for (const t of opens) {
                    const x = price.timeScale().timeToCoordinate(t);
                    if (x === null || x < 0 || x > mediaSize.width) continue;
                    context.moveTo(x, 0);
                    context.lineTo(x, mediaSize.height);
                }
                context.stroke();
                context.setLineDash([]);
            });
        }}),
    }],
});

function ridBands(days) {
    const segments = [];
    for (const day of days) {
        let current = null;
        let length = 0;
        for (const [time, , high, low, close] of day.bars) {
            // Allow for binary rounding at the strict one-cent boundary.
            const tolerance = 0.01 + Math.abs(close) * Number.EPSILON;
            if (current && length < 25 && close < current.upper - tolerance && close > current.lower + tolerance) {
                current.last = time;
                length += 1;
            } else {
                current = {first: time, last: time, upper: high, lower: low};
                segments.push(current);
                length = 1;
            }
        }
    }
    return segments;
}

candles.attachPrimitive({
    attached({requestUpdate}) { requestRidUpdate = requestUpdate; },
    paneViews: () => [{
        zOrder: () => "bottom",
        renderer: () => ({draw(target) {
            if (!toggleRid.checked) return;
            target.useMediaCoordinateSpace(({context, mediaSize}) => {
                context.save();
                context.strokeStyle = "#90a4ae";
                context.lineWidth = 2;
                context.beginPath();
                const scale = price.timeScale();
                for (const {first, last, upper, lower} of ridSegments) {
                    const start = scale.timeToCoordinate(first);
                    const end = scale.timeToCoordinate(last);
                    if (start === null || end === null || end < 0 || start > mediaSize.width) continue;
                    for (const bound of [upper, lower]) {
                        const y = candles.priceToCoordinate(bound);
                        if (y === null) continue;
                        const padding = first === last ? 1 : 0;
                        context.moveTo(start - padding, y);
                        context.lineTo(end + padding, y);
                    }
                }
                context.stroke();
                context.restore();
            });
        }}),
    }],
});
toggleRid.addEventListener("change", () => requestRidUpdate());

for (const key of Object.keys(run.months)) {
    month.add(new Option(key, key));
}
price.subscribeCrosshairMove(({time: stamp}) => {
    const bar = chartBars.get(stamp);
    if (!bar) return;
    byId("chart-legend").textContent = [
        formatDate.format(stamp * 1000) + " " + time(stamp),
        "O " + orderPrice(bar[1]), "H " + orderPrice(bar[2]), "L " + orderPrice(bar[3]), "C " + orderPrice(bar[4]),
        "Vol " + formatCount.format(bar[5]), "VWAP est. " + orderPrice(bar[6]),
        ...(signalStops.has(stamp) ? ["Signal", "Initial stop " + orderPrice(signalStops.get(stamp))] : []),
    ].join(" · ");
});
function clearTradeOverlays() {
    if (stopLine) price.removeSeries(stopLine);
    stopLine = null;
    for (const series of rangeLines) price.removeSeries(series);
    rangeLines = [];
    for (const level of levels) candles.removePriceLine(level);
    levels = [];
}

function showMonth(key) {
    clearTradeOverlays();
    for (const series of overlays) price.removeSeries(series);
    overlays = [];
    chartBars = new Map();
    signalStops = new Map();
    orderSelect.replaceChildren(new Option("Select an order", ""));
    const days = run.months[key];
    ridSegments = ridBands(days);
    opens = days.map(day => day.bars[0][0]);
    const bars = [], vols = [], markers = [];
    for (const day of days) {
        const vwap = [];
        for (const bar of day.bars) {
            const [time, open, high, low, close, value, v] = bar;
            chartBars.set(time, bar);
            bars.push({time, open, high, low, close});
            vols.push({time, value, color: close >= open ? "#10b98180" : "#ef444480"});
            if (v != null) vwap.push({time, value: v});
        }
        overlays.push(addLine(price, vwap, {color: "#c6a1fa", lineWidth: 1}));
        const last = day.bars.at(-1)[0];
        for (const order of ordersByDate.get(day.date) ?? []) {
            const trade = tradesByKey.get(order.key);
            const label = entryLabel(order.entry_type);
            const end = Math.min(last, order.end ?? day.close);
            orderSelect.add(new Option(day.date + " " + time(order.signal_time) + " · " +
                label + " · " + order.status, order.key));
            signalStops.set(order.signal_time, order.initial_stop);
            markers.push({time: order.signal_time, position: "aboveBar", color: "#f1c375", shape: "circle",
                text: label + " · SL " + orderPrice(order.initial_stop)});
            const active = Math.min(last, order.time);
            const segment = [{time: active, value: order.initial_stop}];
            if (end > active) segment.push({time: end, value: order.initial_stop});
            overlays.push(addLine(price, segment, {
                title: "Initial stop", color: "#ff8a8a", lineWidth: 1, priceFormat,
                lineStyle: library.LineStyle.Dashed, pointMarkersVisible: segment.length === 1,
            }));
            if (trade) {
                markers.push(
                    {time: trade.entry_time, position: "belowBar", color: "#58daba", shape: "arrowUp", text: "Buy"},
                    {time: Math.min(trade.exit_time, last), position: "aboveBar",
                        color: "#ff8a8a", shape: "arrowDown", text: "Exit"},
                );
            }
        }
    }
    candles.setData(bars);
    volume.setData(vols);
    candles.setMarkers(markers.sort((a, b) => a.time - b.time));
    orderSelect.disabled = orderSelect.options.length < 2;
    byId("chart-legend").textContent = "Hover a candle to inspect OHLC, volume and VWAP.";
    byId("selected-trade").textContent = "";
    price.timeScale().setVisibleLogicalRange({from: 0, to: Math.min(119, bars.length - 1)});
    shownMonth = key;
}
month.addEventListener("change", () => {
    byId("trades").querySelector('[aria-selected="true"]')?.removeAttribute("aria-selected");
    showMonth(month.value);
});
const table = byId("trades");
const filter = byId("filter");
const fields = ["number", "date", "entry_type", "entry_time", "entry_price", "exit_time", "exit_price", "shares", "pnl",
    "commission", "slippage", "atr_close_pct", "mfe_pct"];
let sortColumn = 0;
let direction = 1;
function selectTrade(t, row) {
    month.value = t.date.slice(0, 7);
    if (shownMonth !== month.value) showMonth(month.value);
    clearTradeOverlays();
    const day = run.months[month.value].find(d => d.date === t.date);
    const last = day.bars.at(-1)[0];
    const from = Math.max(day.bars[0][0], t.signal_time - 300);
    const rangeAvailable = day.bars[15][0];
    for (const [title, value] of [["Range high", day.range_high], ["Range low", day.range_low]]) {
        rangeLines.push(addLine(price, [{time: rangeAvailable, value}, {time: last, value}], {
            title, color: "#7eb8f1", lineWidth: 1, lineStyle: library.LineStyle.Dashed,
        }));
    }
    for (const [title, value, color] of [
        ["Buy stop", t.entry_trigger, "#58daba"], ["Initial stop", t.initial_stop, "#ff8a8a"],
    ]) {
        levels.push(candles.createPriceLine({title, price: value, color, lineWidth: 1, lineStyle: library.LineStyle.Dashed}));
    }
    const stopPath = [[t.entry_time, t.initial_stop], ...t.stop_updates];
    const stopEnd = Math.min(last, t.exit_time);
    if (stopEnd > stopPath.at(-1)[0]) stopPath.push([stopEnd, stopPath.at(-1)[1]]);
    stopLine = addLine(price, stopPath.map(([time, value]) => ({time, value})), {
        color: "#ff8a8a", lineWidth: 2, lineType: library.LineType.WithSteps,
    });
    const exitLabels = {time: "13:00 exit", stop: "Protective stop", early_close: "Early-close liquidation"};
    const selectedOrder = orderWindows.get(keyOf(t.date, t.order_id));
    orderSelect.value = selectedOrder.key;
    byId("selected-trade").textContent = "Trade " + t.number + " · " + t.date + " · " + entryLabel(t.entry_type) + " · Signal " +
        time(t.signal_time) + " · Initial stop " + orderPrice(t.initial_stop) +
        " · Working " + time(selectedOrder.time) + "–" + time(selectedOrder.end) +
        " · Buy " + time(t.entry_time) + " at " + fillPrice.format(t.entry_price) +
        " · Exit " + time(t.exit_time) + " at " + fillPrice.format(t.exit_price) +
        " · " + exitLabels[t.exit_reason] + " · " + t.stop_updates.length +
        " stop raises" + (t.ambiguous ? " · Adverse same-minute sequence" : "");
    price.timeScale().setVisibleRange({from, to: Math.min(last, t.exit_time + 600)});
    table.querySelector('[aria-selected="true"]')?.removeAttribute("aria-selected");
    row?.setAttribute("aria-selected", "true");
    byId("trade-chart").scrollIntoView({block: "start"});
}
function renderTrades() {
    const field = fields[sortColumn];
    const shown = [...tradesByKey.values()].filter(t => filter.value === "all" || (filter.value === "wins" ? t.pnl > 0 : t.pnl < 0));
    shown.sort((a, b) => {
        if (a[field] == null) return b[field] == null ? 0 : 1;
        if (b[field] == null) return -1;
        const value = typeof a[field] === "string" ? a[field].localeCompare(b[field]) : a[field] - b[field];
        return direction * value;
    });
    const body = table.tBodies[0];
    body.replaceChildren();
    for (const t of shown) {
        const row = body.insertRow();
        row.dataset.order = keyOf(t.date, t.order_id);
        row.tabIndex = 0;
        for (const value of [t.number, t.date, t.entry_type === "entry1" ? "1" : "2", time(t.entry_time), fillPrice.format(t.entry_price),
            time(t.exit_time), fillPrice.format(t.exit_price), formatCount.format(t.shares), money(t.pnl),
            money(t.commission), money(t.slippage), pct(t.atr_close_pct), pct(t.mfe_pct)]) {
            row.insertCell().textContent = value;
        }
        row.cells[fields.indexOf("pnl")].className = t.pnl >= 0 ? "positive" : "negative";
        row.addEventListener("click", () => selectTrade(t, row));
        row.addEventListener("keydown", event => {
            if (event.key === "Enter") selectTrade(t, row);
        });
    }
    if (!shown.length) {
        const cell = body.insertRow().insertCell();
        cell.colSpan = fields.length;
        cell.textContent = "No trades for this selection.";
    }
}
for (const [index, header] of Array.from(table.tHead.rows[0].cells).entries()) {
    header.tabIndex = 0;
    function sort() {
        direction = index === sortColumn ? -direction : 1;
        sortColumn = index;
        renderTrades();
    }
    header.addEventListener("click", sort);
    header.addEventListener("keydown", event => { if (event.key === "Enter") sort(); });
}
filter.addEventListener("change", renderTrades);
orderSelect.addEventListener("change", () => {
    const order = orderWindows.get(orderSelect.value);
    if (!order) {
        showMonth(month.value);
        table.querySelector('[aria-selected="true"]')?.removeAttribute("aria-selected");
        return;
    }
    const trade = tradesByKey.get(order.key);
    if (trade) {
        const row = [...table.tBodies[0].rows].find(item => item.dataset.order === order.key);
        selectTrade(trade, row);
        return;
    }
    clearTradeOverlays();
    for (const [title, value, color] of [
        ["Buy stop", order.entry_trigger, "#58daba"], ["Initial stop", order.initial_stop, "#ff8a8a"],
    ]) {
        levels.push(candles.createPriceLine({title, price: value, color, lineWidth: 1,
            lineStyle: library.LineStyle.Dashed}));
    }
    table.querySelector('[aria-selected="true"]')?.removeAttribute("aria-selected");
    const day = run.months[month.value].find(item => item.date === order.date);
    const last = day.bars.at(-1)[0];
    price.timeScale().setVisibleRange({from: Math.max(day.bars[0][0], order.signal_time - 300),
        to: Math.min(last, (order.end ?? day.close) + 300)});
    byId("selected-trade").textContent = entryLabel(order.entry_type) + " · " + order.date + " · Signal " +
        time(order.signal_time) + " · Buy stop " + orderPrice(order.entry_trigger) +
        " · Initial stop " + orderPrice(order.initial_stop) + " · " +
        time(order.time) + "–" + time(order.end ?? day.close) + " · " + order.status;
});
function showRun() {
    selected = run.scenarios.find(item => item.scenario_id === runSelect.value && item.rule_version === rulesSelect.value);
    indexSelectedRun();
    showSummary();
    showEquity();
    showMonth(month.value);
    renderTrades();
}
runSelect.addEventListener("change", () => {
    filter.value = "all";
    showRun();
});
rulesSelect.addEventListener("change", () => {
    showScenarios(runSelect.value);
    filter.value = "all";
    showRun();
});
showScenarios("both-base");
showRun();
