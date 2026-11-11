"""
Pipeline orchestrator. Run this once to execute a full cycle:
  1. Fetch new listing URLs from Gmail
  2. Store partial records
  3. Scrape listing pages for full data
  4. Score each listing
  5. Send alerts for high-scoring new matches

Usage:
  python main.py            # normal run
  python main.py --dry-run  # fetch + scrape without writing to DB or sending alerts
"""

import argparse
import json
import sys
import traceback

from dotenv import load_dotenv
load_dotenv()

import db
import gmail_fetcher
import notifier
import scraper
import scorer


def run(dry_run: bool = False) -> None:
    db.init_db()
    errors: list[str] = []
    new_count = 0

    # --- Step 1: Fetch new listings from Gmail ---
    last_run = db.get_last_run_time()
    print(f"[main] Fetching Gmail alerts since {last_run or 'beginning'} ...")

    try:
        cards = gmail_fetcher.fetch_new_listings(since=last_run)
    except Exception as e:
        print(f"[main] Gmail fetch failed: {e}")
        errors.append(f"gmail_fetch: {e}")
        cards = []

    print(f"[main] Found {len(cards)} listing(s) in emails")

    # --- Step 2: Store partial records, skip already-known listings ---
    new_cards = []
    for card in cards:
        if dry_run:
            print(f"  [dry-run] Would insert: {card['id']} — {card.get('address')} £{card.get('price')}")
            new_cards.append(card)
            continue

        is_new = db.upsert_listing(card)
        if is_new:
            new_cards.append(card)
            new_count += 1

    # --- Step 3: Enrich all unenriched records ---
    unenriched = new_cards if dry_run else db.get_unenriched()
    print(f"[main] Enriching {len(unenriched)} listing(s) ...")

    for listing in unenriched:
        try:
            enriched = scraper.enrich(listing)
        except Exception as e:
            print(f"  [scraper] Failed {listing['id']}: {e} — scoring from email data only")
            errors.append(f"scrape:{listing['id']}: {e}")
            enriched = {**listing, "enriched": 1}  # mark done so we don't retry every run

        # Compute distance to home postcode
        dist = scorer.compute_distance(enriched)
        if dist is not None:
            enriched["dist_km"] = dist

        # TfL commute time (kept for reference)
        commute = scorer.compute_commute_time(enriched)
        if commute is not None:
            enriched["commute_m"] = commute

        # Walk time to nearest tube/overground station (used for commute score)
        station_name, walk_min = scorer.compute_nearest_station(enriched)
        if station_name is not None:
            enriched["nearest_station"] = station_name
        if walk_min is not None:
            enriched["walk_to_station_m"] = walk_min

        # Compute score
        total, breakdown = scorer.score_listing(enriched)
        enriched["score"] = total
        enriched["score_json"] = json.dumps(breakdown)

        if dry_run:
            print(
                f"  [dry-run] {listing['id']}: score={total} "
                f"commute={commute}min dist={dist}km "
                f"tenure={enriched.get('tenure')} garden={enriched.get('has_garden')}"
            )
            continue

        db.upsert_listing(enriched)

        # --- Step 4: Alert if above threshold and not yet alerted ---
        if not listing.get("alerted"):
            try:
                notifier.send_alert(enriched, breakdown)
                db.mark_alerted(listing["id"])
            except Exception as e:
                print(f"  [notifier] Failed {listing['id']}: {e}")
                errors.append(f"notify:{listing['id']}: {e}")

    if not dry_run:
        db.log_run(new_count, errors)

    print(f"[main] Done. New listings: {new_count}. Errors: {len(errors)}.")
    if errors:
        for err in errors:
            print(f"  ERROR: {err}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
