This is just a small project for checking for new apartments on the website boplats.se. Only useful for me as of now would probably change it for public use, but then i would need to check for security changes in the code.

## Setup

```sh
cp .env.example .env   # then fill in the SMTP settings
uv run scraper.py
```

The script checks the first-hand listings every 5 minutes and sends one email with all new listings that mention any of the search terms. Search terms and the interval can be changed in `.env`, see `.env.example`.

Listings that have been seen are stored in `seen_boplats_items.json`. If the checks fail three times in a row (for example because the page layout changed and no listings are found) an email is sent about that too.

## Running as a service

```sh
cp boplats-scraper.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now boplats-scraper
journalctl --user -u boplats-scraper -f   # logs
```

The unit file assumes the project is in `~/projects/personal_projects/boplats-scraper` and that `uv` is in `~/.local/bin`.

## Tests

```sh
uv run pytest
```
