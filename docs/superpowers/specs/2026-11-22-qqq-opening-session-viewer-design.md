# QQQ opening-session viewer

## Purpose

Browse QQQ one-minute bars to study patterns in the first two hours of the US regular trading session. Keep the entire regular session available so the user can scroll through the rest of the day and into adjacent sessions.

## Approaches

- **Local viewer.** One browser page displays one selected month from a local data cache. A local server serves the chart and the requested month. The user chose this approach for browsing between months in the same viewer.
- **Monthly HTML reports.** Generate a standalone chart for each month and link them with a month picker. The files open directly in a browser. A data refresh requires regenerating the affected reports.

## Historical data

- Fetch QQQ one-minute trade OHLCV bars through the open Interactive Brokers Trader Workstation, with regular trading hours enabled. Start on January 1, 2020 in New York time, with the first bars expected on the next trading session. End at the latest completed regular session as of the run. Use historical market data requests only. The viewer works from the cache independently of TWS.
- Qualify the QQQ contract and first verify that TWS returns January 2020 bars with usable price and volume. Check the response and volume units before backfilling. Report the available range and any unresolved gaps if the requested history cannot be obtained.
- Request bounded windows at a controlled pace. Save successful chunks durably and record the request window, instrument, source, response status, and received coverage so an interrupted run can resume. Preserve provider bars and their timestamps. Deduplicate identical overlapping bars by timestamp. If valid responses disagree, preserve both values and use the first accepted bar for the chart, legend, and SMA. Flag the affected minute and month so reruns cannot silently change the displayed history.
- Store timestamps in UTC and assign sessions and calendar months using America/New_York. Check expected minute coverage against the exchange calendar, including early closes, and show missing or incomplete coverage separately from scheduled closures. Keep missing bars visible as coverage issues and use received bars for the chart and SMA.
- Provide a repeatable command to resume missing windows and extend the cache through the latest completed session. Check local read-only TWS API connectivity and market data access at run time. Report any connection, entitlement, pacing, or coverage failure with its affected range.

## Viewer

- Serve the viewer on localhost. Use the dark candlestick chart, crosshair legend, and lower volume area in the supertrend_etf report as the visual reference.
- Provide a calendar month picker backed by cached coverage. Default to the latest cached month. On opening a month, load only its plotted bars and open the chart on the first available trading day's 09:30–11:30 New York time window. Preserve horizontal pan and zoom within the selected month.
- Display the full regular session for each day and draw a vertical divider at each session open. Compress overnight, weekend, and holiday gaps. Keep New York date and time visible on the crosshair so the user can tell which day they are inspecting.
- Show open, high, low, close, and volume for the hovered minute. Plot a volume histogram and the simple average of the last 20 received regular-session volume bars, including the current bar. Carry the rolling window across session and calendar-month boundaries. Leave the SMA blank until 20 bars are available at the start of recorded history. The volume SMA is the sole indicator.
- Display the selected month's coverage status and use clear empty and error states. Keep browsing and data refresh separate operations.

## Acceptance

- Verify a real TWS response for an early session and reconcile the final acquired range against the exchange calendar. Report the cutoff and any remaining gaps.
- In the browser, verify month selection, two-hour opening zoom, left and right scrolling, New York crosshair timestamps, session dividers, and OHLCV values against cached data.
- Check the displayed SMA against the preceding 20 received bars at a session open and a month boundary. Check an early close and a daylight-saving transition.
- Verify that rerunning after a partial download resumes from saved coverage and avoids duplicate bars.

The viewer startup command, cache format, and TWS client are implementation choices. Live chart streaming, order handling, and strategy signals are outside this design.
