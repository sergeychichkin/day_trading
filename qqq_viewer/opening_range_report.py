import html
import json
from pathlib import Path

STATIC = Path(__file__).with_name("static")


def write_report(path: Path, data: dict) -> None:
    template = (STATIC / "opening-range.html").read_text(encoding="utf-8-sig")
    chart_lib = (STATIC / "vendor" / "lightweight-charts.js").read_text(encoding="utf-8-sig")
    chart_code = (STATIC / "opening-range.js").read_text(encoding="utf-8-sig")
    css = (STATIC / "opening-range.css").read_text(encoding="utf-8-sig")
    payload = json.dumps(data, allow_nan=False, separators=(",", ":")).replace("<", "\\u003c")
    values = {
        "TITLE": html.escape(f'{data["ticker"]} Opening Range Backtest'),
        "CSS": css,
        "LIBRARY": chart_lib,
        "PAYLOAD": payload,
        "SCRIPT": chart_code,
    }
    page = template
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", value, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
