"""Write a standalone SPY replay report without touching existing outputs."""

import html
import json
import re
from pathlib import Path

STATIC = Path(__file__).with_name("static")
TOKENS = re.compile(r"\{\{(TITLE|CSS|PAYLOAD|LIBRARY|SCRIPT)\}\}")


def write_report(path: Path, data: dict) -> None:
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
