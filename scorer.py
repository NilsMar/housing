"""
Weighted scoring model for property listings.
Produces a 0–100 score and a per-dimension breakdown dict.
"""

import json
import math
import os

import yaml
from geopy.distance import geodesic


def _load_config() -> dict:
    with open("config.yaml") as f:
        return yaml.safe_load(f)


def _geocode_postcode(postcode: str) -> tuple[float, float] | None:
    """Resolve a UK postcode to (lat, lng) via postcodes.io — no API key needed."""
    import requests

    clean = postcode.replace(" ", "")
    try:
        resp = requests.get(
            f"https://api.postcodes.io/postcodes/{clean}", timeout=5
        )
        resp.raise_for_status()
        result = resp.json().get("result", {})
        return result.get("latitude"), result.get("longitude")
    except Exception:
        return None


_HOME_COORDS: tuple[float, float] | None = None


def _home_coords(config: dict) -> tuple[float, float]:
    global _HOME_COORDS
    if _HOME_COORDS is None:
        postcode = os.getenv("HOME_POSTCODE") or config["criteria"].get("home_postcode", "")
        if postcode:
            coords = _geocode_postcode(postcode)
            if coords and coords[0]:
                _HOME_COORDS = coords
        if _HOME_COORDS is None:
            raise RuntimeError("HOME_POSTCODE env var not set and no home_postcode in config.yaml")
    return _HOME_COORDS


def _score_location(commute_m: int | None, lat: float | None, lng: float | None, config: dict) -> float:
    # Prefer TfL commute time when available
    if commute_m is not None:
        max_commute = config["criteria"].get("max_commute_minutes", 30)
        return max(0.0, 10.0 * (1 - commute_m / max_commute))
    # Fallback to straight-line distance
    if not lat or not lng:
        return 5.0
    home = _home_coords(config)
    dist_km = geodesic(home, (lat, lng)).km
    radius = config["criteria"]["radius_km"]
    return max(0.0, 10.0 * (1 - dist_km / radius))


def _score_price(price: int | None, bedrooms: int | None, config: dict) -> float:
    if not price or not bedrooms:
        return 5.0
    medians = config["criteria"].get("median_prices", {})
    bed_key = min(int(bedrooms), 4)
    expected = medians.get(bed_key) or medians.get(str(bed_key)) or 650000
    ratio = price / expected  # 1.0 = at median
    # score = 10 at median, 0 at 100% above median
    return max(0.0, min(10.0, 10.0 * (2.0 - ratio)))


def _score_bedrooms(bedrooms: int | None) -> float:
    if not bedrooms:
        return 5.0
    mapping = {1: 3, 2: 7, 3: 9}
    return float(mapping.get(int(bedrooms), 10))


def _score_outdoor(has_garden: int) -> float:
    # has_garden is 0/1; we store richer data as text in description
    # Garden = 10, unknown = 5, confirmed none = 0
    if has_garden == 1:
        return 10.0
    return 4.0  # slight penalty for unknown / no garden info


def _score_tenure(tenure: str | None, lease_years: int | None) -> float:
    if not tenure or tenure == "unknown":
        return 5.0
    if tenure == "freehold":
        return 10.0
    if tenure == "share_freehold":
        return 8.0
    if tenure == "leasehold":
        if lease_years is None:
            return 4.0
        if lease_years >= 900:
            return 6.0
        if lease_years >= 500:
            return 5.0
        if lease_years >= 250:
            return 4.0
        if lease_years >= 100:
            return 2.0
        return 0.0
    return 5.0


def _score_condition(condition: str | None) -> float:
    mapping = {
        "new_build": 10.0,
        "modern": 8.0,
        "period": 7.0,
        "unknown": 5.0,
    }
    return mapping.get(condition or "unknown", 5.0)


def score_listing(listing: dict) -> tuple[float, dict]:
    """
    Returns (total_score_0_to_100, dimension_breakdown_dict).
    Dimension scores are 0–10 before weighting.
    """
    config = _load_config()
    weights = config["weights"]

    dimensions = {
        "location": _score_location(listing.get("commute_m"), listing.get("lat"), listing.get("lng"), config),
        "price_value": _score_price(listing.get("price"), listing.get("bedrooms"), config),
        "bedrooms": _score_bedrooms(listing.get("bedrooms")),
        "outdoor": _score_outdoor(listing.get("has_garden", 0)),
        "tenure": _score_tenure(listing.get("tenure"), listing.get("lease_years")),
        "condition": _score_condition(listing.get("condition")),
    }

    total_weight = sum(weights.values())
    # dimensions are 0–10, weights sum to 100 → weighted average is 0–10 → scale to 0–100
    total = sum(
        dimensions[dim] * weights.get(dim, 0) / total_weight
        for dim in dimensions
    ) * 10

    return round(total, 2), {k: round(v, 2) for k, v in dimensions.items()}


def compute_commute_time(listing: dict) -> int | None:
    """
    Returns fastest public transport journey time in minutes from the listing
    to the home postcode, using the TfL Journey Planner API (free, no key required).
    """
    import requests

    config = _load_config()
    home_postcode = os.getenv("HOME_POSTCODE") or config["criteria"].get("home_postcode", "")
    if not home_postcode:
        return None

    lat, lng = listing.get("lat"), listing.get("lng")
    if not lat or not lng:
        postcode = listing.get("postcode")
        if postcode:
            coords = _geocode_postcode(postcode)
            if coords and coords[0]:
                lat, lng = coords

    if not lat or not lng:
        return None

    from_loc = f"{lat},{lng}"
    to_loc = home_postcode.replace(" ", "")

    params = {
        "mode": "tube,bus,overground,national-rail,dlr,elizabeth-line",
        "journeyPreference": "LeastTime",
        "time": "0800",
        "timeIs": "Departing",
    }
    app_key = os.getenv("TFL_APP_KEY")
    if app_key:
        params["app_key"] = app_key

    try:
        resp = requests.get(
            f"https://api.tfl.gov.uk/Journey/JourneyResults/{from_loc}/to/{to_loc}",
            params=params,
            timeout=10,
        )
        resp.raise_for_status()
        journeys = resp.json().get("journeys", [])
        if journeys:
            return min(j["duration"] for j in journeys)
    except Exception as e:
        print(f"[scorer] TfL API failed for {listing.get('id')}: {e}")

    return None


def compute_distance(listing: dict) -> float | None:
    """Return distance in km from home postcode. Geocodes postcode if lat/lng missing."""
    config = _load_config()
    lat, lng = listing.get("lat"), listing.get("lng")

    if not lat or not lng:
        postcode = listing.get("postcode")
        if postcode:
            coords = _geocode_postcode(postcode)
            if coords and coords[0]:
                lat, lng = coords

    if not lat or not lng:
        return None

    home = _home_coords(config)
    return round(geodesic(home, (lat, lng)).km, 3)
