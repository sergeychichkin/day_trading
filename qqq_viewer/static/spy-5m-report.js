"use strict";

const get = id => document.getElementById(id);
const data = JSON.parse(get("report-data").textContent);
const ny = {timeZone: "America/New_York"};
const stamp = new Intl.DateTimeFormat("en-US", {
    ...ny, year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const dayTick = new Intl.DateTimeFormat("en-US", {...ny, month: "short", day: "numeric"});
const clockTick = new Intl.DateTimeFormat("en-US", {...ny, hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
const timestamp = time => stamp.format(time * 1000);
const clockTime = time => clockTick.format(time * 1000);
const usd = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
const px = new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 4});
const count = new Intl.NumberFormat("en-US", {maximumFractionDigits: 2});
const price = n => n == null ? "—" : px.format(n);
const money = n => usd.format(n);
const pct = n => n == null ? "—" : `${n.toFixed(2)}%`;
const profitFactor = n => n === "infinite" ? "∞" : n == null ? "—" : n.toFixed(2);
const colors = {up: "#10b981", down: "#ef4444", upper: "#f1c375", lower: "#7eb8f1", vwap: "#c6a1fa"};
const exitNames = {protective_stop: "Protective stop", same_day_close: "Same-day close", next_session_open: "Next-session open"};
const exitMarkers = {protective_stop: "Stop", same_day_close: "Day close", next_session_open: "Next open"};
const STEP = 300;

function chart(id, priceChart = false) {
    return LightweightCharts.createChart(get(id), {
        autoSize: true,
        layout: {background: {color: "#101824"}, textColor: "#aebbd0", fontFamily: "Segoe UI, sans-serif"},
        grid: {vertLines: {visible: false}, horzLines: {color: "#202b3d"}},
        rightPriceScale: {borderColor: "#273347"},
        crosshair: {mode: LightweightCharts.CrosshairMode.Normal},
        localization: {timeFormatter: timestamp},
        timeScale: {
            borderColor: "#273347", timeVisible: priceChart, lockVisibleTimeRangeOnResize: true,
            ...(priceChart ? {tickMarkFormatter: (time, type) =>
                (type < 3 ? dayTick : clockTick).format(time * 1000)} : {}),
        },
    });
}

const equity = chart("equity");
const drawdown = chart("drawdown");
const intraday = chart("price", true);
const quiet = {priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false};
const equityLine = equity.addLineSeries({...quiet, color: "#58daba"});
const benchmark = equity.addLineSeries({...quiet, color: colors.lower});
const drawdownLine = drawdown.addLineSeries({...quiet, color: "#ff8a8a", priceFormat: {
    type: "custom", formatter: pct, minMove: 0.01,
}});
const candles = intraday.addCandlestickSeries({
    ...quiet, upColor: colors.up, downColor: colors.down, wickUpColor: colors.up, wickDownColor: colors.down,
    borderVisible: false,
});
const volumeScale = {priceScaleId: "volume", priceFormat: {type: "volume"}};
const volume = intraday.addHistogramSeries({...quiet, ...volumeScale});
const volumeAverage = intraday.addLineSeries({...quiet, ...volumeScale, title: "Volume SMA(20)",
    color: "#58daba", lineWidth: 1});
intraday.priceScale("volume").applyOptions({scaleMargins: {top: 0.81, bottom: 0.02}});
const {high_atr_multiplier: highMultiplier, low_atr_multiplier: lowMultiplier} = data.settings.chart;
const atrOverlays = [
    {id: "high-atr", label: `High + ${highMultiplier} ATR`, field: "high", multiplier: highMultiplier, color: "#f4ae75"},
    {id: "low-atr", label: `Low − ${lowMultiplier} ATR`, field: "low", multiplier: -lowMultiplier, color: "#85a5f1"},
].map(({id, label, field, multiplier, color}) => {
    const checkbox = get(id);
    get(`${id}-label`).textContent = label;
    checkbox.checked = false;
    const series = intraday.addLineSeries({...quiet, title: label, color, visible: false, lineWidth: 1});
    checkbox.addEventListener("change", () => series.applyOptions({visible: checkbox.checked}));
    return {field, multiplier, series};
});
const overlays = [];
const scopedBars = new Map();
const starts = new Map();
let active = data.scenarios[0];

function line(points, color, style = LightweightCharts.LineStyle.Solid, showSinglePoint = false) {
    if (points.length) {
        const series = intraday.addLineSeries({...quiet, color, lineStyle: style, lineWidth: 1,
            pointMarkersVisible: showSinglePoint && points.length === 1, pointMarkersRadius: 3});
        series.setData(points);
        overlays.push(series);
    }
}

function horizontal(start, end, value, color, style, showSinglePoint = false) {
    if (value == null) return;
    const points = [];
    for (let time = start; time <= end; time += STEP) points.push({time, value});
    line(points, color, style, showSinglePoint);
}

function legend(time) {
    const item = scopedBars.get(time);
    if (!item) return;
    const {bar, session} = item;
    const text = [timestamp(time), `O ${price(bar.open)}`, `H ${price(bar.high)}`, `L ${price(bar.low)}`,
        `C ${price(bar.close)}`, `Vol ${count.format(bar.volume)} ${data.coverage.volume_unit}`,
        `Vol SMA(20) ${price(bar.volume_sma_20)}`, `VWAP ${price(bar.vwap)}`,
        `Upper ${price(session.upper)}`, `Lower ${price(session.lower)}`];
    if (session.levels_status !== "available") text.push(session.levels_status.replaceAll("_", " "));
    if (session.exit_only) text.push("Exit-only session");
    get("chart-legend").textContent = text.join(" · ");
}

intraday.subscribeCrosshairMove(({time}) => {
    if (time != null) legend(time);
});

function addRow(body, values) {
    const row = body.insertRow();
    for (const value of values) row.insertCell().textContent = value;
    return row;
}

function drawEvents(month, lastMonth) {
    const body = get("orders").tBodies[0];
    body.replaceChildren();
    get("event-month").textContent = month === lastMonth ? `Events shown for ${month}`
        : `Events shown for ${month} through ${lastMonth}`;
    const events = active.events.filter(event => starts.has(event.date));
    if (!events.length) {
        addRow(body, ["No order events in this chart window"]).cells[0].colSpan = 7;
    } else {
        for (const event of events) {
            const row = addRow(body, [
                timestamp(event.time), event.action, event.detail, price(event.trigger), price(event.stop), event.shares ?? "—",
            ]);
            row.insertCell().append(button("Show event", () => {
                jump(event.date, event.time, `Order event · ${timestamp(event.time)} · ${event.detail}`);
            }));
        }
    }
}

function showSession(day, time) {
    const item = starts.get(day);
    if (!item) return;
    intraday.timeScale().setVisibleLogicalRange({from: item.first, to: item.last});
    const anchor = Math.min(time ?? item.session.open_utc, item.session.close_utc - STEP);
    legend(anchor);
}

function scenarioSessions(month) {
    return (data.months[month] ?? []).filter(session => !session.exit_only || active.id === "next_session_open");
}

function nextMonth(month) {
    const [year, index] = month.split("-").map(Number);
    return index === 12 ? `${year + 1}-01` : `${year}-${String(index + 1).padStart(2, "0")}`;
}

function chartSessions(month) {
    return [...scenarioSessions(month), ...scenarioSessions(nextMonth(month))];
}

function drawVolumeAndATR(prices) {
    volume.setData(prices.map(bar => ({
        time: bar.time, value: bar.volume,
        color: bar.close >= bar.open ? "#10b98166" : "#ef444466",
    })));
    volumeAverage.setData(prices.filter(bar => bar.volume_sma_20 != null)
        .map(bar => ({time: bar.time, value: bar.volume_sma_20})));
    for (const {field, multiplier, series} of atrOverlays) {
        series.setData(prices.filter(bar => bar.atr != null)
            .map(bar => ({time: bar.time, value: bar[field] + multiplier * bar.atr}))
            .filter(point => Number.isFinite(point.value)));
    }
}

function drawStops(trade, sessions, endTime) {
    const adjustments = trade.stop_adjustments;
    let start = trade.entry_time;
    let stop = trade.initial_stop;
    const drawSegment = end => {
        for (const session of sessions) {
            if (trade.exit_reason === "next_session_open" && session.date !== trade.date) continue;
            horizontal(Math.max(start, session.open_utc), Math.min(end, session.close_utc - STEP),
                stop, colors.down, LightweightCharts.LineStyle.Dashed, true);
        }
    };
    for (const adjustment of adjustments) {
        drawSegment(Math.min(endTime, adjustment.effective_time - STEP));
        start = adjustment.effective_time;
        stop = adjustment.new_stop;
    }
    drawSegment(endTime);
}

function drawMonth(month, focusDay, focusTime) {
    const sessions = chartSessions(month);
    get("month").value = month;
    get("selected-trade").textContent = "";
    scopedBars.clear();
    starts.clear();
    candles.setMarkers([]);
    for (const series of overlays.splice(0)) intraday.removeSeries(series);
    const prices = [];
    for (const session of sessions) {
        const first = prices.length;
        for (const bar of session.bars) {
            prices.push(bar);
            scopedBars.set(bar.time, {bar, session});
        }
        starts.set(session.date, {first, last: prices.length - 1, session});
    }
    candles.setData(prices);
    drawVolumeAndATR(prices);
    for (const session of sessions) {
        const lastBar = session.close_utc - STEP;
        horizontal(session.open_utc, lastBar, session.upper, colors.upper);
        horizontal(session.open_utc, lastBar, session.lower, colors.lower);
        line(session.bars.filter(bar => bar.vwap != null).map(bar => ({time: bar.time, value: bar.vwap})), colors.vwap);
    }
    const markers = [];
    const position = active.open_position;
    const charts = position ? [...active.trades, {...position, date: position.entry_date}] : active.trades;
    for (const trade of charts) {
        if (starts.has(trade.date)) {
            markers.push({time: trade.signal_time, position: "aboveBar", shape: "circle", color: colors.upper, text: "Signal"});
            markers.push({time: trade.entry_time, position: "belowBar", shape: "arrowUp", color: colors.up, text: "Buy"});
        }
        const end = trade.exit_time ?? trade.mark_time;
        if (sessions.length && trade.entry_time <= sessions.at(-1).close_utc && end >= sessions[0].open_utc) {
            drawStops(trade, sessions, end);
        }
        for (const adjustment of trade.stop_adjustments) {
            if (scopedBars.has(adjustment.candle_time)) {
                markers.push({time: adjustment.candle_time, position: "belowBar", shape: "circle",
                    color: colors.down, text: `Stop raised to ${price(adjustment.new_stop)}`});
            }
        }
        if (starts.has(trade.exit_date)) {
            const {session} = starts.get(trade.exit_date);
            markers.push({time: Math.min(trade.exit_time, session.close_utc - STEP),
                position: "aboveBar", shape: "arrowDown", color: colors.down,
                text: exitMarkers[trade.exit_reason] + (trade.ambiguous ? " · ambiguous" : "")});
        }
    }
    candles.setMarkers(markers.sort((a, b) => a.time - b.time));
    intraday.priceScale("right").applyOptions({autoScale: true});
    if (sessions.length) showSession(focusDay ?? sessions[0].date, focusTime);
    drawEvents(month, sessions.at(-1)?.date.slice(0, 7) ?? month);
}

function jump(day, time, description) {
    if (starts.has(day)) showSession(day, time);
    else drawMonth(day.slice(0, 7), day, time);
    get("selected-trade").textContent = description;
    const chartSection = get("trade-chart");
    chartSection.focus({preventScroll: true});
    chartSection.scrollIntoView({block: "start"});
}

function jumpTrade(trade, exit) {
    const day = exit ? trade.exit_date : trade.date;
    const time = exit ? trade.exit_time : trade.entry_time;
    jump(day, time, `${exit ? "Exit" : "Entry"}: ${timestamp(time)} · ${exitNames[trade.exit_reason]}`
        + (trade.ambiguous ? " · Ambiguous path: simulated entry then stop" : ""));
}

function button(label, click) {
    const node = document.createElement("button");
    node.type = "button";
    node.textContent = label;
    node.addEventListener("click", event => { event.stopPropagation(); click(); });
    return node;
}

function drawTradeLog() {
    const body = get("trades").tBodies[0];
    body.replaceChildren();
    if (!active.trades.length) {
        addRow(body, ["No trades for this exit scenario"]).cells[0].colSpan = get("trades").tHead.rows[0].cells.length;
        return;
    }
    for (const trade of active.trades) {
        const row = addRow(body, [trade.date, clockTime(trade.signal_time), clockTime(trade.bear_time), clockTime(trade.entry_time),
            price(trade.entry_price), price(trade.initial_stop), timestamp(trade.exit_time),
            price(trade.exit_price), exitNames[trade.exit_reason], count.format(trade.shares),
            trade.mfe_r.toFixed(2), trade.mae_r.toFixed(2), money(trade.pnl),
            money(trade.commission), money(trade.slippage),
        ]);
        row.addEventListener("click", () => jumpTrade(trade, false));
        const controls = row.insertCell();
        controls.append(button("Show entry", () => jumpTrade(trade, false)), " ", button("Show exit", () => jumpTrade(trade, true)));
    }
}

function drawOpenPosition() {
    const summary = get("open-position");
    const position = active.open_position;
    summary.replaceChildren();
    summary.hidden = !position;
    if (!position) return;
    const mark = `Unrealized P&L ${money(position.unrealized_pnl)} at ${price(position.mark_price)} on ${timestamp(position.mark_time)}`;
    const queuedStop = position.pending_stop == null ? "" : ` · Next session stop ${price(position.pending_stop)}`;
    const details = document.createElement("p");
    details.textContent = `Open position · ${position.entry_date} ${clockTime(position.entry_time)} · ${count.format(position.shares)} shares`
        + ` at ${price(position.entry_price)} · Active stop ${price(position.active_stop)}${queuedStop} · ${mark}`;
    summary.append(details, button("Show open entry", () =>
        jump(position.entry_date, position.entry_time, `Open entry · ${timestamp(position.entry_time)}`)));
}

function drawScenario() {
    active = data.scenarios.find(item => item.id === get("scenario").value) ?? data.scenarios[0];
    const stats = active.stats;
    get("ending-equity").textContent = money(stats.ending_equity);
    get("net-pnl").textContent = money(stats.net_pnl);
    get("trade-count").textContent = count.format(stats.trades);
    get("max-drawdown").textContent = pct(stats.max_drawdown_pct);
    get("selected-equity").textContent = active.label;
    [...get("scenarios").tBodies[0].rows].forEach((row, i) =>
        row.classList.toggle("active-scenario", data.scenarios[i] === active));
    equityLine.setData(active.equity);
    let peak = data.settings.account.starting_balance;
    drawdownLine.setData(active.equity.map(({time, value}) => {
        peak = Math.max(peak, value);
        return {time, value: 100 * (value / peak - 1)};
    }));
    equity.timeScale().fitContent();
    drawdown.timeScale().fitContent();
    const skips = Object.entries(active.skips).map(([key, value]) => `${key.replaceAll("_", " ")}: ${count.format(value)}`);
    get("skips").textContent = skips.length ? `No-trade sessions (${active.label}): ${skips.join(" · ")}` : "";
    drawTradeLog();
    drawOpenPosition();
    const picker = get("month");
    const previous = picker.value;
    const months = Object.keys(data.months).filter(month => scenarioSessions(month).length);
    picker.replaceChildren(...months.map(month => new Option(month, month)));
    drawMonth(months.includes(previous) ? previous : data.coverage.end.slice(0, 7));
}

for (const item of data.scenarios) {
    get("scenario").add(new Option(item.label, item.id));
    const stats = item.stats;
    addRow(get("scenarios").tBodies[0], [item.label, count.format(stats.trades), money(stats.net_pnl),
        pct(stats.return_pct), pct(stats.max_drawdown_pct), pct(stats.win_rate), profitFactor(stats.profit_factor), money(stats.commission),
        money(stats.slippage), money(stats.gap_slippage), count.format(stats.ambiguous),
    ]);
}
get("ticker").textContent = data.ticker;
const {entry, account} = data.settings;
const c = data.coverage;
get("parameters").textContent = `${c.start} through ${c.end} · Bull signal window ${entry.entry1_start_time}–${entry.entry1_end_time} ET`
    + ` · One setup per day · ${money(account.starting_balance)} start · ${account.risk_pct}% planned risk`;
get("coverage").textContent = `${count.format(c.signal_sessions)} signal sessions · ${count.format(c.signal_bars)} signal bars`
    + ` · ${count.format(c.warmup_bars)} ATR warmup bars · Published cache ${c.cache_start} through ${c.cache_end}`
    + (c.next_open_tail ? ` · ${c.next_open_tail} included only for a next-open exit` : "")
    + (c.next_open_tail_unavailable ? ` · Next open unavailable: ${c.next_open_tail_unavailable}` : "");
get("execution").textContent = `ATR(20) carries across regular sessions; trigger/stop buffers ${entry.entry_atr_buffer}/${entry.stop_atr_buffer}. `
    + `Order activates on the following bar for up to ${entry.entry_order_valid_minutes} minutes. Slippage is at least `
    + `$${data.settings.account_exact?.slippage_floor ?? account.slippage_floor} or ${account.slippage_bps} bp per fill. `
    + "Ambiguous five-minute paths assume entry then stop. "
    + "Gaps, costs and slippage can raise losses above the sizing budget."
    + (data.settings.trailing_stop ? " Exit 3 carries one position across regular sessions and can raise its stop from the next bar." : "");
get("provenance").textContent = JSON.stringify({settings: data.settings, fingerprints: data.fingerprints}, null, 2);
benchmark.setData(data.benchmark);
get("scenario").addEventListener("change", drawScenario);
get("month").addEventListener("change", () => drawMonth(get("month").value));
drawScenario();
