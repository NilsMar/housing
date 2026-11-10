"""
Sends email alerts when a listing crosses the score threshold.
Uses Gmail SMTP with an app password (set GMAIL_APP_PASSWORD env var).
"""

import os
import smtplib
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
    app_password = os.getenv("GMAIL_APP_PASSWORD")
    if not app_password:
        print(f"[notifier] GMAIL_APP_PASSWORD not set — skipping alert for {listing['id']}")
        return

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

    html_body = _build_html(listing, score_breakdown)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, app_password)
        server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

    print(f"[notifier] Alert sent for {listing['id']} (score {score})")
