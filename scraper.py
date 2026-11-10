"""
Enriches partial listing records by scraping individual listing pages.
Each platform stores full data in a JSON blob embedded in the page HTML.
"""

import json
import re
import time

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


def _get(url: str) -> requests.Response:
    time.sleep(2)
    resp = SESSION.get(url, timeout=15)
    resp.raise_for_status()
    return resp


def _extract_json_var(html: str, var_name: str) -> dict | None:
    """Extract a JS variable assignment like `var_name = {...}` from page HTML."""
    pattern = re.compile(
        rf"{re.escape(var_name)}\s*=\s*(\{{.*?\}});?\s*</script>",
        re.DOTALL,
    )
    m = pattern.search(html)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    return None


def _extract_next_data(html: str) -> dict | None:
    """Extract Next.js __NEXT_DATA__ JSON blob."""
    soup = BeautifulSoup(html, "lxml")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        try:
            return json.loads(tag.string)
        except json.JSONDecodeError:
            return None
    return None


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


def _has_garden(features: list[str], description: str) -> bool:
    combined = " ".join(features) + " " + (description or "")
    return bool(re.search(r"\bgarden\b", combined, re.IGNORECASE))


def _has_parking(features: list[str], description: str) -> bool:
    combined = " ".join(features) + " " + (description or "")
    return bool(re.search(r"\b(parking|garage|car\s+space)\b", combined, re.IGNORECASE))


def _infer_condition(listing_type: str, features: list[str], description: str) -> str:
    combined = " ".join(features) + " " + (description or "") + " " + (listing_type or "")
    if re.search(r"\bnew\s+build\b", combined, re.IGNORECASE):
        return "new_build"
    if re.search(r"\b(modern|recently\s+renovated|refurbished|contemporary)\b", combined, re.IGNORECASE):
        return "modern"
    if re.search(r"\b(period|victorian|edwardian|georgian|original\s+features)\b", combined, re.IGNORECASE):
        return "period"
    return "unknown"


def scrape_rightmove(url: str) -> dict:
    resp = _get(url)
    html = resp.text

    # Rightmove embeds full data in window.PAGE_MODEL
    data = _extract_json_var(html, "window.PAGE_MODEL")

    # Fallback: try __PRELOADED_STATE__
    if not data:
        data = _extract_json_var(html, "window.__PRELOADED_STATE__")

    result: dict = {}

    if data:
        prop = data.get("propertyData", data)
        prices = prop.get("prices", {})
        result["price"] = prices.get("primaryPrice") or prop.get("price")
        result["bedrooms"] = prop.get("bedrooms")
        result["bathrooms"] = prop.get("bathrooms")
        result["sq_ft"] = (
            prop.get("floorArea", {}).get("sqFeet")
            or prop.get("floorArea", {}).get("value")
        )

        loc = prop.get("location", {})
        result["lat"] = loc.get("latitude")
        result["lng"] = loc.get("longitude")
        result["postcode"] = loc.get("outcode") or loc.get("postcode")

        tenure_raw = prop.get("tenure", {})
        if isinstance(tenure_raw, dict):
            tenure_str = tenure_raw.get("tenureType", "")
            lease_years = tenure_raw.get("yearsRemainingOnLease")
        else:
            tenure_str = str(tenure_raw)
            lease_years = None

        result["tenure"], result["lease_years"] = _normalise_tenure(tenure_str, lease_years)

        features = prop.get("keyFeatures", [])
        if isinstance(features, list):
            features = [str(f) for f in features]
        else:
            features = []

        desc = prop.get("description", {})
        if isinstance(desc, dict):
            description = desc.get("htmlSummary", "") or desc.get("shortDescription", "")
        else:
            description = str(desc)

        result["has_garden"] = int(_has_garden(features, description))
        result["has_parking"] = int(_has_parking(features, description))
        result["condition"] = _infer_condition(prop.get("propertySubType", ""), features, description)
        result["title"] = prop.get("address", {}).get("displayAddress") if isinstance(prop.get("address"), dict) else prop.get("address")
        result["address"] = result["title"]
        result["description"] = description[:500] if description else None

        images = prop.get("images", [])
        if images and isinstance(images, list):
            first = images[0]
            result["image_url"] = first.get("url") if isinstance(first, dict) else str(first)
    else:
        # Fallback: basic BeautifulSoup parse
        soup = BeautifulSoup(html, "lxml")
        result["title"] = soup.title.string if soup.title else None
        result["address"] = result.get("title")

    result["enriched"] = 1
    return result


