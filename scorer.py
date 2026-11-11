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

    clean = postcode.replace(" ", "").upper()
    try:
        resp = requests.get(f"https://api.postcodes.io/postcodes/{clean}", timeout=5)
        if resp.ok:
            result = resp.json().get("result", {})
            return result.get("latitude"), result.get("longitude")
        # Outcode fallback (e.g. "N1" with no sector/unit)
        resp = requests.get(f"https://api.postcodes.io/outcodes/{clean}", timeout=5)
        if resp.ok:
            result = resp.json().get("result", {})
            return result.get("latitude"), result.get("longitude")
    except Exception:
        pass
    return None


def _geocode_address(address: str) -> tuple[float, float] | None:
    """Geocode a UK street address via Nominatim (OSM). No API key needed."""
    import requests

    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": address + ", UK", "format": "json", "limit": 1, "countrycodes": "gb"},
            headers={"User-Agent": "housing-tracker/1.0"},
            timeout=5,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
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


def _score_location(lat: float | None, lng: float | None, config: dict) -> float:
    """Proximity to home postcode N1 3PJ — pure distance score."""
    if not lat or not lng:
        return 5.0
    home = _home_coords(config)
    dist_km = geodesic(home, (lat, lng)).km
    radius = config["criteria"]["radius_km"]
    return max(0.0, 10.0 * (1 - dist_km / radius))


def _score_commute(walk_to_station_m: int | None, config: dict) -> float:
    """Walk time in minutes to nearest tube/overground station."""
    if walk_to_station_m is None:
        return 5.0  # neutral when not yet fetched
    max_walk = config["criteria"].get("max_walk_to_station_minutes", 20)
    return max(0.0, 10.0 * (1 - walk_to_station_m / max_walk))


def _score_price(price: int | None, bedrooms: int | None, config: dict) -> float:
    if not price or not bedrooms:
        return 5.0
    medians = config["criteria"].get("median_prices", {})
    bed_key = min(int(bedrooms), 4)
    expected = medians.get(bed_key) or medians.get(str(bed_key)) or 650000
    ratio = price / expected  # 1.0 = at median
    # score = 10 at median, 0 at 100% above median
    return max(0.0, min(10.0, 10.0 * (2.0 - ratio)))


def _score_sq_m(sq_ft: int | None) -> float:
    if not sq_ft:
        return 5.0
    sq_m = sq_ft * 0.0929
    return max(0.0, min(10.0, sq_m / 10.0))  # 100 sq_m = 10/10


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


def _score_epc(epc_rating: str | None) -> float:
    if not epc_rating:
        return 5.0  # neutral when unknown
    return {"A": 10.0, "B": 8.5, "C": 7.0, "D": 5.0, "E": 3.0, "F": 1.5, "G": 0.0}.get(
        epc_rating.upper(), 5.0
    )


def score_listing(listing: dict) -> tuple[float, dict]:
    """
    Returns (total_score_0_to_100, dimension_breakdown_dict).
    Dimension scores are 0–10 before weighting.
    """
    config = _load_config()
    weights = config["weights"]

    dimensions = {
        "location": _score_location(listing.get("lat"), listing.get("lng"), config),
        "commute": _score_commute(listing.get("walk_to_station_m"), config),
        "price_value": _score_price(listing.get("price"), listing.get("bedrooms"), config),
        "sq_m": _score_sq_m(listing.get("sq_ft")),
        "outdoor": _score_outdoor(listing.get("has_garden", 0)),
        "tenure": _score_tenure(listing.get("tenure"), listing.get("lease_years")),
        "condition": _score_condition(listing.get("condition")),
        "epc": _score_epc(listing.get("epc_rating")),
    }

    total_weight = sum(weights.values())
    # dimensions are 0–10, weights sum to 100 → weighted average is 0–10 → scale to 0–100
    total = sum(
        dimensions[dim] * weights.get(dim, 0) / total_weight
        for dim in dimensions
    ) * 10

    return round(total, 2), {k: round(v, 2) for k, v in dimensions.items()}


def _resolve_coords(listing: dict) -> tuple[float | None, float | None]:
    """Return (lat, lng) from the listing, geocoding from address or postcode if missing."""
    lat, lng = listing.get("lat"), listing.get("lng")
    if lat and lng:
        return lat, lng
    address = listing.get("address") or listing.get("title")
    if address:
        coords = _geocode_address(address)
        if coords and coords[0]:
            return coords
    postcode = listing.get("postcode")
    if postcode:
        coords = _geocode_postcode(postcode)
        if coords and coords[0]:
            return coords
    return None, None


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

    lat, lng = _resolve_coords(listing)

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


def compute_nearest_station(listing: dict) -> tuple[str | None, int | None]:
    """
    Returns (station_name, walk_minutes) for the nearest tube/overground station
    using the TfL StopPoint API (no key required for basic queries).
    """
    import requests

    lat, lng = _resolve_coords(listing)
    if not lat or not lng:
        return None, None

    params = {
        "lat": lat,
        "lon": lng,
        "stopTypes": "NaptanMetroStation,NaptanRailStation",
        "radius": 1200,
        "useStopPointHierarchy": "false",
        "modes": "tube,overground,dlr,elizabeth-line",
    }
    app_key = os.getenv("TFL_APP_KEY")
    if app_key:
        params["app_key"] = app_key

    try:
        resp = requests.get("https://api.tfl.gov.uk/StopPoint", params=params, timeout=10)
        resp.raise_for_status()
        stops = resp.json().get("stopPoints", [])
        if not stops:
            return None, None

        nearest = min(stops, key=lambda s: s.get("distance", float("inf")))
        name = nearest.get("commonName", "")
        for suffix in (" Underground Station", " Overground Station", " Rail Station",
                       " DLR Station", " Elizabeth Line Station", " Station"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break

        dist_m = nearest.get("distance", 0)
        walk_min = max(1, round(dist_m / 80))  # 80 m/min ≈ 4.8 km/h walking speed
        return name, walk_min
    except Exception as e:
        print(f"[scorer] TfL StopPoint failed for {listing.get('id')}: {e}")
        return None, None


def compute_distance(listing: dict) -> float | None:
    """Return distance in km from home postcode. Geocodes from address/postcode if lat/lng missing."""
    config = _load_config()
    lat, lng = _resolve_coords(listing)
    if not lat or not lng:
        return None
    home = _home_coords(config)
    return round(geodesic(home, (lat, lng)).km, 3)
