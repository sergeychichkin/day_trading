import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright



@pytest.fixture
def page():
    with sync_playwright() as playwright:
        channel = os.environ.get("QQQ_TEST_BROWSER_CHANNEL")
        if not channel and Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe").exists():
            channel = "msedge"
        browser = playwright.chromium.launch(channel=channel)
        yield browser.new_page(viewport={"width": 1280, "height": 820})
        browser.close()