def scrape_zoopla(url: str) -> dict:
    resp = _get(url)
    data = _extract_next_data(resp.text)
    result: dict = {}

    if data:
        # Zoopla Next.js data path varies; try common paths
        props = (
            data.get("props", {})
            .get("pageProps", {})
            .get("listingDetails", {})
        )
        if not props:
            # Alternate path
            props = data.get("props", {}).get("pageProps", {})

        result["price"] = props.get("pricing", {}).get("price") or props.get("price")
        result["bedrooms"] = props.get("numBedrooms") or props.get("bedrooms")
        result["bathrooms"] = props.get("numBathrooms") or props.get("bathrooms")
        result["sq_ft"] = props.get("floorArea", {}).get("value") if isinstance(props.get("floorArea"), dict) else None

        coords = props.get("location", {}).get("coordinates", {})
        result["lat"] = coords.get("lat")
        result["lng"] = coords.get("lng")
        result["postcode"] = props.get("address", {}).get("postcode") if isinstance(props.get("address"), dict) else None

        tenure_str = props.get("tenure") or ""
        result["tenure"], result["lease_years"] = _normalise_tenure(
            tenure_str, props.get("leaseExpiryYear")
        )

        features = props.get("features", []) or []
        if not isinstance(features, list):
            features = []
        description = props.get("description", "") or ""

        result["has_garden"] = int(_has_garden(features, description))
        result["has_parking"] = int(_has_parking(features, description))
        result["condition"] = _infer_condition(props.get("propertySubType", ""), features, description)

        addr = props.get("address", {})
        result["title"] = addr.get("displayAddress") if isinstance(addr, dict) else str(addr)
        result["address"] = result["title"]
        result["description"] = description[:500] if description else None
    else:
        soup = BeautifulSoup(resp.text, "lxml")
        result["title"] = soup.title.string if soup.title else None
        result["address"] = result.get("title")

    result["enriched"] = 1
    return result


def scrape_onthemarket(url: str) -> dict:
    resp = _get(url)
    soup = BeautifulSoup(resp.text, "lxml")
    result: dict = {}

    # Price
    price_el = soup.select_one("[data-testid='price'], .price, h2.price")
    if price_el:
        price_str = price_el.get_text(strip=True)
        m = re.search(r"[\d,]+", price_str.replace(",", ""))
        result["price"] = int(m.group(0)) if m else None

    # Beds/baths
    for el in soup.select("[data-testid='beds'], [class*='bedrooms']"):
        m = re.search(r"\d+", el.get_text())
        if m:
            result["bedrooms"] = int(m.group(0))
            break

    # Features
    features = [el.get_text(strip=True) for el in soup.select("li.feature, [data-testid='feature-list'] li")]
    description = ""
    desc_el = soup.select_one("[data-testid='description'], .description")
    if desc_el:
        description = desc_el.get_text(" ", strip=True)

    # Tenure from features or description
    tenure_text = " ".join(features) + " " + description
    result["tenure"], result["lease_years"] = _normalise_tenure(tenure_text)

    result["has_garden"] = int(_has_garden(features, description))
    result["has_parking"] = int(_has_parking(features, description))
    result["condition"] = _infer_condition("", features, description)

    # Address / title
    h1 = soup.find("h1")
    result["title"] = h1.get_text(strip=True) if h1 else None
    result["address"] = result["title"]
    result["description"] = description[:500] if description else None

    result["enriched"] = 1
    return result


SCRAPERS = {
    "rightmove": scrape_rightmove,
    "zoopla": scrape_zoopla,
    "onthemarket": scrape_onthemarket,
}


def enrich(listing: dict) -> dict:
    """Scrape the listing page and merge results into the listing dict."""
    platform = listing.get("platform")
    scraper = SCRAPERS.get(platform)
    if not scraper:
        return {**listing, "enriched": 1}

    scraped = scraper(listing["url"])
    # Merge: scraped data wins, but don't overwrite non-null email data with None
    merged = dict(listing)
    for k, v in scraped.items():
        if v is not None or merged.get(k) is None:
            merged[k] = v
    return merged
