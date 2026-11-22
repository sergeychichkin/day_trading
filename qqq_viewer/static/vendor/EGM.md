# Bundled chart library

## Overview

This directory contains TradingView Lightweight Charts 4.1.1
(`lightweight-charts.js`) and its accompanying `lightweight-charts.LICENSE`.
The JavaScript bundle is minified and was not exhaustively inspected.

## Interface

- `lightweight-charts.js` exposes `window.LightweightCharts`, including
  `createChart`; the viewer uses this interface in `../chart.js`.

## Invariants

- The bundle header identifies version 4.1.1; retain its copyright notice and
  consult `lightweight-charts.LICENSE` when redistributing or modifying it.

## Side Effects

- The local callers use `createChart` to render and update charts in the DOM
  (`../chart.js` and `../opening-range.js`). Other bundle behavior has not
  been exhaustively verified.

## Verification

- From the repository root: `.\.venv\Scripts\python.exe -m pytest -q`.
