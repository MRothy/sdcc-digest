# SDCC Digest — Claude Code Project Prompt

Use this file as the full context for any Claude Code session on this project.
Run it with: `claude` from the project root, or paste it as your opening prompt.

---

## Project Overview

Build a Python project called **`sdcc-digest`** that fetches RSS feeds about
San Diego Comic-Con (SDCC), deduplicates items, language-filters to English,
builds a clean HTML email grouped by source category, and sends it via Gmail
SMTP on a GitHub Actions schedule.

---

## Project Structure to Create

```
sdcc-digest/
├── digest.py
├── seen_items.json          ← start as empty JSON object: {}
├── requirements.txt
├── .env.example             ← template, never commit real .env
├── .gitignore
├── CLAUDE.md                ← this file
└── .github/
    └── workflows/
        └── digest.yml
```

---

## requirements.txt

```
feedparser==6.0.11
requests==2.31.0
beautifulsoup4==4.12.3
langdetect==1.0.9
python-dotenv==1.0.1
```

---

## Environment Variables

| Variable            | Description                        |
|---------------------|------------------------------------|
| `GMAIL_USER`        | Sender Gmail address               |
| `GMAIL_APP_PASSWORD`| 16-char Gmail App Password         |
| `RECIPIENT_EMAIL`   | Recipient address                  |

Load locally via `python-dotenv` from a `.env` file (never committed).
In GitHub Actions, map all three explicitly in the `env:` block of the run step.

---

## digest.py — Full Specification

### Top-level constants

```python
LOOKBACK_DAYS = 7
SEEN_FILE = Path("seen_items.json")
```

### Feeds dictionary

Structured as `dict[str, list[tuple[str, str]]]` — category → list of
`(label, feed_url)`. Use **exactly** these entries:

```python
FEEDS = {
    "Official": [
        ("SDCC Unofficial Blog",        "https://sdccblog.com/feed"),
        ("Toucan – Official SDCC Blog",  "https://www.comic-con.org/toucan/feed/"),
    ],
    "Comics & Pop Culture News": [
        ("Bleeding Cool – SDCC tag",       "https://bleedingcool.com/tag/sdcc/feed/"),
        ("Bleeding Cool – Conventions",    "https://bleedingcool.com/pop-culture/events/conventions/san-diego-comic-con/feed/"),
        ("ComicBook.com – SDCC tag",       "https://comicbook.com/tag/sdcc/feed/"),
    ],
}
```

> **Note:** `www.comic-con.org/cc/` has no public RSS. Toucan
> (`comic-con.org/toucan/feed/`) is the official blog equivalent — use that.
> Verify each feed URL returns HTTP 200 with valid XML before first run.
> If a feed URL is dead on first run, log a warning and skip it — do not abort.

### SCRAPER_MAP

No scrapers are needed for this project at launch. Define an empty dict as a
placeholder so the pattern is established for future additions:

```python
SCRAPER_MAP: dict[str, Callable] = {}
```

Document in a comment: "To add a scraper, write a function returning
`list[dict]` with keys `title`, `url`, `summary`, `published` and add an entry
here keyed by site label."

### Helper: `_hash_item(url: str, title: str) -> str`

SHA-256 of `url + title`, hex digest truncated to 16 characters.

### Helper: `_is_english(title: str, summary: str) -> bool`

- Detect language from `title + " " + summary` (strip HTML tags first).
- Return `True` if detected language is `"en"`.
- Catch `LangDetectException` and return `True` (fail open — never drop items
  when detection is uncertain).
- Log skipped non-English items at `INFO` level:
  `INFO  Skipping non-English item: <title[:60]>`

### Helper: `_fmt_date(dt: datetime) -> str`

Cross-platform date formatting:
- Linux/macOS: `"%-d %b %Y"`
- Windows: `"%#d %b %Y"`
- Detect with `platform.system() == "Windows"`.

### Helper: `_parse_date(entry) -> datetime | None`

- Try `entry.published_parsed` then `entry.updated_parsed`.
- If found, return as UTC-aware `datetime`.
- If neither exists, return `None`.

### Core: `collect_feed_items(label, url) -> list[dict]`

