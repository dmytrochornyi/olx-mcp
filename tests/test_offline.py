"""Offline tests: no browser, no network."""

import json

from olx_mcp.api import STATE_RE, _clean, assess_description, summarize_offer
from olx_mcp.photos import sized_url

LONG = (
    "Rower w bardzo dobrym stanie, rama aluminiowa rozmiar 19 cali, koła 28 cali, "
    "przerzutki Shimano Deore 3x9, hamulce hydrauliczne, nowe opony Schwalbe, "
    "błotniki, bagażnik, oświetlenie na dynamo. Serwisowany w październiku."
)


def test_clean_strips_html_and_whitespace():
    assert _clean("<p>Hello<br/>  world</p>\n") == "Hello world"
    assert _clean(None) == ""


def test_no_description_flags_review():
    info = assess_description("iPhone 13", "", n_params=3, n_photos=5)
    assert info["needs_photo_review"] is True
    assert info["reasons"] == ["no description"]


def test_short_description():
    info = assess_description("iPhone 13", "Sprzedam telefon", 3, 5)
    assert info["needs_photo_review"]
    assert info["reasons"][0].startswith("very short description")


def test_good_description_passes():
    info = assess_description("Rower trekkingowy", LONG, 5, 6)
    assert info["needs_photo_review"] is False
    assert info["reasons"] == []


def test_points_to_photos():
    desc = LONG + " Więcej szczegółów na zdjęciach."
    info = assess_description("Rower trekkingowy", desc, 5, 6)
    assert "description points to the photos" in info["reasons"]


def test_no_photos_means_no_review_but_note():
    info = assess_description("Laptop", "", 0, 0)
    assert info["needs_photo_review"] is False
    assert "no photos either - ask the seller" in info["notes"]
    assert "few item parameters filled in" in info["notes"]


def test_sized_url():
    base = "https://ireland.apollo.olxcdn.com:443/v1/files/abc-PL/image"
    assert sized_url(base + ";s=4000x3000", 640) == base + ";s=640x640"
    assert sized_url(base, 300) == base + ";s=300x300"
    assert sized_url("https://x/img/{width}x{height}", 500) == "https://x/img/500x500"


def test_summarize_offer():
    raw = {
        "id": 1,
        "title": "Rower trekkingowy",
        "url": "https://www.olx.pl/d/oferta/rower-CID767-ID1.html",
        "description": f"<p>{LONG}</p>",
        "params": [
            {"key": "price", "value": {"value": 900, "currency": "PLN", "label": "900 zł"}},
            {"key": "state", "value": {"label": "Używane"}},
        ],
        "location": {"city": {"name": "Łódź"}, "region": {"name": "Łódzkie"}},
        "photos": [{"link": "https://x/image;s={width}x{height}"}],
        "delivery": {"rock": {"active": True}},
    }
    s = summarize_offer(raw)
    assert s["price"] == "900 zł"
    assert s["condition"] == "Używane"
    assert s["location"] == "Łódź, Łódzkie"
    assert s["delivery"] is True
    assert s["info"]["photo_count"] == 1


def test_state_regex_string_encoded():
    state = {"ad": {"ad": {"id": 5, "title": "x"}}}
    html = f"<script>window.__PRERENDERED_STATE__= {json.dumps(json.dumps(state))};window.x=1</script>"
    m = STATE_RE.search(html)
    assert m
    parsed = json.loads(m.group(1))
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    assert parsed["ad"]["ad"]["id"] == 5
