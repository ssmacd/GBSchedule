# GBSchedule

Automated GymBox schedule export.

## GitHub Action

This repo includes a scheduled workflow at `.github/workflows/fetch-gymbox-schedule.yml` that:

- Runs every day at **04:00 UTC**.
- Fetches classes for all GymBox locations.
- Calculates booking window metadata for each class:
  - `bookableFrom`: **74 hours** before class start.
  - `bookableUntil`: **2 hours** before class start.
- Writes output to `gymbox-schedule.json`.
- Commits and pushes changes when schedule data has changed.

## Optional environment overrides

If GymBox changes API routes, set these in workflow/repo variables:

- `GYMBOX_API_BASE` (default: `https://ugg.api.magicline.com/connect/v2`)
- `GYMBOX_STUDIOS_PATH` (default: `/studio`)
- `GYMBOX_ACCEPT_LANGUAGE` (default: `en-GB`)
- `GYMBOX_API_BASE` (default: `https://www.gymbox.com/api`)
- `GYMBOX_CLUBS_PATH` (default: `/clubs`)
- `GYMBOX_CLASSES_PATH` (default: `/classes`)
- `GYMBOX_LOOKAHEAD_DAYS` (default: `14`)
