#!/usr/bin/env python3
"""Fetches GymBox class schedule data and writes it to gymbox-schedule.json.

The script expects the GymBox public API to expose:
- a clubs endpoint returning a list of clubs/locations
- a classes endpoint returning class sessions for a date range and club

Environment variables allow endpoint overrides if GymBox updates their API paths.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = os.getenv("GYMBOX_API_BASE", "https://www.gymbox.com/api")
CLUBS_PATH = os.getenv("GYMBOX_CLUBS_PATH", "/clubs")
CLASSES_PATH = os.getenv("GYMBOX_CLASSES_PATH", "/classes")
LOOKAHEAD_DAYS = int(os.getenv("GYMBOX_LOOKAHEAD_DAYS", "14"))
BOOKABLE_OPEN_OFFSET_HOURS = 74
BOOKABLE_CLOSE_OFFSET_HOURS = 2
OUTPUT_PATH = Path("gymbox-schedule.json")


def _api_get(path: str, params: dict[str, Any] | None = None) -> Any:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{API_BASE.rstrip('/')}/{path.strip('/')}" + query
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "GBScheduleBot/1.0 (+https://github.com/actions)",
        },
    )

    try:
        with urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        raise RuntimeError(f"GymBox API request failed ({err.code}) for {url}") from err
    except URLError as err:
        raise RuntimeError(f"GymBox API unreachable for {url}: {err.reason}") from err


def _coerce_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "clubs", "classes", "sessions"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _find_start_time(session: dict[str, Any]) -> datetime:
    for key in ("start", "startTime", "startDate", "startAt", "datetime"):
        value = session.get(key)
        if isinstance(value, str):
            return _parse_iso(value)
    raise ValueError("Class session is missing a parseable start datetime field")


def _normalise_session(session: dict[str, Any], location_name: str, location_id: str) -> dict[str, Any]:
    start_at = _find_start_time(session)
    bookable_from = start_at - timedelta(hours=BOOKABLE_OPEN_OFFSET_HOURS)
    bookable_until = start_at - timedelta(hours=BOOKABLE_CLOSE_OFFSET_HOURS)

    return {
        **session,
        "locationId": session.get("locationId") or location_id,
        "locationName": session.get("locationName") or location_name,
        "bookableFrom": bookable_from.isoformat().replace("+00:00", "Z"),
        "bookableUntil": bookable_until.isoformat().replace("+00:00", "Z"),
        "bookingWindowHours": BOOKABLE_OPEN_OFFSET_HOURS,
    }


def main() -> None:
    fetched_at = datetime.now(timezone.utc)
    range_start = fetched_at
    range_end = fetched_at + timedelta(days=LOOKAHEAD_DAYS)

    clubs_payload = _api_get(CLUBS_PATH)
    clubs = _coerce_list(clubs_payload)
    if not clubs:
        raise RuntimeError(
            "No GymBox locations returned. Set GYMBOX_API_BASE/GYMBOX_CLUBS_PATH to the correct endpoint."
        )

    classes_by_location: dict[str, list[dict[str, Any]]] = {}
    total_classes = 0

    for club in clubs:
        club_id = str(club.get("id") or club.get("clubId") or club.get("slug") or "")
        if not club_id:
            continue

        club_name = str(club.get("name") or club.get("title") or club_id)
        classes_payload = _api_get(
            CLASSES_PATH,
            params={
                "clubId": club_id,
                "from": range_start.isoformat().replace("+00:00", "Z"),
                "to": range_end.isoformat().replace("+00:00", "Z"),
            },
        )
        raw_sessions = _coerce_list(classes_payload)
        sessions = [_normalise_session(item, club_name, club_id) for item in raw_sessions]
        classes_by_location[club_name] = sessions
        total_classes += len(sessions)

    output = {
        "fetchedAt": fetched_at.isoformat().replace("+00:00", "Z"),
        "lookaheadDays": LOOKAHEAD_DAYS,
        "bookingWindow": {
            "opensHoursBeforeClass": BOOKABLE_OPEN_OFFSET_HOURS,
            "closesHoursBeforeClass": BOOKABLE_CLOSE_OFFSET_HOURS,
        },
        "locationsCount": len(classes_by_location),
        "totalClasses": total_classes,
        "classesByLocation": classes_by_location,
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Saved schedule for {len(classes_by_location)} locations / {total_classes} classes to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