- Fetch with `feedparser.parse(url)`.
- Log and return `[]` on any exception — one bad feed never aborts the run.
- For each entry:
  - Parse date; skip if older than `LOOKBACK_DAYS` or date is `None`.
  - Run `_is_english`; skip if False.
  - Return dicts with keys: `title`, `url`, `summary`, `published`, `label`.

### Core: `collect_scraper_items(label, fn) -> list[dict]`

- Call `fn()`; catch all exceptions, log and return `[]`.
- Apply same date and language filters as `collect_feed_items`.

### Core: `load_seen() -> dict` / `save_seen(seen: dict)`

- Load/save `seen_items.json`. If file missing or corrupt JSON, return `{}`.

### Core: `deduplicate(items, seen) -> list[dict]`

- For each item, compute `_hash_item(item["url"], item["title"])`.
- Skip if hash already in `seen`.
- Return only new items (do not mutate `seen` yet — that happens after send).

### Core: `build_html(items_by_category: dict[str, list[dict]]) -> str`

Build a single self-contained HTML string with **inline CSS only** (no
`<style>` blocks, no external sheets — safe for Gmail).

Requirements:
- White background, `font-family: Arial, sans-serif`, `max-width: 680px`,
  centered with `margin: 0 auto`.
- Header: dark background (`#1a1a2e`), white text, title "📰 SDCC Digest",
  subtitle showing the date range covered.
- One `<h2>` section per category, with a light gray top border.
- Each item: bold linked title, source label in gray, date, and a one-sentence
  summary (strip HTML, truncate to 200 chars).
- Footer: "Generated by sdcc-digest · {timestamp}".
- If `items_by_category` is empty (all items already seen), return an empty
  string — do not send.

### Core: `send_email(html: str)`

```python
msg = MIMEMultipart("alternative")
msg["Subject"] = f"SDCC Digest – {datetime.now(timezone.utc).strftime('%b %d, %Y')}"
msg["From"]    = GMAIL_USER
msg["To"]      = RECIPIENT_EMAIL
msg.attach(MIMEText(html, "html"))

with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
    server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
    server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())
```

### `main()`

1. Load `seen_items.json`.
2. Collect all items from `FEEDS` (call `collect_feed_items` for each).
3. Collect all items from `SCRAPER_MAP` (call `collect_scraper_items` for each).
4. Combine all items.
5. Deduplicate against seen.
6. Group new items by category.
7. Build HTML. If empty string returned, log "No new items — skipping send."
   and exit 0.
8. Send email.
9. **Only after a successful send:** update `seen` with new hashes and call
   `save_seen()`.
10. Log "Digest sent: {n} items across {k} categories."

---

## .github/workflows/digest.yml — Full Specification

```yaml
name: SDCC Digest

on:
  schedule:
    - cron: "0 8 * * 1"   # Every Monday 08:00 UTC
  workflow_dispatch:        # Manual trigger

env:
  FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true

jobs:
  send-digest:
    runs-on: ubuntu-latest
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: "pip"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run digest
        env:
          GMAIL_USER:         ${{ secrets.GMAIL_USER }}
          GMAIL_APP_PASSWORD: ${{ secrets.GMAIL_APP_PASSWORD }}
          RECIPIENT_EMAIL:    ${{ secrets.RECIPIENT_EMAIL }}
        run: python digest.py

      - name: Commit updated seen_items.json
        run: |
          git config user.name  "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add seen_items.json
          git diff --staged --quiet || git commit -m "chore: update seen_items [skip ci]"
          git push --rebase
```

**Important GitHub repo settings (do manually after pushing):**
- Settings → Actions → General → Workflow permissions → **Read and write**
- Settings → Secrets → Add `GMAIL_USER`, `GMAIL_APP_PASSWORD`, `RECIPIENT_EMAIL`

---

## .gitignore

```
.env
__pycache__/
*.pyc
.DS_Store
```

## .env.example

```
GMAIL_USER=michael.roth89@gmail.com
GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
RECIPIENT_EMAIL=michael.roth89@gmail.com
```

---

## Language Filtering Rules

