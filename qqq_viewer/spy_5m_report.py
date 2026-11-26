"""Write a standalone SPY replay report without touching existing outputs."""

import html
import json
import os
import re
from pathlib import Path

STATIC = Path(__file__).with_name("static")
TOKENS = re.compile(r"\{\{(TITLE|CSS|PAYLOAD|LIBRARY|SCRIPT)\}\}")


def _validate_windows_output_name(path) -> None:
    name = os.fspath(path)
    remainder = name[2:] if re.match(r"^[A-Za-z]:", name) else name
    if ":" in remainder:
        raise ValueError("The report path cannot use Windows alternate data streams.")


def write_report(path: Path, data: dict) -> None:
    if os.name == "nt":
        _validate_windows_output_name(path)
    values = {
        "TITLE": html.escape(f'{data["ticker"]} Five-Minute Entry 1 Backtest'),
        "CSS": (STATIC / "opening-range.css").read_text(encoding="utf-8-sig"),
        "LIBRARY": (STATIC / "vendor/lightweight-charts.js").read_text(encoding="utf-8-sig"),
        "SCRIPT": (STATIC / "spy-5m-report.js").read_text(encoding="utf-8-sig"),
        "PAYLOAD": json.dumps(data, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c"),
    }
    template = (STATIC / "spy-5m-report.html").read_text(encoding="utf-8-sig")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        file.write(TOKENS.sub(lambda match: values[match[1]], template))
