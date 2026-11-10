"""
Reads Gmail for property alert emails from Rightmove, Zoopla, and OnTheMarket.
Parses HTML email bodies to extract listing cards (URL, price, beds, address, image).
Returns partial listing dicts ready for db.upsert_listing().
"""

import base64
import os
import re
from email import message_from_bytes
from html.parser import HTMLParser

from bs4 import BeautifulSoup
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]

ALERT_SENDERS = {
    "rightmove": "rightmove@rightmove.co.uk",
    "zoopla": "alerts@zoopla.co.uk",
    "onthemarket": "noreply@onthemarket.com",
}

URL_PATTERNS = {
    "rightmove": re.compile(r"https?://(?:www\.)?rightmove\.co\.uk/properties/(\d+)"),
    "zoopla": re.compile(r"https?://(?:www\.)?zoopla\.co\.uk/for-sale/details/(\d+)"),
    "onthemarket": re.compile(r"https?://(?:www\.)?onthemarket\.com/details/(\d+)"),
}

PRICE_RE = re.compile(r"£([\d,]+)")
BEDS_RE = re.compile(r"(\d+)\s*bed", re.IGNORECASE)


def _get_credentials() -> Credentials:
    token_path = os.getenv("GMAIL_TOKEN_PATH", "credentials/token.json")
    creds_path = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials/credentials.json")

    creds = None

    # Railway / hosted: token stored as env var JSON string
    token_json = os.getenv("GMAIL_TOKEN_JSON")
    if token_json:
        creds = Credentials.from_authorized_user_info(
            __import__("json").loads(token_json), SCOPES
        )
    elif os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Persist refreshed token back to file if running locally
            if not token_json and token_path:
                os.makedirs(os.path.dirname(token_path), exist_ok=True)
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
            os.makedirs(os.path.dirname(token_path), exist_ok=True)
            with open(token_path, "w") as f:
                f.write(creds.to_json())

    return creds


def _get_service():
    return build("gmail", "v1", credentials=_get_credentials())


def _decode_body(payload: dict) -> str:
    """Recursively extract HTML body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    if mime_type.startswith("multipart/"):
        for part in payload.get("parts", []):
            html = _decode_body(part)
            if html:
                return html

    return ""


def _parse_price(text: str) -> int | None:
    m = PRICE_RE.search(text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


def _parse_beds(text: str) -> int | None:
    m = BEDS_RE.search(text)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


def _extract_cards_from_html(html: str, platform: str) -> list[dict]:
    """Extract listing cards from alert email HTML."""
    soup = BeautifulSoup(html, "lxml")
    pattern = URL_PATTERNS[platform]
    cards = []
    seen_ids = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        m = pattern.search(href)
        if not m:
            continue

        listing_id = m.group(1)
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        # Normalise URL (strip tracking params)
        clean_url = pattern.search(href).group(0)

        # Walk up the DOM to find the containing card element
        container = a_tag
        for _ in range(6):
            parent = container.parent
            if parent is None:
                break
            container = parent

        card_text = container.get_text(" ", strip=True)
        price = _parse_price(card_text)
        beds = _parse_beds(card_text)

        # Address: often the visible text of the link itself, or a nearby element
        link_text = a_tag.get_text(" ", strip=True)
        address = link_text if len(link_text) > 5 else None

        # Thumbnail image
        img = container.find("img")
        image_url = img["src"] if img and img.get("src") else None

        cards.append(
            {
                "id": f"{platform}:{listing_id}",
                "url": clean_url,
                "platform": platform,
                "price": price,
                "bedrooms": beds,
                "address": address,
                "image_url": image_url,
                "enriched": 0,
            }
        )

    return cards


def fetch_new_listings(since: str | None = None) -> list[dict]:
    """
    Poll Gmail for new property alert emails since `since` (RFC3339 timestamp).
    Returns a list of partial listing dicts.
    """
    service = _get_service()

    sender_query = " OR ".join(f"from:{s}" for s in ALERT_SENDERS.values())
    query = f"({sender_query})"
    if since:
        # Gmail uses epoch seconds for after: filter
        import datetime
        dt = datetime.datetime.fromisoformat(since.replace("Z", "+00:00"))
        epoch = int(dt.timestamp())
        query += f" after:{epoch}"

    result = service.users().messages().list(userId="me", q=query, maxResults=100).execute()
    messages = result.get("messages", [])

    all_cards: list[dict] = []

    for msg_ref in messages:
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()

        # Determine platform from sender
        headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}
        sender = headers.get("from", "").lower()
        platform = None
        for p, addr in ALERT_SENDERS.items():
            if addr in sender:
                platform = p
                break
        if not platform:
            continue

        html_body = _decode_body(msg["payload"])
        if not html_body:
            continue

        cards = _extract_cards_from_html(html_body, platform)
        all_cards.extend(cards)

    # Deduplicate by id across all emails
    seen = set()
    unique = []
    for card in all_cards:
        if card["id"] not in seen:
            seen.add(card["id"])
            unique.append(card)

    return unique
