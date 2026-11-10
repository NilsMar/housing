"""
Sends email alerts when a listing crosses the score threshold.
Uses the Gmail API (same OAuth token as gmail_fetcher) — no app password needed.
"""

import base64
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import yaml

GMAIL_USER = os.getenv("GMAIL_USER", "machernils0@gmail.com")


def _load_threshold() -> float:
    with open("config.yaml") as f:
        cfg = yaml.safe_load(f)
    return float(cfg.get("alert_threshold", 75))


def _build_html(listing: dict, score_breakdown: dict) -> str:
    score = listing.get("score", 0)
    price = listing.get("price")
    price_str = f"£{price:,}" if price else "Unknown"
    beds = listing.get("bedrooms", "?")
    address = listing.get("address") or listing.get("title") or "Unknown address"
    url = listing.get("url", "#")
    image_url = listing.get("image_url", "")
    tenure = listing.get("tenure", "unknown")

    rows = "".join(
        f"<tr><td style='padding:4px 8px'>{dim.replace('_', ' ').title()}</td>"
        f"<td style='padding:4px 8px; font-weight:bold'>{val}/10</td></tr>"
        for dim, val in score_breakdown.items()
    )

    img_tag = (
        f'<img src="{image_url}" style="max-width:400px;border-radius:6px;margin-bottom:12px">'
        if image_url
        else ""
    )

    return f"""
    <html><body style="font-family:sans-serif;max-width:600px;margin:0 auto">
      <h2 style="color:#2c3e50">New property match: {score}/100</h2>
      {img_tag}
      <h3>{address}</h3>
      <p><strong>{price_str}</strong> &bull; {beds} bedrooms &bull; Tenure: {tenure}</p>
      <table style="border-collapse:collapse;margin:12px 0">
        <tr style="background:#f0f0f0">
          <th style="padding:4px 8px;text-align:left">Dimension</th>
          <th style="padding:4px 8px;text-align:left">Score</th>
        </tr>
        {rows}
      </table>
      <a href="{url}" style="display:inline-block;padding:10px 20px;background:#2980b9;color:white;text-decoration:none;border-radius:4px">
        View listing
      </a>
    </body></html>
    """


def send_alert(listing: dict, score_breakdown: dict) -> None:
    from gmail_fetcher import _get_service  # reuse the same OAuth token

    threshold = _load_threshold()
    score = listing.get("score", 0)
    if score < threshold:
        return

    address = listing.get("address") or listing.get("title") or listing["id"]
    subject = f"[Housing] New match ({score}/100): {address}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER
    msg.attach(MIMEText(_build_html(listing, score_breakdown), "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = _get_service()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()

    print(f"[notifier] Alert sent for {listing['id']} (score {score})")
