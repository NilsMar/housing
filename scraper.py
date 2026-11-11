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


def _extract_json_ld_listing(html: str) -> dict | None:
    """Extract the schema.org RealEstateListing from JSON-LD markup."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if data.get("@type") == "RealEstateListing":
                return data
        except (json.JSONDecodeError, AttributeError):
            continue
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


def _decode_rm_page_model(html: str) -> dict | None:
    """Decode Rightmove's current index-encoded __PAGE_MODEL format."""
    m = re.search(r"window\.__PAGE_MODEL\s*=\s*(\{.*)", html, re.DOTALL)
    if not m:
        return None
    text = m.group(1)
    depth = end = 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    try:
        raw = json.loads(text[:end])
        arr = json.loads(raw["data"])
    except (json.JSONDecodeError, KeyError):
        return None

    def resolve(idx):
        val = arr[idx]
        if isinstance(val, dict):
            return {k: resolve(v) for k, v in val.items()}
        if isinstance(val, list):
            return [resolve(v) for v in val]
        return val

    top = resolve(0)
    return top.get("propertyData")


def scrape_rightmove(url: str) -> dict:
    resp = _get(url)
    html = resp.text
    result: dict = {}

    prop = _decode_rm_page_model(html)

    # Fallback to legacy single-underscore format
    if not prop:
        raw = _extract_json_var(html, "window.PAGE_MODEL") or _extract_json_var(html, "window.__PRELOADED_STATE__")
        if raw:
            prop = raw.get("propertyData", raw)

    if prop:
        # Price — new format returns "£800,000" string; old format returns int
        price_raw = (prop.get("prices") or {}).get("primaryPrice") or prop.get("price")
        if price_raw:
            price_clean = re.sub(r"[^\d]", "", str(price_raw))
            result["price"] = int(price_clean) if price_clean else None

        result["bedrooms"] = prop.get("bedrooms")
        result["bathrooms"] = prop.get("bathrooms")

        # Floor area: new format has sizings list; old format has floorArea dict
        for sizing in prop.get("sizings") or []:
            if isinstance(sizing, dict) and sizing.get("unit") == "sqft":
                result["sq_ft"] = sizing.get("minimumSize")
                break
        if not result.get("sq_ft"):
            fa = prop.get("floorArea") or {}
            result["sq_ft"] = fa.get("sqFeet") or fa.get("value")

        loc = prop.get("location") or {}
        result["lat"] = loc.get("latitude")
        result["lng"] = loc.get("longitude")

        addr = prop.get("address") or {}
        result["title"] = addr.get("displayAddress") if isinstance(addr, dict) else str(addr)
        result["address"] = result["title"]
        result["postcode"] = addr.get("outcode") or addr.get("postcode") if isinstance(addr, dict) else None

        tenure_raw = prop.get("tenure") or {}
        tenure_str = tenure_raw.get("tenureType", "") if isinstance(tenure_raw, dict) else str(tenure_raw)
        lease_years = tenure_raw.get("yearsRemainingOnLease") if isinstance(tenure_raw, dict) else None
        result["tenure"], result["lease_years"] = _normalise_tenure(tenure_str, lease_years)

        features = prop.get("keyFeatures") or []
        features = [str(f) for f in features] if isinstance(features, list) else []

        # Description: new format nests under text.description; old under description
        text_obj = prop.get("text")
        if isinstance(text_obj, dict):
            description = text_obj.get("description") or text_obj.get("shortDescription") or ""
        else:
            desc = prop.get("description") or {}
            description = (desc.get("htmlSummary") or desc.get("shortDescription") or "") if isinstance(desc, dict) else str(desc)

        result["has_garden"] = int(_has_garden(features, description))
        result["has_parking"] = int(_has_parking(features, description))
        result["condition"] = _infer_condition(str(prop.get("propertySubType") or ""), features, description)
        result["description"] = description[:500] if description else None

        images = prop.get("images") or []
        if images and isinstance(images, list):
            first = images[0]
            result["image_url"] = first.get("url") if isinstance(first, dict) else str(first)
    else:
        soup = BeautifulSoup(html, "lxml")
        result["title"] = soup.title.string if soup.title else None
        result["address"] = result.get("title")

    result["enriched"] = 1
    return result


