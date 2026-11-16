"""Download offer photos from OLX's image CDN (public, no browser needed)."""

from __future__ import annotations

import asyncio
import re

import httpx

_SIZE_RE = re.compile(r";s=\d+x\d+")


def sized_url(url: str, size: int) -> str:
    """OLX CDN serves any size via ';s=WxH' (or a {width}/{height} template)."""
    url = url.replace("{width}", str(size)).replace("{height}", str(size))
    if _SIZE_RE.search(url):
        return _SIZE_RE.sub(f";s={size}x{size}", url)
    if "/image" in url and ";s=" not in url:
        return url + f";s={size}x{size}"
    return url


async def download(urls: list[str], size: int = 640) -> list[tuple[str, bytes | None, str]]:
    """Returns (url, bytes or None, mime/format or error) for each url, in order."""
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "olx-mcp"}) as client:

        async def one(u: str) -> tuple[str, bytes | None, str]:
            u = sized_url(u, size)
            try:
                r = await client.get(u)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "image/jpeg")
                if not ctype.startswith("image/"):
                    return u, None, f"not an image ({ctype})"
                return u, r.content, ctype.split("/", 1)[1].split(";")[0]
            except httpx.HTTPError as e:
                return u, None, f"download failed: {e}"

        return list(await asyncio.gather(*(one(u) for u in urls)))
