import json
import os
import smtplib
import time
from email.message import EmailMessage

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

URL = "https://boplats.se/sok?types=1hand"
STATE_FILE = "seen_boplats_items.json"
CHECK_INTERVAL_SECONDS = 60
SEARCH_TEXT = ["högsbo", "gamlestaden", "olskroken"]

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = os.environ["SMTP_USERNAME"]
SMTP_PASSWORD = os.environ["SMTP_PASSWORD"]
EMAIL_FROM = os.environ["EMAIL_FROM"]
EMAIL_TO = os.environ["EMAIL_TO"]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}


def load_seen():
    if not os.path.exists(STATE_FILE):
        return set()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return set(json.load(f))


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)


def send_email_alert(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(SMTP_USERNAME, SMTP_PASSWORD)
        smtp.send_message(msg)


def fetch_ads():
    response = requests.get(URL, headers=HEADERS, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    ads = []

    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings)
        href = a["href"].strip()

        if not text:
            continue

        if "Publ." not in text:
            continue

        if href.startswith("/"):
            href = "https://boplats.se" + href
        elif href.startswith("http"):
            pass
        else:
            href = "https://boplats.se/" + href.lstrip("/")

        ads.append(
            {
                "id": href,
                "text": text,
                "url": href,
            }
        )

    unique_ads = {}
    for ad in ads:
        unique_ads[ad["id"]] = ad

    return list(unique_ads.values())


def find_new_matching_ads(all_ads, seen_ids):
    new_matches = []
    for search in SEARCH_TEXT:
        needle = search.casefold()

        for ad in all_ads:
            if ad["id"] in seen_ids:
                continue
            if needle in ad["text"].casefold():
                new_matches.append(ad)

    return new_matches


def main():
    seen_ids = load_seen()

    while True:
        try:
            ads = fetch_ads()

            current_ids = {ad["id"] for ad in ads}
            new_matching_ads = find_new_matching_ads(ads, seen_ids)

            for ad in new_matching_ads:
                subject = f"New Boplats match: {SEARCH_TEXT}"
                body = f"{ad['text']}\n\n{ad['url']}"
                send_email_alert(subject, body)
                print(f"Alert sent for: {ad['url']}")

            seen_ids.update(current_ids)
            save_seen(seen_ids)

        except Exception as e:
            print(f"Error: {e}")

        #
        print(
            f"Checked at {time.strftime('%Y-%m-%d %H:%M:%S')}. Sleeping for {CHECK_INTERVAL_SECONDS} seconds..."
        )

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