- Add `langdetect==1.0.9` to `requirements.txt` (already included above).
- `_is_english()` detects from `title + " " + summary` (strip HTML first).
- **Fail open** on `LangDetectException` — never drop items when detection is uncertain.
- Apply the check in all item-collection paths before deduplication.
- Log skipped non-English items at `INFO`.
- All feed URLs listed above are English-language sources; no language-specific
  URL substitution is required at launch.

---

## Feed Handling Rules

- `LOOKBACK_DAYS = 7` — skip items with `published` older than 7 days.
- Log and skip per-feed errors; one bad feed never aborts the run.
- Use `_fmt_date()` for all date output (cross-platform `%-d` vs `%#d`).
- `SCRAPER_MAP` is empty at launch but must be present as a typed dict.

---

## Deduplication Rules

- Hash = SHA-256 of `url + title`, hex truncated to 16 chars.
- `seen_items.json` stores a flat dict of `{hash: ISO-timestamp-first-seen}`.
- **Only write** `seen_items.json` after a confirmed successful email send.
- If the file is missing or corrupt, start fresh with `{}`.

---

## Email Rules

- Send via `smtplib.SMTP_SSL("smtp.gmail.com", 465)` with Gmail App Password.
- Subject: `"SDCC Digest – {Mon DD, YYYY}"`.
- HTML only (no plain-text part needed).
- All CSS must be inline — no `<style>` blocks (Gmail strips them).
- Do not send if there are zero new items.

---

## Maintenance Notes (keep in CLAUDE.md for future sessions)

- **`SMTPAuthenticationError 534`** means the App Password was silently revoked
  by Google. Regenerate at https://myaccount.google.com/apppasswords and update
  the GitHub secret.
- **Verify new feed URLs** before adding: must return HTTP 200, valid
  `Content-Type: application/rss+xml` or `application/atom+xml`, and parse
  without errors via `feedparser`.
- **`git push --rebase`** is intentional — avoids race condition rejections when
  another workflow run commits `seen_items.json` at the same time.
- **`[skip ci]`** in the commit message prevents the commit-back step from
  triggering a new workflow run.
- The `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: true` env var is required at the
  top-level `env:` block to silence Node.js deprecation warnings in Actions.

---

## Verified Feed URLs (researched April 2026)

| Label | URL | Notes |
|---|---|---|
| SDCC Unofficial Blog | `https://sdccblog.com/feed` | WordPress, confirmed active |
| Toucan – Official Blog | `https://www.comic-con.org/toucan/feed/` | WordPress; verify on first run |
| Bleeding Cool – SDCC tag | `https://bleedingcool.com/tag/sdcc/feed/` | High volume during con season |
| Bleeding Cool – Conventions | `https://bleedingcool.com/pop-culture/events/conventions/san-diego-comic-con/feed/` | Narrower scope, SDCC-specific |
| ComicBook.com – SDCC tag | `https://comicbook.com/tag/sdcc/feed/` | SDCC-filtered, not full site feed |

> **Note:** `www.comic-con.org/cc/` has no RSS feed. The Toucan blog is the
> official equivalent. Comic-Con International's main site posts news via
> Toucan, Facebook, Instagram, and X only.

> All five feeds are SDCC-specific. Between cons, post volume will be low —
> that's expected. During SDCC week (late July), expect a significant spike.

---

## What Claude Code Should Do

1. Create all files listed in **Project Structure** above.
2. Implement `digest.py` exactly per the spec — do not skip any helper or any
   section of `main()`.
3. Create `requirements.txt`, `.gitignore`, `.env.example`, and
   `.github/workflows/digest.yml` exactly as specified.
4. Initialize `seen_items.json` as `{}`.
5. After scaffolding, run `pip install -r requirements.txt` to verify the
   environment installs cleanly.
6. Do **not** run `digest.py` itself (it requires live secrets).
7. Print a final checklist of manual steps the user must complete:
   - Copy `.env.example` → `.env` and fill in real values
   - Push repo to GitHub
   - Set GitHub Actions permissions to Read and Write
   - Add the three secrets in GitHub Settings → Secrets
   - Verify each feed URL returns valid RSS (quick `feedparser.parse(url)` test)
   - Optionally: run `python digest.py` locally with `.env` populated to test
     end-to-end before the first scheduled run