def scrape_zoopla(url: str) -> dict:
    resp = _get(url)
    html = resp.text
    data = _extract_next_data(html)
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
        soup = BeautifulSoup(html, "lxml")
        result["title"] = soup.title.string if soup.title else None
        result["address"] = result.get("title")

    # JSON-LD schema is always present and reliable for price, beds, baths, floor area
    ld = _extract_json_ld_listing(html)
    if ld:
        ld_props = {p["name"]: p["value"] for p in ld.get("additionalProperty", []) if isinstance(p, dict)}
        if not result.get("price"):
            result["price"] = int((ld.get("offers") or {}).get("price") or 0) or None
        if not result.get("bedrooms"):
            result["bedrooms"] = int(ld_props.get("Bedrooms") or 0) or None
        if not result.get("bathrooms"):
            result["bathrooms"] = int(ld_props.get("Bathrooms") or 0) or None
        # Floor size: "941 sq. ft" or "1,501 sq. ft" — strip non-digits
        if not result.get("sq_ft"):
            floor_str = ld_props.get("Floor size", "")
            if floor_str:
                nums = re.sub(r"[^\d]", "", floor_str)
                if nums:
                    result["sq_ft"] = int(nums)

    result["enriched"] = 1
    return result


def scrape_onthemarket(url: str) -> dict:
    resp = _get(url)
    result: dict = {}

    data = _extract_next_data(resp.text)
    prop = (data or {}).get("props", {}).get("pageProps", {}).get("initialReduxState", {}).get("property", {})
    meta = (data or {}).get("props", {}).get("pageProps", {}).get("initialReduxState", {}).get("metadata", {})

    if prop or meta:
        dl = meta.get("dataLayer", {})
        price_str = dl.get("price", "")
        result["price"] = int(re.sub(r"[^\d]", "", price_str)) if price_str else None

        result["bedrooms"] = prop.get("bedrooms")
        result["bathrooms"] = prop.get("bathrooms")

        loc = prop.get("location", {})
        result["lat"] = loc.get("lat")
        result["lng"] = loc.get("lon")
        result["postcode"] = dl.get("postcode")

        # Address from page title: "Cleveland Road, London, N1 2 bed apartment for sale - £795,000"
        page_title = meta.get("pageTitles", {}).get("page-title", "")
        m_addr = re.match(r"^(.+?)\s+\d+\s+bed\b", page_title)
        addr = m_addr.group(1).rstrip(", ") if m_addr else dl.get("addressline_2", "")
        result["title"] = addr
        result["address"] = addr

        # Tenure from keyInfo structured data
        for item in prop.get("keyInfo") or []:
            if item.get("title") == "Tenure" and not item.get("isUnknown"):
                val = item.get("value", "")
                # e.g. "Leasehold  |  88 yrs left" or "Freehold"
                lease_m = re.search(r"(\d+)\s*yr", val, re.IGNORECASE)
                lease_years = int(lease_m.group(1)) if lease_m else None
                result["tenure"], result["lease_years"] = _normalise_tenure(val, lease_years)
                break
        if not result.get("tenure"):
            result["tenure"] = "unknown"

        description = prop.get("description", "") or ""
        # Strip HTML tags
        description = re.sub(r"<[^>]+>", " ", description).strip()
        features = [f.get("description", "") for f in (prop.get("keyFeatures") or []) if isinstance(f, dict)]

        result["has_garden"] = int(_has_garden(features, description))
        result["has_parking"] = int(_has_parking(features, description))
        result["condition"] = _infer_condition(str(prop.get("propertyType") or ""), features, description)
        result["description"] = description[:500] if description else None

        images = prop.get("images") or []
        if images and isinstance(images, list):
            first = images[0]
            result["image_url"] = first.get("src") or first.get("url") if isinstance(first, dict) else str(first)
    else:
        soup = BeautifulSoup(resp.text, "lxml")
        result["title"] = soup.title.string if soup.title else None
        result["address"] = result.get("title")

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
