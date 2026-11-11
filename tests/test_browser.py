"""Browser check for the summer chart's negative bar."""

from __future__ import annotations

import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

from road_toll.paths import SITE

_CHROME = (
    "/usr/local/bin/google-chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


class _SiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SITE), **kwargs)

    def log_message(self, format, *args):  # noqa: A003
        return


def _launch(playwright):
    try:
        return playwright.chromium.launch(headless=True)
    except Exception:
        for path in _CHROME:
            if Path(path).exists():
                return playwright.chromium.launch(headless=True, executable_path=path)
        raise


def test_northern_territory_summer_gap_extends_left_of_zero():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url, wait_until="networkidle")
            page.click('button.tab[data-tab="holiday"]')
            page.wait_for_selector('#page-holiday rect[data-label="NT"]', state="visible")
            zero = float(page.locator("#page-holiday .zero-line").get_attribute("x1"))
            bar = page.locator('#page-holiday rect[data-label="NT"]')
            label = page.locator('#page-holiday text[data-bar-label="NT"]')
            bar_x = float(bar.get_attribute("x"))
            bar_w = float(bar.get_attribute("width"))
            assert bar.get_attribute("data-sign") == "neg"
            assert bar_w >= 12
            assert bar_x + bar_w <= zero + 0.5
            assert (label.text_content() or "").strip() == "-4"
            assert float(label.get_attribute("x")) < zero
            positive = page.locator('#page-holiday rect[data-label="VIC"]')
            assert positive.get_attribute("data-sign") == "pos"
            assert float(positive.get_attribute("x")) >= zero - 0.5
            browser.close()
    finally:
        server.shutdown()
