import json
import time
from pathlib import Path

import pytest

import scraper

FIXTURE = Path(__file__).parent / "fixtures" / "search.html"


def ad(id_, text, area="", address=""):
    return {"id": id_, "text": text, "url": id_, "area": area, "address": address}


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "seen.json"
    monkeypatch.setattr(scraper, "STATE_FILE", str(path))
    return path


def test_parse_ads_reads_listings_from_search_page():
    ads = scraper.parse_ads(FIXTURE.read_text(encoding="utf-8"))

    assert [a["area"] for a in ads] == ["Vasastaden", "Angereds Centrum", "Landala"]
    first = ads[0]
    assert first["url"] == "https://boplats.se/objekt/1hand/6ACD067006A1F4C0B34DB095"
    assert first["address"] == "Karl Gustavsgatan 19B"
    assert "Publ." in first["text"]


def test_parse_ads_falls_back_to_publ_text_and_resolves_relative_links():
    html = '<a href="/objekt/1hand/ABC">Högsbo Publ. idag</a><a href="/om">Om oss</a>'

    ads = scraper.parse_ads(html)

    assert [a["url"] for a in ads] == ["https://boplats.se/objekt/1hand/ABC"]


def test_fetch_ads_raises_when_page_has_no_listings(monkeypatch):
    class Response:
        text = "<html><body>Nothing here</body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(scraper.requests, "get", lambda *args, **kwargs: Response())

    with pytest.raises(RuntimeError):
        scraper.fetch_ads()


def test_find_new_matching_ads(monkeypatch):
    monkeypatch.setattr(scraper, "SEARCH_TEXT", ["högsbo", "olskroken"])
    ads = [
        ad("1", "HÖGSBO Olskroken 2 rum"),
        ad("2", "Landala 1 rum"),
        ad("3", "Olskroken 3 rum"),
        ad("4", "Högsbo 1 rum"),
    ]

    matches = scraper.find_new_matching_ads(ads, {"4": 0})

    # "1" matches both terms but is only reported once
    assert [m["id"] for m in matches] == ["1", "3"]


def test_build_alert_names_the_listing_or_the_areas():
    one = [ad("u1", "text one", "Högsbo", "Gatan 1")]
    two = one + [ad("u2", "text two", "Olskroken", "Gatan 2")]

    subject, body = scraper.build_alert(one)
    assert subject == "New Boplats match: Högsbo, Gatan 1"
    assert body == "text one\nu1"

    subject, body = scraper.build_alert(two)
    assert subject == "2 new Boplats matches: Högsbo, Olskroken"
    assert "u1" in body and "u2" in body


def test_state_round_trip(state_file):
    scraper.save_seen({"a": 1.0, "b": 2.0})

    assert scraper.load_seen() == {"a": 1.0, "b": 2.0}
    assert list(state_file.parent.iterdir()) == [state_file]


def test_load_seen_migrates_old_list_format(state_file):
    state_file.write_text(json.dumps(["a", "b"]), encoding="utf-8")

    seen = scraper.load_seen()

    assert set(seen) == {"a", "b"}
    assert all(time.time() - last_seen < 5 for last_seen in seen.values())


def test_load_seen_moves_corrupt_file_aside(state_file):
    state_file.write_text('["a", ', encoding="utf-8")

    assert scraper.load_seen() == {}
    assert not state_file.exists()
    assert (state_file.parent / "seen.json.corrupt").exists()


def test_prune_seen_drops_old_entries():
    now = 1_000_000_000
    seen = {"old": now - scraper.SEEN_MAX_AGE_SECONDS - 1, "recent": now - 60}

    assert scraper.prune_seen(seen, now) == {"recent": now - 60}


def test_check_once_does_not_mark_seen_when_email_fails(state_file, monkeypatch):
    monkeypatch.setattr(scraper, "SEARCH_TEXT", ["högsbo"])
    monkeypatch.setattr(scraper, "fetch_ads", lambda: [ad("1", "Högsbo"), ad("2", "Landala")])
    sent = []

    def failing_send(subject, body):
        raise OSError("smtp down")

    monkeypatch.setattr(scraper, "send_email", failing_send)
    seen = {}
    with pytest.raises(OSError):
        scraper.check_once(seen)
    assert seen == {}

    monkeypatch.setattr(scraper, "send_email", lambda subject, body: sent.append(subject))
    assert scraper.check_once(seen) == (2, 1)
    assert scraper.check_once(seen) == (2, 0)
    assert len(sent) == 1
    assert set(scraper.load_seen()) == {"1", "2"}
