import json
import logging
import os
import smtplib
import tempfile
import time
from email.message import EmailMessage
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://boplats.se"
URL = os.environ.get("BOPLATS_URL", f"{BASE_URL}/sok?types=1hand")
STATE_FILE = os.environ.get("STATE_FILE", "seen_boplats_items.json")
CHECK_INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL_SECONDS", "300"))
SEARCH_TEXT = [
    term.strip()
    for term in os.environ.get("SEARCH_TEXT", "högsbo,gamlestaden,olskroken").split(",")
    if term.strip()
]

MAX_BACKOFF_SECONDS = 3600
FAILURE_ALERT_THRESHOLD = 3
SEEN_MAX_AGE_SECONDS = 60 * 24 * 60 * 60

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}

log = logging.getLogger("boplats-scraper")


def load_seen():
    """Return {listing id: epoch seconds when it was last on the page}."""
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError):
        corrupt = STATE_FILE + ".corrupt"
        os.replace(STATE_FILE, corrupt)
        log.error("State file was unreadable, moved it to %s and starting fresh", corrupt)
        return {}

    # Older versions stored a plain list of ids
    if isinstance(data, list):
        now = time.time()
        return {item: now for item in data}
    return data


def save_seen(seen):
    directory = os.path.dirname(os.path.abspath(STATE_FILE))
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
    ) as f:
        json.dump(seen, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, STATE_FILE)


def prune_seen(seen, now):
    return {
        item: last_seen
        for item, last_seen in seen.items()
        if now - last_seen < SEEN_MAX_AGE_SECONDS
    }


def send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = os.environ["EMAIL_TO"]
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USERNAME"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)


def _field(a, css_class):
    element = a.select_one(f".{css_class}")
    return " ".join(element.stripped_strings) if element else ""


def parse_ads(html):
    soup = BeautifulSoup(html, "html.parser")

    links = soup.select("a.search-result-link[href]")
    if not links:
        # Fall back to the listing text in case the class name changes
        links = [
            a
            for a in soup.find_all("a", href=True)
            if "Publ." in " ".join(a.stripped_strings)
        ]

    unique_ads = {}
    for a in links:
        text = " ".join(a.stripped_strings)
        if not text:
            continue

        href = urljoin(BASE_URL + "/", a["href"].strip())
        unique_ads[href] = {
            "id": href,
            "text": text,
            "url": href,
            "area": _field(a, "search-result-area-name"),
            "address": _field(a, "search-result-address"),
        }

    return list(unique_ads.values())


def fetch_ads():
    response = requests.get(URL, headers=HEADERS, timeout=30)
    response.raise_for_status()

    ads = parse_ads(response.text)
    if not ads:
        raise RuntimeError(f"No listings found on {URL}, the page layout may have changed")
    return ads


def find_new_matching_ads(all_ads, seen_ids):
    needles = [search.casefold() for search in SEARCH_TEXT]

    new_matches = []
    for ad in all_ads:
        if ad["id"] in seen_ids:
            continue
        text = ad["text"].casefold()
        if any(needle in text for needle in needles):
            new_matches.append(ad)

    return new_matches


def build_alert(ads):
    titles = [
        ", ".join(part for part in (ad.get("area"), ad.get("address")) if part)
        or ad["text"]
        for ad in ads
    ]

    if len(ads) == 1:
        subject = f"New Boplats match: {titles[0]}"
    else:
        areas = sorted({ad.get("area") for ad in ads if ad.get("area")})
        subject = f"{len(ads)} new Boplats matches"
        if areas:
            subject += f": {', '.join(areas)}"

    body = "\n\n".join(f"{ad['text']}\n{ad['url']}" for ad in ads)
    return subject, body


def check_once(seen):
    ads = fetch_ads()
    now = time.time()

    new_matching_ads = find_new_matching_ads(ads, seen)
    if new_matching_ads:
        # Nothing is marked as seen until the email is out, so a failed send is retried
        send_email(*build_alert(new_matching_ads))
        for ad in new_matching_ads:
            log.info("Alert sent for: %s", ad["url"])

    for ad in ads:
        seen[ad["id"]] = now
    pruned = prune_seen(seen, now)
    seen.clear()
    seen.update(pruned)
    save_seen(seen)

    return len(ads), len(new_matching_ads)


def notify_failure(failures, error):
    try:
        send_email(
            "Boplats scraper is failing",
            f"The last {failures} checks of {URL} failed.\n\nLatest error: {error!r}",
        )
    except Exception:
        log.exception("Could not send the failure alert")


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )

    missing = [
        name
        for name in ("SMTP_USERNAME", "SMTP_PASSWORD", "EMAIL_FROM", "EMAIL_TO")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f"Missing settings in .env: {', '.join(missing)}")

    seen = load_seen()
    failures = 0

    while True:
        try:
            total, new = check_once(seen)
            if failures >= FAILURE_ALERT_THRESHOLD:
                log.info("Recovered after %d failed checks", failures)
            failures = 0
            log.info("Checked %d listings, %d new matches", total, new)
        except Exception as e:
            failures += 1
            log.exception("Check failed (%d in a row)", failures)
            if failures == FAILURE_ALERT_THRESHOLD:
                notify_failure(failures, e)

        delay = min(CHECK_INTERVAL_SECONDS * 2**failures, MAX_BACKOFF_SECONDS)
        log.info("Sleeping for %d seconds", delay)
        time.sleep(delay)


if __name__ == "__main__":
    main()
