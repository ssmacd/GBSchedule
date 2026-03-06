#!/usr/bin/env python3
"""Fetch the full GymBox class schedule across all locations and save as JSON.

Uses the public Magicline / UGG APIs (no authentication required):
  - Studios: https://ugg.api.magicline.com/connect/v2/studio?tag=...
  - Schedule: https://prod.ugg.globaldelivery.nl/api/frontend/class_schedule
"""

import json
import math
import sys
from datetime import datetime, timedelta, timezone

import requests

STUDIOS_URL = "https://ugg.api.magicline.com/connect/v2/studio"
STUDIOS_TAG = "BRANDEDAPPGBDONOTDELETE-0001"

SCHEDULE_URL = "https://prod.ugg.globaldelivery.nl/api/frontend/class_schedule"
SCHEDULE_MAX_DAYS = 3  # API enforces a max 3-day window per request

BOOKING_WINDOW_HOURS = 74  # Classes bookable from 74h before start (3 days + 2 hours)

OUTPUT_FILE = "gymbox-schedule.json"


def get_gymbox_studios() -> list[dict]:
    """Fetch all GymBox studios, filtering out HQ/non-gym entries."""
    resp = requests.get(STUDIOS_URL, params={"tag": STUDIOS_TAG})
    resp.raise_for_status()
    studios = resp.json()

    # Keep only actual gym locations (exclude HQ which has no classes)
    gym_studios = [
        s for s in studios
        if "gymbox" in s.get("studioName", "").lower()
        and "hq" not in s.get("studioName", "").lower()
    ]

    print(f"Found {len(gym_studios)} GymBox studios (of {len(studios)} total)")
    return gym_studios


def fetch_schedule(venue_id: int, start_date: str, end_date: str) -> list[dict]:
    """Fetch class schedule for a single venue and date range."""
    resp = requests.get(SCHEDULE_URL, params={
        "venue_id": venue_id,
        "start_date": start_date,
        "end_date": end_date,
    })
    resp.raise_for_status()
    return resp.json().get("class_schedules", [])


def fetch_full_schedule(venue_id: int, start_date: datetime, end_date: datetime) -> list[dict]:
    """Fetch schedule across a date range, chunked into 3-day windows."""
    all_classes = []
    current = start_date
    while current <= end_date:
        chunk_end = min(current + timedelta(days=SCHEDULE_MAX_DAYS - 1), end_date)
        classes = fetch_schedule(
            venue_id,
            current.strftime("%Y-%m-%d"),
            chunk_end.strftime("%Y-%m-%d"),
        )
        all_classes.extend(classes)
        current = chunk_end + timedelta(days=1)
    return all_classes


def build_schedule_entry(class_info: dict, slot: dict) -> dict:
    """Build a single schedule entry from a class + slot pair."""
    booked = slot.get("bookedParticipants", 0)
    capacity = slot.get("maxParticipants", 0)
    waiting = slot.get("waitingListParticipants", 0)
    max_waiting = slot.get("maxWaitingListParticipants", 0)

    if capacity > 0 and booked >= capacity:
        if max_waiting > 0 and waiting < max_waiting:
            availability = "waitlist"
        else:
            availability = "full"
    else:
        availability = "available"

    instructors = [i.get("publicName") or f"{i.get('firstName', '')} {i.get('lastName', '')}".strip()
                   for i in slot.get("instructors", [])]

    return {
        "slotId": slot.get("id"),
        "classId": class_info.get("id"),
        "className": class_info.get("title"),
        "category": class_info.get("category"),
        "description": class_info.get("description"),
        "duration": class_info.get("duration"),
        "startDateTime": slot.get("startDateTime"),
        "endDateTime": slot.get("endDateTime"),
        "location": slot.get("location", {}).get("name"),
        "locationId": slot.get("location", {}).get("id"),
        "instructors": instructors,
        "capacity": capacity,
        "booked": booked,
        "spotsLeft": max(0, capacity - booked),
        "waitingList": waiting,
        "maxWaitingList": max_waiting,
        "availability": availability,
        "bookableFrom": slot.get("earliestBookingDateTime"),
        "bookableUntil": slot.get("latestBookingDateTime"),
    }


def main() -> None:
    now = datetime.now(timezone.utc)
    start_date = now
    # Fetch 7 days ahead (the practical booking window)
    end_date = now + timedelta(days=6)

    studios = get_gymbox_studios()

    all_entries = []
    for studio in studios:
        studio_id = studio["id"]
        studio_name = studio["studioName"]
        print(f"  Fetching {studio_name} (id={studio_id})...")

        try:
            class_schedules = fetch_full_schedule(studio_id, start_date, end_date)
            count = 0
            for cs in class_schedules:
                class_info = cs.get("class", {})
                for slot in cs.get("slots", []):
                    all_entries.append(build_schedule_entry(class_info, slot))
                    count += 1
            print(f"    -> {count} class slots")
        except Exception as e:
            print(f"    -> Failed: {e}", file=sys.stderr)

    # Group by date
    schedule_by_date: dict[str, list[dict]] = {}
    for entry in all_entries:
        date = entry["startDateTime"][:10] if entry.get("startDateTime") else "unknown"
        schedule_by_date.setdefault(date, []).append(entry)

    # Sort each day by location then time
    for date in schedule_by_date:
        schedule_by_date[date].sort(key=lambda c: (c.get("location", ""), c.get("startDateTime", "")))

    sorted_schedule = dict(sorted(schedule_by_date.items()))

    output = {
        "fetchedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bookingWindowHours": BOOKING_WINDOW_HOURS,
        "dateRange": {
            "start": start_date.strftime("%Y-%m-%d"),
            "end": end_date.strftime("%Y-%m-%d"),
        },
        "studios": [{"id": s["id"], "name": s["studioName"]} for s in studios],
        "schedule": sorted_schedule,
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    total = sum(len(v) for v in sorted_schedule.values())
    full = sum(1 for v in sorted_schedule.values() for c in v if c["availability"] == "full")
    print(f"\nSaved {OUTPUT_FILE}: {total} class slots across {len(studios)} studios, {len(sorted_schedule)} days")
    print(f"  {full} fully booked, {total - full} with availability")


if __name__ == "__main__":
    main()
