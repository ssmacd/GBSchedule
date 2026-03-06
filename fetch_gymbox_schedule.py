#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

API_BASE = os.getenv("GYMBOX_API_BASE", "https://ugg.api.magicline.com/connect/v2")
STUDIOS_PATH = os.getenv("GYMBOX_STUDIOS_PATH", "/studio")
LOOKAHEAD_DAYS = int(os.getenv("GYMBOX_LOOKAHEAD_DAYS", "14"))
BOOKABLE_OPEN_OFFSET_HOURS = 74
BOOKABLE_CLOSE_OFFSET_HOURS = 2
OUTPUT_PATH = Path("gymbox-schedule.json")
LANGUAGE = os.getenv("GYMBOX_ACCEPT_LANGUAGE", "en-GB")


def _api_get(path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    query = f"?{urlencode(params)}" if params else ""
    url = f"{API_BASE.rstrip('/')}/{path.strip('/')}" + query
    req = Request(
        url,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Language": LANGUAGE,
            "User-Agent": "GBScheduleBot/1.0 (+https://github.com/actions)",
        },
    )

    try:
        with urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as err:
        return err.code, None
    except URLError as err:
        raise RuntimeError(f"GymBox API unreachable for {url}: {err.reason}") from err


def _coerce_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("data", "results", "items", "studios", "classes", "sessions", "appointments", "courses"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _find_start_time(session: dict[str, Any]) -> datetime:
    for key in ("start", "startTime", "startDate", "startAt", "date", "dateTime", "begin"):
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


def _fetch_sessions_for_studio(studio_id: str, start_iso: str, end_iso: str) -> list[dict[str, Any]]:
    # Gymbox uses Magicline Connect. Different tenants expose slightly different route names;
    # try the known schedule endpoints used by this API family.
    candidate_requests = [
        (f"/studio/{studio_id}/class", {"from": start_iso, "to": end_iso}),
        (f"/studio/{studio_id}/classes", {"from": start_iso, "to": end_iso}),
        (f"/studio/{studio_id}/course", {"from": start_iso, "to": end_iso}),
        (f"/studio/{studio_id}/courses", {"from": start_iso, "to": end_iso}),
        (f"/studio/{studio_id}/appointments", {"startDate": start_iso, "endDate": end_iso}),
        (f"/studio/{studio_id}/appointment", {"startDate": start_iso, "endDate": end_iso}),
        (f"/studio/{studio_id}/calendar", {"from": start_iso, "to": end_iso}),
    ]

    for path, params in candidate_requests:
        status, payload = _api_get(path, params=params)
        if status == 200:
            sessions = _coerce_list(payload)
            if sessions:
                return sessions
    return []


def main() -> None:
    fetched_at = datetime.now(timezone.utc)
    range_start = fetched_at
    range_end = fetched_at + timedelta(days=LOOKAHEAD_DAYS)
    start_iso = range_start.isoformat().replace("+00:00", "Z")
    end_iso = range_end.isoformat().replace("+00:00", "Z")

    status, studios_payload = _api_get(STUDIOS_PATH)
    if status != 200:
        raise RuntimeError(f"Failed to fetch studios from {API_BASE}{STUDIOS_PATH} (status {status})")

    studios = [s for s in _coerce_list(studios_payload) if "gymbox" in str(s.get("studioName", "")).lower()]
    if not studios:
        raise RuntimeError("No Gymbox studios returned from Magicline Connect endpoint.")

    classes_by_location: dict[str, list[dict[str, Any]]] = {}
    total_classes = 0

    for studio in studios:
        studio_id = str(studio.get("id") or "")
        if not studio_id:
            continue

        studio_name = str(studio.get("studioName") or studio.get("name") or studio_id)
        raw_sessions = _fetch_sessions_for_studio(studio_id, start_iso, end_iso)
        sessions = [_normalise_session(item, studio_name, studio_id) for item in raw_sessions]
        classes_by_location[studio_name] = sessions
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
