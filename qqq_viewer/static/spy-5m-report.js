"use strict";

const get = id => document.getElementById(id);
const data = JSON.parse(get("report-data").textContent);
const ny = {timeZone: "America/New_York"};
const stamp = new Intl.DateTimeFormat("en-US", {
    ...ny, year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const sessionDate = new Intl.DateTimeFormat("en-US", {...ny, year: "numeric", month: "2-digit", day: "2-digit"});
const dayTick = new Intl.DateTimeFormat("en-US", {...ny, month: "short", day: "numeric"});
const clockTick = new Intl.DateTimeFormat("en-US", {...ny, hour: "2-digit", minute: "2-digit", hourCycle: "h23"});
const timestamp = time => stamp.format(time * 1000);
const dateAt = time => {
    const parts = Object.fromEntries(sessionDate.formatToParts(time * 1000).map(p => [p.type, p.value]));
    return `${parts.year}-${parts.month}-${parts.day}`;
};
const usd = new Intl.NumberFormat("en-US", {style: "currency", currency: "USD"});
const px = new Intl.NumberFormat("en-US", {minimumFractionDigits: 2, maximumFractionDigits: 4});
const count = new Intl.NumberFormat("en-US", {maximumFractionDigits: 2});
const price = n => n == null ? "—" : px.format(n);
const money = n => usd.format(n);
const pct = n => n == null ? "—" : `${n.toFixed(2)}%`;
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
const overlays = [];
const scopedBars = new Map();
const starts = new Map();
let active = data.scenarios[0];

function line(points, color, style = LightweightCharts.LineStyle.Solid) {
    if (points.length) {
        const series = intraday.addLineSeries({...quiet, color, lineStyle: style, lineWidth: 1});
        series.setData(points);
        overlays.push(series);
    }
}

function horizontal(start, end, value, color, style) {
    if (value == null) return;
    const points = [];
    for (let time = start; time <= end; time += STEP) points.push({time, value});
    line(points, color, style);
}

function legend(time) {
    const item = scopedBars.get(time);
    if (!item) return;
    const {bar, session} = item;
    const text = [timestamp(time), `O ${price(bar.open)}`, `H ${price(bar.high)}`, `L ${price(bar.low)}`,
        `C ${price(bar.close)}`, `Vol ${count.format(bar.volume)}`, `VWAP ${price(bar.vwap)}`,
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

function drawEvents(month) {
    const body = get("orders").tBodies[0];
    body.replaceChildren();
    get("event-month").textContent = `Events shown for ${month}`;
    const events = active.events.filter(event => dateAt(event.time).startsWith(month));
    if (!events.length) {
        addRow(body, ["No order events for this month"]).cells[0].colSpan = 7;
    } else {
        for (const event of events) {
            const row = addRow(body, [
                timestamp(event.time), event.action, event.detail, price(event.trigger), price(event.stop), event.shares ?? "—",
            ]);
            row.insertCell().append(button("Show event", () => {
                jump(dateAt(event.time), event.time, `Order event: ${event.action} · ${timestamp(event.time)} · ${event.detail}`);
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

function drawMonth(month, focusDay, focusTime) {
    const sessions = data.months[month] ?? [];
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
    for (const session of sessions) {
        const lastBar = session.close_utc - STEP;
        horizontal(session.open_utc, lastBar, session.upper, colors.upper);
        horizontal(session.open_utc, lastBar, session.lower, colors.lower);
        line(session.bars.filter(bar => bar.vwap != null).map(bar => ({time: bar.time, value: bar.vwap})), colors.vwap);
    }
    const markers = [];
    for (const trade of active.trades) {
        if (starts.has(trade.date)) {
            const {session} = starts.get(trade.date);
            markers.push({time: trade.signal_time, position: "aboveBar", shape: "circle", color: colors.upper, text: "Signal"});
            markers.push({time: trade.entry_time, position: "belowBar", shape: "arrowUp", color: colors.up, text: "Buy"});
            horizontal(trade.entry_time, Math.min(trade.exit_time, session.close_utc - STEP),
                trade.initial_stop, colors.down, LightweightCharts.LineStyle.Dashed);
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
    drawEvents(month);
}

function jump(day, time, description) {
    if (get("month").value === day.slice(0, 7)) showSession(day, time);
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
        addRow(body, ["No trades for this exit scenario"]).cells[0].colSpan = 16;
        return;
    }
    for (const trade of active.trades) {
        const row = addRow(body, [trade.date, timestamp(trade.signal_time), timestamp(trade.entry_time),
            price(trade.entry_price), price(trade.entry_trigger), price(trade.initial_stop), timestamp(trade.exit_time),
            price(trade.exit_price), exitNames[trade.exit_reason], count.format(trade.shares), money(trade.pnl),
            money(trade.commission), money(trade.slippage), money(trade.gap_slippage), trade.ambiguous ? "Yes" : "No",
        ]);
        row.addEventListener("click", () => jumpTrade(trade, false));
        const controls = row.insertCell();
        controls.append(button("Show entry", () => jumpTrade(trade, false)), " ", button("Show exit", () => jumpTrade(trade, true)));
    }
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
    drawMonth(get("month").value);
}

for (const [month] of Object.entries(data.months)) get("month").add(new Option(month, month));
get("month").value = data.coverage.end.slice(0, 7);
for (const item of data.scenarios) {
    get("scenario").add(new Option(item.label, item.id));
    const stats = item.stats;
    addRow(get("scenarios").tBodies[0], [item.label, count.format(stats.trades), money(stats.net_pnl),
        pct(stats.return_pct), pct(stats.max_drawdown_pct), pct(stats.win_rate), money(stats.commission),
        money(stats.slippage), money(stats.gap_slippage), count.format(stats.ambiguous),
    ]);
}
get("ticker").textContent = data.ticker;
const {entry, account} = data.settings;
const c = data.coverage;
get("parameters").textContent = `${c.start} through ${c.end} · Bull signal window ${entry.entry1_start_time}–${entry.entry1_end_time} ET`
    + ` · One setup per day · ${money(account.starting_balance)} start · ${account.risk_pct}% planned risk`;
get("coverage").textContent = `${count.format(c.signal_sessions)} signal sessions · ${count.format(c.signal_bars)} replay bars`
    + ` · ${count.format(c.warmup_bars)} ATR warmup bars · Published cache ${c.cache_start} through ${c.cache_end}`
    + (c.next_open_tail ? ` · ${c.next_open_tail} included only for a next-open exit` : "")
    + (c.next_open_tail_unavailable ? ` · Next open unavailable: ${c.next_open_tail_unavailable}` : "");
get("execution").textContent = `ATR(20) carries across regular sessions; trigger/stop buffers ${entry.entry_atr_buffer}/${entry.stop_atr_buffer}. `
    + `Order activates on the following bar for up to ${entry.entry_order_valid_minutes} minutes. Slippage is at least `
    + `${money(account.slippage_floor)} or ${account.slippage_bps} bp per fill. Ambiguous five-minute paths assume entry then stop. `
    + "Gaps, costs and slippage can raise losses above the sizing budget.";
get("provenance").textContent = JSON.stringify({settings: data.settings, fingerprints: data.fingerprints}, null, 2);
benchmark.setData(data.benchmark);
get("scenario").addEventListener("change", drawScenario);
get("month").addEventListener("change", () => drawMonth(get("month").value));
drawScenario();
