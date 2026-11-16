# olx-mcp

[Русская версия](README.ru.md)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude Code, Claude Desktop,
openclaw, zeroclaw or any other MCP client) **search [OLX.pl](https://www.olx.pl)**, the Polish
classifieds site, **without an account**.

It can also **look at the photos**: each offer gets a quality check of its description. When the text
is missing, too short or generic, the offer is flagged and the model can fetch the photos as images and
describe what is actually being sold.

> **Unofficial.** This project is not affiliated with OLX. It uses the same internal JSON endpoints
> the olx.pl website uses, which can change at any time. Use it for personal, low-volume searching
> and respect the OLX terms of service.

## What you can ask

- "Find a used PS5 in Kraków under 1500 zł, newest first."
- "Show mini PCs with Ryzen 7 across Poland and tell me which are underpriced."
- "Open this offer and check on the photos whether the bike has damage."
- "Which categories have 'dyson' offers, and how many?"

## Features

- Text search with category, location + radius, price range, sort and category-specific filters.
- Location lookup (city / district / region ids) and category discovery with offer counts.
- Converts any olx.pl listing URL into search parameters.
- Full offer details: description, parameters, all photos, seller info, map position, delivery.
- Photos returned as images (configurable size, paging), downloaded straight from the OLX image CDN.
- Description quality check on every offer (`info.needs_photo_review`).
- Polite by design: one browser session, at least 1 second between requests to OLX.

## Requirements

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- Chromium or Google Chrome (the system one is used if found; otherwise Playwright's build)
- **A graphical display** (X11 or Wayland), or Xvfb on a headless server. See [How it works](#how-it-works).

## Installation

```bash
git clone https://github.com/dmytrochornyi/olx-mcp.git
cd olx-mcp
uv sync
# Only if you have no system Chromium/Chrome:
uv run playwright install chromium
```

Check that it starts (it waits for an MCP client on stdin; stop with Ctrl+C):

```bash
uv run olx-mcp
```

## Connecting to a client

### Claude Code

```bash
claude mcp add -s user olx -- uv run --directory /path/to/olx-mcp olx-mcp
```

Restart Claude Code, then check with `/mcp`.

### Claude Desktop and other JSON-configured clients

```json
{
  "mcpServers": {
    "olx": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/olx-mcp", "olx-mcp"],
      "env": {
        "WAYLAND_DISPLAY": "wayland-1",
        "XDG_RUNTIME_DIR": "/run/user/1000"
      }
    }
  }
}
```

Many clients start servers with a clean environment. If the server reports *No display*, pass your
display variables in `env` as above (`DISPLAY=:0` on X11). Check yours with
`echo $DISPLAY $WAYLAND_DISPLAY $XDG_RUNTIME_DIR`.

### Headless server (openclaw, zeroclaw, VPS)

Run under a virtual display:

```bash
xvfb-run -a uv run --directory /path/to/olx-mcp olx-mcp
```

## Tools

| Tool | Purpose |
|---|---|
| `olx_find_location(text)` | Find `city_id` / `region_id` / `district_id` for a place name (`"Łódź"`, `"Kraków"`). |
| `olx_suggest_categories(query, limit=15)` | Which categories contain offers for a query, with counts. Use the `category_id` to narrow searches (phones vs. cases). |
| `olx_category_filters(category_id)` | Category-specific filters and their allowed values (condition, brand, model, size…). |
| `olx_resolve_url(url)` | Turn an olx.pl listing URL into search parameters. |
| `olx_search(...)` | Search offers (parameters below). |
| `olx_get_offer(offer)` | Full details of one offer. `offer` is a URL, or an id returned by `olx_search` in the same session. |
| `olx_offer_photos(offer, max_photos=4, size=640, start=0)` | The offer's photos as images. `max_photos` 1–8, `size` 300–1500 px, `start` for paging. |

### `olx_search` parameters

| Parameter | Type | Notes |
|---|---|---|
| `query` | string | Free text. |
| `category_id` | int | From `olx_suggest_categories` or `olx_resolve_url`. |
| `city_id`, `region_id`, `district_id` | int | From `olx_find_location`. |
| `distance_km` | int | Radius around `city_id`. |
| `price_min`, `price_max` | number | PLN. |
| `sort` | `relevance` \| `newest` \| `price_asc` \| `price_desc` | Default `relevance`. |
| `offset`, `limit` | int | `limit` max 50. Page with `offset`. |
| `extra_filters` | object | Filters from `olx_category_filters`, e.g. `{"filter_enum_state": ["used"]}` or `{"owner_type": "private"}`. Lists become `key[0]`, `key[1]`… |

The result is `{total, offset, returned, offers: [...]}`. Each offer has `id`, `title`, `price`,
`location`, `created`, `refreshed`, `business`, `delivery`, `condition`, `url`, `description_snippet`
and `info`.

### Description check (`info`)

```json
{
  "description_chars": 42,
  "photo_count": 6,
  "needs_photo_review": true,
  "reasons": ["very short description (42 chars)"],
  "notes": ["few item parameters filled in"]
}
```

Possible `reasons`: `no description`, `very short description (N chars)` (under 80 characters),
`description mostly repeats the title`, `description is generic boilerplate`,
`description points to the photos` ("see the photos"). `needs_photo_review` is `true` when there is at
least one reason and the offer has photos. The server instructions tell the model to open the photos
in that case and describe what they show: the item, visible condition, damage, completeness, and
mismatches with the title.

## How it works

- **OLX blocks non-browser HTTP clients** (by TLS fingerprint) **and headless browsers.** So the server
  drives a real, visible Chromium window through Playwright and calls OLX's JSON endpoints from inside
  that page, like the website itself does. The window opens on the first request and stays open; you
  can minimise it, but don't close it while the server runs (it will be reopened if you do).
- **Offer details** are read from the data embedded in the offer page (`window.__PRERENDERED_STATE__`),
  because the offer JSON endpoint is not available to anonymous visitors.
- **Photos** are downloaded directly from the OLX image CDN with plain HTTP (no blocking there).
- **No login.** The browser has its own profile (cookies and consent only), separate from your normal
  browser.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OLX_MCP_CHROMIUM` | system `chromium` / `chrome`, else Playwright's | Path to the browser executable. |
| `OLX_MCP_PROFILE` | `~/.local/share/olx-mcp/profile` | Browser profile directory. |

## Limitations

- Unofficial internal API: OLX can change or block it without notice.
- Needs a display (or Xvfb); it cannot run as a pure headless process.
- `olx_get_offer` / `olx_offer_photos` by numeric id only work for offers returned by a search in the
  same server session. Otherwise pass the URL.
- Requests are serialized (one at a time, ≥1 s apart), so large scans take a while.
- Only olx.pl is supported (not olx.ua, olx.pt, etc.).
- Search only. Posting or managing listings is out of scope.

## Development

```bash
uv sync
uv run pytest          # offline unit tests, no browser needed
```

Project layout:

```
src/olx_mcp/
  server.py   MCP tools and server instructions
  api.py      OLX endpoints, result formatting, description check
  browser.py  the shared Chromium session
  photos.py   photo download from the image CDN
tests/        offline tests
```

## License

[MIT](LICENSE)
