#!/usr/bin/env python3
"""
Automated screenshot script for Themarr web UI.

Uses Playwright to launch a real browser, intercepts all /api/* calls with
mock data, and captures the README screenshot set in both dark and light
themes (poster view, list view, YouTube downloader, copy-theme modal, and
Plex download modal). Generated mock poster/thumbnail artwork is served so card
visuals are meaningful in captures. Screenshots are written to the
screenshots/ directory in the repo root.

Usage:
    pip install playwright
    playwright install chromium
    python3 scripts/take_screenshots.py

The script requires no running Plex server — all API responses are mocked.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate repo root (one level above this script)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOTS_DIR = REPO_ROOT / "screenshots"

# ---------------------------------------------------------------------------
# Mock API data that the browser intercept layer will return
# ---------------------------------------------------------------------------
MOCK_STATUS = {
    "connected": True,
    "server_name": "My Plex Server",
    "version": "1.32.0",
}

MOCK_LIBRARIES = [
    {"id": 1, "key": 1, "title": "TV Shows", "type": "show", "totalSize": 42},
    {"id": 2, "key": 2, "title": "Movies",   "type": "movie", "totalSize": 18},
]

# 12 realistic-looking TV show entries
_TV_ITEMS_RAW = [
    ("Breaking Bad",      2008, True,  True),
    ("Chernobyl",         2019, True,  True),
    ("Dark",              2017, True,  True),
    ("Game of Thrones",   2011, True,  False),
    ("House of the Dragon", 2022, False, False),
    ("Mindhunter",        2017, True,  True),
    ("Ozark",             2017, True,  False),
    ("Peaky Blinders",    2013, True,  True),
    ("Succession",        2018, False, False),
    ("The Bear",          2022, True,  True),
    ("The Crown",         2016, True,  False),
    ("Yellowjackets",     2021, False, False),
]

MOCK_TV_ITEMS = [
    {
        "ratingKey": 100 + i,
        "title": title,
        "year": year,
        "thumb": f"/library/metadata/{100 + i}/thumb",
        "type": "show",
        "has_plex_theme": has_plex,
        "has_local_theme": has_local,
        "theme_size": 245760 if has_local else 0,
        "local_path": f"/tv/{title.replace(' ', '_')}",
    }
    for i, (title, year, has_plex, has_local) in enumerate(_TV_ITEMS_RAW)
]

_MOVIE_ITEMS_RAW = [
    ("Dune", 2021, True, True),
    ("Inception", 2010, True, True),
    ("Oppenheimer", 2023, True, False),
    ("Interstellar", 2014, True, False),
    ("The Dark Knight", 2008, True, True),
    ("The Batman", 2022, False, False),
]

MOCK_MOVIE_ITEMS = [
    {
        "ratingKey": 300 + i,
        "title": title,
        "year": year,
        "thumb": f"/library/metadata/{300 + i}/thumb",
        "type": "movie",
        "has_plex_theme": has_plex,
        "has_local_theme": has_local,
        "theme_size": 245760 if has_local else 0,
        "local_path": f"/movies/{title.replace(' ', '_')}",
    }
    for i, (title, year, has_plex, has_local) in enumerate(_MOVIE_ITEMS_RAW)
]

MOCK_ITEMS_BY_KEY = {
    int(item["ratingKey"]): item for item in (MOCK_TV_ITEMS + MOCK_MOVIE_ITEMS)
}
MOCK_YOUTUBE_SEARCH = {
    "results": [
        {
            "id": "ilfYnhXD-bE",
            "title": "Breaking Bad Main Title Theme (Extended)",
            "url": "https://www.youtube.com/watch?v=ilfYnhXD-bE",
            "channel": "Dave Porter - Topic",
            "duration": "1:16",
            "thumbnail": "https://i.ytimg.com/vi/ilfYnhXD-bE/hqdefault.jpg",
            "view_count": 14687926,
        },
        {
            "id": "3U6PSWyv5sc",
            "title": "Breaking Bad Full Intro Title Sequence",
            "url": "https://www.youtube.com/watch?v=3U6PSWyv5sc",
            "channel": "AMC",
            "duration": "1:16",
            "thumbnail": "https://i.ytimg.com/vi/3U6PSWyv5sc/hqdefault.jpg",
            "view_count": 8234567,
        },
        {
            "id": "HEmx23LwFhI",
            "title": "Breaking Bad - Theme",
            "url": "https://www.youtube.com/watch?v=HEmx23LwFhI",
            "channel": "SoundtrackHub",
            "duration": "0:18",
            "thumbnail": "https://i.ytimg.com/vi/HEmx23LwFhI/hqdefault.jpg",
            "view_count": 5123456,
        },
        {
            "id": "NYnDrbv7uJs",
            "title": "Breaking Bad Main Theme Extended Version",
            "url": "https://www.youtube.com/watch?v=NYnDrbv7uJs",
            "channel": "TV Themes",
            "duration": "11:05",
            "thumbnail": "https://i.ytimg.com/vi/NYnDrbv7uJs/hqdefault.jpg",
            "view_count": 2345678,
        },
        {
            "id": "PvcmS31dIPw",
            "title": "Breaking Bad Theme - 10 Hour Loop",
            "url": "https://www.youtube.com/watch?v=PvcmS31dIPw",
            "channel": "LoopMaster",
            "duration": "10:00:00",
            "thumbnail": "https://i.ytimg.com/vi/PvcmS31dIPw/hqdefault.jpg",
            "view_count": 987654,
        },
    ]
}


def _safe_svg_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _build_mock_poster_svg(title: str, media_type: str, width: int, height: int) -> bytes:
    icon = "📺" if media_type == "show" else "🎬"
    top = "#2f5d8a" if media_type == "show" else "#6b3d87"
    bottom = "#1b2735" if media_type == "show" else "#2d1f3a"
    safe_title = _safe_svg_text(title)
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{top}"/>
      <stop offset="100%" stop-color="{bottom}"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="url(#bg)"/>
  <rect x="12" y="12" width="{width - 24}" height="{height - 24}" rx="12" fill="none" stroke="rgba(255,255,255,0.28)" stroke-width="2"/>
  <text x="50%" y="44%" text-anchor="middle" font-size="54">{icon}</text>
  <text x="50%" y="66%" text-anchor="middle" fill="#eef4ff" font-family="Arial, sans-serif" font-size="22" font-weight="700">{safe_title}</text>
</svg>"""
    return svg.encode("utf-8")


