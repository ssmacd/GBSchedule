#!/usr/bin/env python3
"""Fetch the full GymBox class schedule across all locations and save as JSON."""

import json
import os
import re
import sys
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://gymbox.legendonlineservices.co.uk/enterprise"
LOGIN_URL = f"{BASE_URL}/account/login"
TIMETABLE_URL = f"{BASE_URL}/BookingsCentre/MemberTimetable"
CLUBS_URL = f"{BASE_URL}/mobile/getfacilities"

BOOKING_WINDOW_HOURS = 74  # 3 days (72h) + 2 hours before class

SESSION = requests.Session()
SESSION.headers.update({
    "Accept-Language": "en-GB,en;q=0.9",
    "DNT": "1",
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
})


def login(email: str, password: str) -> None:
    """Authenticate with GymBox using a two-step login (cookie init + credentials)."""
    # Step 1: initialise session cookies
    SESSION.get(LOGIN_URL)

    # Step 2: submit credentials
    resp = SESSION.post(
        LOGIN_URL,
        data={"login.Email": email, "login.Password": password},
        allow_redirects=False,
    )

    if resp.status_code not in (200, 302) or (
        resp.status_code == 200 and "Login failed" in resp.text
    ):
        print("Login failed", file=sys.stderr)
        sys.exit(1)

    # Follow redirect if needed
    if resp.status_code == 302:
        SESSION.get(resp.headers.get("Location", BASE_URL))

    print("Login successful")


def get_all_clubs() -> list[dict]:
    """Return a list of all GymBox clubs with their Id and Name."""
    resp = SESSION.get(CLUBS_URL)
    resp.raise_for_status()
    clubs = resp.json()
    print(f"Found {len(clubs)} clubs")
    return clubs


def parse_timetable(html: str, club_name: str) -> dict[str, list[dict]]:
    """Parse the HTML timetable for a single club into a date-keyed dict."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.select_one("#MemberTimetable")
    if not table:
        print(f"  No timetable found for {club_name}")
        return {}

    schedule: dict[str, list[dict]] = {}
    current_date = None

    for row in table.find_all("tr"):
        # Date header rows contain an h5
        header = row.find("h5")
        if header:
            raw = header.get_text(strip=True)
            # Format: "DayName - DD MonthName YYYY"
            match = re.search(r"\d{1,2}\s+\w+\s+\d{4}", raw)
            if match:
                try:
                    parsed = datetime.strptime(match.group(), "%d %B %Y")
                    current_date = parsed.strftime("%Y-%m-%d")
                    schedule.setdefault(current_date, [])
                except ValueError:
                    current_date = None
            continue

        if current_date is None:
            continue

        cols = row.find_all("td")
        if not cols:
            continue

        def col_text(idx: int) -> str:
            return cols[idx].get_text(strip=True) if idx < len(cols) else ""

        time_str = col_text(0)
        class_name = col_text(1)
        if not time_str or not class_name:
            continue

        # Extract slot id
        slot_id = None
        col6 = cols[5] if len(cols) > 5 else None
        if col6:
            el = col6.find(id=True)
            if el and el.get("id", "").startswith("slot"):
                slot_id = el["id"].replace("slot", "")
        if not slot_id:
            col5 = cols[4] if len(cols) > 4 else None
            if col5:
                link = col5.find("a", id=True)
                if link and link.get("id", "").startswith("price"):
                    slot_id = link["id"].replace("price", "")

        # Determine booking status from last column text
        action = col_text(6) if len(cols) > 6 else col_text(5) if len(cols) > 5 else ""

        # Calculate when the class becomes bookable
        class_datetime_str = f"{current_date} {time_str}"
        bookable_from = None
        try:
            class_dt = datetime.strptime(class_datetime_str, "%Y-%m-%d %H:%M")
            bookable_dt = class_dt - timedelta(hours=BOOKING_WINDOW_HOURS)
            bookable_from = bookable_dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            pass

        schedule[current_date].append({
            "id": slot_id,
            "className": class_name,
            "time": time_str,
            "category": col_text(2),
            "instructor": col_text(3),
            "duration": col_text(4),
            "location": club_name,
            "status": action,
            "classDatetime": f"{current_date}T{time_str}:00" if time_str else None,
            "bookableFrom": bookable_from,
        })

    return schedule


def fetch_timetable_for_club(club_id: int, club_name: str) -> dict[str, list[dict]]:
    """Fetch and parse the timetable for a single club."""
    resp = SESSION.get(TIMETABLE_URL, params={"clubId": club_id})
    resp.raise_for_status()
    return parse_timetable(resp.text, club_name)


def combine_timetables(timetables: list[dict[str, list[dict]]]) -> dict[str, list[dict]]:
    """Merge multiple club timetables into one date-keyed dict."""
    combined: dict[str, list[dict]] = {}
    for tt in timetables:
        for date, classes in tt.items():
            combined.setdefault(date, []).extend(classes)
    return combined


def main() -> None:
    email = os.environ.get("GYMBOX_EMAIL", "")
    password = os.environ.get("GYMBOX_PASSWORD", "")

    if not email or not password:
        print("GYMBOX_EMAIL and GYMBOX_PASSWORD environment variables are required", file=sys.stderr)
        sys.exit(1)

    login(email, password)
    clubs = get_all_clubs()

    timetables = []
    for club in clubs:
        club_id = club.get("Id")
        club_name = club.get("Name", f"Club {club_id}")
        print(f"  Fetching timetable for {club_name} (id={club_id})...")
        try:
            tt = fetch_timetable_for_club(club_id, club_name)
            timetables.append(tt)
            class_count = sum(len(v) for v in tt.values())
            print(f"    -> {class_count} classes found")
        except Exception as e:
            print(f"    -> Failed: {e}", file=sys.stderr)

    schedule = combine_timetables(timetables)

    # Sort dates and classes within each date by time
    sorted_schedule = {}
    for date in sorted(schedule.keys()):
        sorted_schedule[date] = sorted(schedule[date], key=lambda c: (c["location"], c["time"]))

    output = {
        "fetchedAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bookingWindowHours": BOOKING_WINDOW_HOURS,
        "clubCount": len(clubs),
        "clubs": [{"id": c.get("Id"), "name": c.get("Name")} for c in clubs],
        "schedule": sorted_schedule,
    }

    with open("gymbox-schedule.json", "w") as f:
        json.dump(output, f, indent=2)

    total_classes = sum(len(v) for v in sorted_schedule.values())
    print(f"\nSchedule saved: {len(sorted_schedule)} days, {total_classes} classes across {len(clubs)} clubs")


if __name__ == "__main__":
    main()
