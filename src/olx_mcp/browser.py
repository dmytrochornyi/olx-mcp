"""One shared, persistent Chromium session (real window) used to talk to OLX.

OLX rejects headless browsers and non-browser HTTP clients, so we drive a normal
Chromium window with its own profile. No login is used; the profile just keeps
cookies/consent between runs.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright

OLX = "https://www.olx.pl"

PROFILE_DIR = Path(
    os.environ.get("OLX_MCP_PROFILE", Path.home() / ".local/share/olx-mcp/profile")
).expanduser()


def _chromium_path() -> str | None:
    """Prefer an explicit path, then a system Chromium/Chrome; else Playwright's own build."""
    explicit = os.environ.get("OLX_MCP_CHROMIUM")
    if explicit:
        return explicit
    for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


class BrowserSession:
    def __init__(self) -> None:
        self._pw = None
        self._ctx: BrowserContext | None = None
        self._api_page: Page | None = None
        self._lock = asyncio.Lock()

    async def context(self) -> BrowserContext:
        async with self._lock:
            if self._ctx is not None:
                try:
                    _ = self._ctx.pages  # raises if the browser was closed by the user
                    if self._ctx.browser is None or self._ctx.browser.is_connected():
                        return self._ctx
                except Exception:
                    pass
                self._ctx = None
                self._api_page = None
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            if self._pw is None:
                self._pw = await async_playwright().start()
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
                raise RuntimeError(
                    "No display: OLX needs a visible Chromium window. Pass DISPLAY/WAYLAND_DISPLAY "
                    "(and XDG_RUNTIME_DIR) to the MCP server, or run it under Xvfb (xvfb-run)."
                )
            self._ctx = await self._pw.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                executable_path=_chromium_path(),
                headless=False,  # OLX blocks headless browsers
                locale="pl-PL",
                timezone_id="Europe/Warsaw",
                viewport={"width": 1280, "height": 900},
            )
            self._ctx.on("close", lambda _: self._reset())
            return self._ctx

    def _reset(self) -> None:
        self._ctx = None
        self._api_page = None

    async def api_page(self) -> Page:
        """A background tab parked on olx.pl, used to call OLX's JSON endpoints."""
        ctx = await self.context()
        if self._api_page is None or self._api_page.is_closed():
            pages = [p for p in ctx.pages if p.url in ("about:blank", "")]
            self._api_page = pages[0] if pages else await ctx.new_page()
        if not self._api_page.url.startswith(OLX):
            await self._api_page.goto(OLX + "/", wait_until="domcontentloaded")
        return self._api_page

    async def fetch_json(self, path: str, params: list[tuple[str, str]] | None = None) -> tuple[int, str]:
        page = await self.api_page()
        return await page.evaluate(
            """async ([path, params]) => {
                const url = new URL(path, location.origin);
                for (const [k, v] of params) url.searchParams.append(k, v);
                const r = await fetch(url, {headers: {Accept: 'application/json'}, credentials: 'include'});
                return [r.status, await r.text()];
            }""",
            [path, params or []],
        )

    async def page_html(self, url: str) -> tuple[int, str]:
        page = await self.api_page()
        resp = await page.goto(url, wait_until="domcontentloaded")
        if resp is None:
            return 0, ""
        return resp.status, await resp.text()

    async def close(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.close()
            except Exception:
                pass
        self._reset()
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
