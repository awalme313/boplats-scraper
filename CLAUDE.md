# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal single-file Python script (`scraper.py`) that polls the first-hand rental listings on boplats.se and emails the owner when a new listing mentions one of the configured areas. It is run long-lived, normally under the systemd user unit `boplats-scraper.service`.

## Commands

The project uses `uv` (Python 3.13).

```sh
uv run scraper.py                                   # run the polling loop (sends real emails)
uv run pytest                                       # all tests
uv run pytest tests/test_scraper.py::test_find_new_matching_ads   # one test
systemctl --user restart boplats-scraper            # pick up code or .env changes when run as a service
journalctl --user -u boplats-scraper -f             # service logs
```

There is no linter or formatter configured.

Running `scraper.py` hits the live site and sends email through the SMTP account in `.env`. To try parsing or matching without sending anything, call the functions directly, e.g. `uv run python -c "import scraper; print(scraper.fetch_ads()[:2])"`.

## Architecture

`main()` loops forever: `check_once(seen)` then sleep. One check is `fetch_ads()` → `find_new_matching_ads()` → `build_alert()` + `send_email()` → update and save state.

Behaviours that are deliberate and easy to break:

- **Seen-marking order**: `check_once` only records listings as seen after the alert email has been sent. If `send_email` raises, nothing is saved and the same matches are retried on the next check. Don't move the state update above the send.
- **One digest per check**: all new matches go in a single email, and a listing matching several search terms appears once.
- **Empty page is an error**: `fetch_ads` raises if no listings are parsed, so a site redesign shows up as a failure instead of silently finding nothing. After `FAILURE_ALERT_THRESHOLD` consecutive failed checks, `notify_failure` emails the owner once. The sleep doubles per consecutive failure up to `MAX_BACKOFF_SECONDS`.
- **State file** (`seen_boplats_items.json`, gitignored): a JSON object mapping listing URL → epoch seconds when the listing was last on the page. Every listing on the page is refreshed each check, matching or not, and entries not seen for `SEEN_MAX_AGE_SECONDS` (60 days) are pruned. `load_seen` also accepts the legacy format (a plain list of URLs) and moves an unparseable file aside to `.corrupt`. `save_seen` writes via temp file + `os.replace`.
- **Parsing**: `parse_ads` selects `a.search-result-link` and reads area and address from `.search-result-area-name` / `.search-result-address`; if no such links exist it falls back to any link whose text contains `"Publ."`. Matching is a case-insensitive substring test against the listing's full text, not just the area. The search page returns all listings in one response (no pagination).

## Configuration

Module-level settings are read from the environment (via `.env`, see `.env.example`) at import time: `SEARCH_TEXT` (comma-separated), `CHECK_INTERVAL_SECONDS`, `BOPLATS_URL`, `STATE_FILE`. SMTP credentials (`SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`) are read when an email is sent and validated at the start of `main()`, so the module can be imported in tests without them. `STATE_FILE` is a relative path, so the working directory matters; the service unit sets it to the project folder.

## Tests

`tests/test_scraper.py` monkeypatches module globals (`scraper.STATE_FILE`, `scraper.SEARCH_TEXT`, `scraper.fetch_ads`, `scraper.send_email`), so code under test must look these up as module attributes at call time. `tests/fixtures/search.html` is a trimmed copy of the real search page with three listings; refresh it from the live page if the site's markup changes.
