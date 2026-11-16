# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-16

First public release.

### Added
- MCP server `olx` (stdio) with seven tools: `olx_find_location`, `olx_suggest_categories`,
  `olx_category_filters`, `olx_resolve_url`, `olx_search`, `olx_get_offer`, `olx_offer_photos`.
- Description quality check on every offer (`info.needs_photo_review` + `reasons`).
- `olx_offer_photos` returns offer photos as images so the model can inspect them.
- Requests go through one persistent, visible Chromium window (OLX blocks headless browsers
  and plain HTTP clients); at least 1 second between requests.
- English and Russian documentation.

[0.1.0]: https://github.com/dmytrochornyi/olx-mcp/releases/tag/v0.1.0
