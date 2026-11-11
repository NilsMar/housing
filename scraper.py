"""
Enriches partial listing records. Fetches the listing page (via requests
with Playwright fallback for anti-scraping blocks) and uses Claude Haiku
to extract structured property data.
"""

import json
import re
import time

import anthropic
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env


def _fetch_html(url: str) -> str:
    time.sleep(2)
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
        return resp.text
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code in (403, 429):
            print(f"  [scraper] {e.response.status_code} from requests — retrying with Playwright")
            return _fetch_with_playwright(url)
        raise


def _fetch_with_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-GB,en;q=0.9"})
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2000)
            return page.content()
        finally:
            browser.close()


def _compact_html(html: str, max_chars: int = 15000) -> str:
    """Reduce page HTML to key content for LLM processing."""
    soup = BeautifulSoup(html, "lxml")

    # Collect JSON-LD structured data (compact, machine-readable)
    ld_parts = []
    for tag in soup.find_all("script", type="application/ld+json"):
        if tag.string:
            ld_parts.append(tag.string.strip())

    # Collect useful meta tags (image, description)
    meta_parts = []
    for meta in soup.find_all("meta"):
        prop = meta.get("property") or meta.get("name", "")
        content = meta.get("content", "")
        if content and any(p in prop for p in ("og:image", "og:title", "description")):
            meta_parts.append(f"{prop}: {content}")

    # Strip everything that isn't readable content
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript", "iframe"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)

    parts = []
    if ld_parts:
        parts.append("=== STRUCTURED DATA ===\n" + "\n".join(ld_parts))
    if meta_parts:
        parts.append("=== META ===\n" + "\n".join(meta_parts))
    parts.append("=== PAGE TEXT ===\n" + text)

    return "\n\n".join(parts)[:max_chars]


def _normalise_tenure(raw: str | None, lease_years: int | None = None) -> tuple[str, int | None]:
    if not raw:
        return "unknown", None
    raw_lower = raw.lower()
    if "share" in raw_lower and "freehold" in raw_lower:
        return "share_freehold", None
    if "freehold" in raw_lower:
        return "freehold", None
    if "leasehold" in raw_lower:
        return "leasehold", lease_years
    return "unknown", None


_SCHEMA = """{
  "price": integer GBP (no currency symbol),
  "bedrooms": integer or null,
  "bathrooms": integer or null,
  "sq_ft": integer square feet or null,
  "tenure": "freehold" | "leasehold" | "share_freehold" | "unknown",
  "lease_years": integer remaining lease years or null,
  "has_garden": 1 or 0,
  "has_parking": 1 or 0,
  "condition": "new_build" | "modern" | "period" | "unknown",
  "epc_rating": single letter A-G or null,
  "address": "full display address string",
  "postcode": "SW1A 1AA" format or null,
  "lat": latitude float or null,
  "lng": longitude float or null,
  "description": "first 400 chars of property description text",
  "image_url": "https://..." or null
}"""


def _extract_with_haiku(content: str) -> dict:
    msg = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                "Extract property listing data from this UK property page. "
                "Return ONLY a valid JSON object with this schema:\n"
                + _SCHEMA
                + "\n\nProperty page content:\n"
                + content
            ),
        }],
    )
    text = msg.content[0].text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def enrich(listing: dict) -> dict:
    """Fetch the listing page and extract property data via Claude Haiku."""
    url = listing["url"]
    html = _fetch_html(url)
    content = _compact_html(html)
    raw = _extract_with_haiku(content)

    raw["tenure"], raw["lease_years"] = _normalise_tenure(
        raw.get("tenure"), raw.get("lease_years")
    )

    # Merge: extracted data wins, but don't overwrite non-null email data with None
    merged = dict(listing)
    for k, v in raw.items():
        if v is not None or merged.get(k) is None:
            merged[k] = v

    merged["enriched"] = 1
    return merged