def _start_flask(port: int = 18080) -> subprocess.Popen:
    """Start web_app.py on *port* as a subprocess and return the Popen handle."""
    env = os.environ.copy()
    env.update({
        "PLEX_URL":   "http://127.0.0.1:19999",  # non-existent — API is mocked
        "PLEX_TOKEN": "mock_token",
        "TV_PATH":    "/tv",
        "MOVIES_PATH": "/movies",
        "DEFAULT_THEME": "dark",
        "FLASK_DEBUG": "0",
        "PORT": str(port),
    })
    proc = subprocess.Popen(
        [sys.executable, str(REPO_ROOT / "web_app.py")],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give Flask a moment to start
    time.sleep(2)
    return proc


# ---------------------------------------------------------------------------
# Main screenshot logic
# ---------------------------------------------------------------------------

def take_screenshots(base_url: str = "http://127.0.0.1:18080") -> None:
    try:
        from playwright.sync_api import sync_playwright, Route, Request
    except ImportError:
        print("ERROR: playwright is not installed.")
        print("  pip install playwright && playwright install chromium")
        sys.exit(1)

    SCREENSHOTS_DIR.mkdir(exist_ok=True)
    for png_path in SCREENSHOTS_DIR.glob("*.png"):
        png_path.unlink()

    def route_handler(route: Route, request: Request) -> None:
        """Intercept /api/* requests and return mock JSON."""
        url = request.url
        if "/api/status" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_STATUS))
        elif "/api/cache/status" in url:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"ready": True, "sections_total": 2, "sections_completed": 2}),
            )
        elif "/api/libraries" in url and "/items" not in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_LIBRARIES))
        elif "/api/libraries/1/items" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_TV_ITEMS))
        elif "/api/libraries/2/items" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_MOVIE_ITEMS))
        elif "/api/youtube/search" in url:
            route.fulfill(status=200, content_type="application/json",
                          body=json.dumps(MOCK_YOUTUBE_SEARCH))
        elif "/api/poster/" in url:
            match = re.search(r"/api/poster/(\d+)", url)
            rating_key = int(match.group(1)) if match else None
            item = MOCK_ITEMS_BY_KEY.get(rating_key, {})
            poster_svg = _build_mock_poster_svg(
                title=str(item.get("title", "Themarr")),
                media_type=str(item.get("type", "show")),
                width=320,
                height=480,
            )
            route.fulfill(status=200, content_type="image/svg+xml", body=poster_svg)
        elif "i.ytimg.com/vi/" in url:
            thumb_svg = _build_mock_poster_svg(
                title="YouTube Preview",
                media_type="movie",
                width=320,
                height=180,
            )
            route.fulfill(status=200, content_type="image/svg+xml", body=thumb_svg)
        else:
            route.continue_()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        def new_page(theme: str = "dark"):
            page = browser.new_page(viewport={"width": 1500, "height": 960})
            page.route("**/api/**", route_handler)
            page.route("https://i.ytimg.com/**", route_handler)
            # Pre-set theme in localStorage before app scripts execute.
            page.add_init_script(
                f"""
                window.localStorage.setItem('themarr-theme', {json.dumps(theme)});
                // Keep this aligned with the server-rendered default in this script.
                window.localStorage.setItem('themarr-theme-default', 'dark');
                window.localStorage.setItem('themarr-view', 'list');
                """,
            )
            page.goto(base_url, wait_until="networkidle")
            page.wait_for_function(
                "(expected) => document.documentElement.dataset.theme === expected",
                arg=theme,
            )
            return page

        screenshot_plan = {
            "dark": {
                "poster": "01_poster_view_dark.png",
                "list": "02_list_view_dark.png",
                "youtube": "03_youtube_downloader_dark.png",
                "copy": "04_copy_theme_dark.png",
                "plex": "05_plex_download_dark.png",
            },
            "light": {
                "poster": "06_poster_view_light.png",
                "list": "07_list_view_light.png",
                "youtube": "08_youtube_downloader_light.png",
                "copy": "09_copy_theme_light.png",
                "plex": "10_plex_download_light.png",
            },
        }

        def capture_theme(theme: str, names: dict) -> None:
            page = new_page(theme)
            page.click("text=TV Shows")
            page.wait_for_selector("#library-view:not(.hidden)", timeout=5000)
            page.wait_for_selector(".item-card, .item-row", timeout=5000)
            page.wait_for_timeout(300)

            page.click("#view-btn-grid")
            page.wait_for_selector(".items-grid .item-card", timeout=5000)
            page.wait_for_timeout(250)
            page.screenshot(path=str(SCREENSHOTS_DIR / names["poster"]))
            print(f"  ✓ {names['poster']}")

            page.click("#view-btn-list")
            page.wait_for_selector(".items-list .item-row", timeout=5000)
            page.wait_for_timeout(250)
            page.screenshot(path=str(SCREENSHOTS_DIR / names["list"]))
            print(f"  ✓ {names['list']}")

            page.locator(".item-row .action-btn-youtube:visible").first.click()
            page.wait_for_selector("#modal-youtube:not(.hidden)", timeout=5000)
            page.wait_for_selector(".yt-result", timeout=5000)
            page.wait_for_timeout(250)
            page.screenshot(path=str(SCREENSHOTS_DIR / names["youtube"]))
            print(f"  ✓ {names['youtube']}")
            page.click("#modal-youtube .modal-close")
            page.wait_for_selector("#modal-youtube", state="hidden", timeout=5000)

            page.locator(".item-row .action-btn-copy:visible").first.click()
            page.wait_for_selector("#modal-copy-theme:not(.hidden)", timeout=5000)
            page.wait_for_selector("#copy-theme-source-item:not([disabled])", timeout=5000)
            page.wait_for_timeout(250)
            page.screenshot(path=str(SCREENSHOTS_DIR / names["copy"]))
            print(f"  ✓ {names['copy']}")
            page.click("#modal-copy-theme .modal-close")
            page.wait_for_selector("#modal-copy-theme", state="hidden", timeout=5000)

            page.locator(".item-row .action-btn-download:visible").first.click()
            page.wait_for_selector("#modal-download:not(.hidden)", timeout=5000)
            page.wait_for_function(
                "() => document.getElementById('modal-download-message').textContent.trim().length > 0",
            )
            page.wait_for_timeout(250)
            page.screenshot(path=str(SCREENSHOTS_DIR / names["plex"]))
            print(f"  ✓ {names['plex']}")
            page.close()

        capture_theme("dark", screenshot_plan["dark"])
        capture_theme("light", screenshot_plan["light"])
        browser.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = 18080
    print(f"Starting Flask on port {port}…")
    proc = _start_flask(port)
    try:
        print("Taking screenshots…")
        take_screenshots(base_url=f"http://127.0.0.1:{port}")
        print(f"\nDone. Screenshots saved to {SCREENSHOTS_DIR}/")
    finally:
        proc.terminate()
        proc.wait()
