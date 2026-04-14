"""
sdcc-digest — fetch SDCC RSS feeds, deduplicate, and email a weekly digest.
"""

import hashlib
import json
import logging
import platform
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Callable

import feedparser
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langdetect import detect, LangDetectException

load_dotenv()

import os

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-5s %(message)s",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOOKBACK_DAYS = 7
SEEN_FILE = Path("seen_items.json")

# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

FEEDS: dict[str, list[tuple[str, str]]] = {
    "Official": [
        ("SDCC Unofficial Blog",       "https://sdccblog.com/feed"),
        ("Toucan – Official SDCC Blog", "https://www.comic-con.org/toucan/feed/"),
    ],
    "Comics & Pop Culture News": [
        ("Bleeding Cool – SDCC tag",      "https://bleedingcool.com/tag/sdcc/feed/"),
        ("Bleeding Cool – Conventions",   "https://bleedingcool.com/pop-culture/events/conventions/san-diego-comic-con/feed/"),
        ("ComicBook.com – SDCC tag",      "https://comicbook.com/tag/sdcc/feed/"),
    ],
}

# To add a scraper, write a function returning list[dict] with keys
# title, url, summary, published and add an entry here keyed by site label.
SCRAPER_MAP: dict[str, Callable] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_item(url: str, title: str) -> str:
    """SHA-256 of url+title, hex truncated to 16 chars."""
    return hashlib.sha256((url + title).encode()).hexdigest()[:16]


def _strip_html(text: str) -> str:
    return BeautifulSoup(text, "html.parser").get_text(separator=" ")


def _is_english(title: str, summary: str) -> bool:
    """Return True if the item appears to be English (fail open on uncertainty)."""
    text = _strip_html(title + " " + summary).strip()
    try:
        return detect(text) == "en"
    except LangDetectException:
        return True


def _fmt_date(dt: datetime) -> str:
    """Cross-platform date formatting."""
    fmt = "%#d %b %Y" if platform.system() == "Windows" else "%-d %b %Y"
    return dt.strftime(fmt)


def _parse_date(entry) -> datetime | None:
    """Return a UTC-aware datetime from a feedparser entry, or None."""
    for attr in ("published_parsed", "updated_parsed"):
        t = getattr(entry, attr, None)
        if t is not None:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def collect_feed_items(label: str, url: str) -> list[dict]:
    """Fetch a feed and return new-ish English items."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    try:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            raise ValueError(f"Feed parse error: {feed.bozo_exception}")
    except Exception as exc:
        log.warning("Feed error [%s]: %s", label, exc)
        return []

    items = []
    for entry in feed.entries:
        pub = _parse_date(entry)
        if pub is None or pub < cutoff:
            continue
        title = getattr(entry, "title", "")
        summary = getattr(entry, "summary", "")
        if not _is_english(title, summary):
            log.info("Skipping non-English item: %s", title[:60])
            continue
        items.append(
            {
                "title": title,
                "url": getattr(entry, "link", ""),
                "summary": summary,
                "published": pub,
                "label": label,
            }
        )
    return items


def collect_scraper_items(label: str, fn: Callable) -> list[dict]:
    """Run a scraper function and return filtered items."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    try:
        raw = fn()
    except Exception as exc:
        log.warning("Scraper error [%s]: %s", label, exc)
        return []

    items = []
    for entry in raw:
        pub = entry.get("published")
        if pub is None or pub < cutoff:
            continue
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        if not _is_english(title, summary):
            log.info("Skipping non-English item: %s", title[:60])
            continue
        items.append(
            {
                "title": title,
                "url": entry.get("url", ""),
                "summary": summary,
                "published": pub,
                "label": label,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def load_seen() -> dict:
    try:
        return json.loads(SEEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(seen: dict) -> None:
    SEEN_FILE.write_text(json.dumps(seen, indent=2))


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def deduplicate(items: list[dict], seen: dict) -> list[dict]:
    """Return only items whose hash is not in seen (does not mutate seen)."""
    new = []
    for item in items:
        h = _hash_item(item["url"], item["title"])
        if h not in seen:
            item["_hash"] = h
            new.append(item)
    return new


# ---------------------------------------------------------------------------
# HTML builder
# ---------------------------------------------------------------------------


def build_html(items_by_category: dict[str, list[dict]]) -> str:
    """Build an inline-CSS HTML email. Returns '' if nothing to send."""
    if not items_by_category:
        return ""

    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(days=LOOKBACK_DAYS)
    date_range = f"{_fmt_date(cutoff)} – {_fmt_date(now_utc)}"
    timestamp = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    parts = [
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f4f4f4;'
        'font-family:Arial,sans-serif;">',
        '<div style="max-width:680px;margin:0 auto;background:#ffffff;">',
        # Header
        '<div style="background:#1a1a2e;padding:24px 32px;text-align:center;">',
        '<h1 style="margin:0;color:#ffffff;font-size:26px;">&#128240; SDCC Digest</h1>',
        f'<p style="margin:8px 0 0;color:#aaaacc;font-size:14px;">{date_range}</p>',
        "</div>",
        # Body
        '<div style="padding:24px 32px;">',
    ]

    for category, items in items_by_category.items():
        parts.append(
            f'<h2 style="border-top:2px solid #eeeeee;padding-top:16px;'
            f'margin-top:24px;color:#1a1a2e;font-size:18px;">{category}</h2>'
        )
        for item in items:
            title = item["title"]
            url = item["url"]
            label = item["label"]
            pub_str = _fmt_date(item["published"])
            raw_summary = _strip_html(item.get("summary", ""))
            # One sentence: truncate at first period or 200 chars
            if len(raw_summary) > 200:
                raw_summary = raw_summary[:200].rstrip() + "…"

            parts.append(
                f'<div style="margin-bottom:20px;">'
                f'<p style="margin:0 0 4px;">'
                f'<a href="{url}" style="color:#1a1a2e;font-weight:bold;'
                f'text-decoration:none;">{title}</a></p>'
                f'<p style="margin:0 0 4px;font-size:12px;color:#888888;">'
                f'{label} &middot; {pub_str}</p>'
                f'<p style="margin:0;font-size:14px;color:#333333;">{raw_summary}</p>'
                f"</div>"
            )

    parts += [
        "</div>",
        # Footer
        f'<div style="background:#f4f4f4;padding:16px 32px;text-align:center;'
        f'font-size:12px;color:#aaaaaa;">'
        f"Generated by sdcc-digest &middot; {timestamp}"
        f"</div>",
        "</div>",
        "</body></html>",
    ]

    return "".join(parts)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def send_email(html: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SDCC Digest \u2013 {datetime.now(timezone.utc).strftime('%b %d, %Y')}"
    msg["From"] = GMAIL_USER
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, RECIPIENT_EMAIL, msg.as_string())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    seen = load_seen()

    all_items: list[dict] = []

    # Collect from RSS feeds
    for category, feeds in FEEDS.items():
        for label, url in feeds:
            items = collect_feed_items(label, url)
            for item in items:
                item["category"] = category
            all_items.extend(items)

    # Collect from scrapers
    for label, fn in SCRAPER_MAP.items():
        items = collect_scraper_items(label, fn)
        # Scrapers don't carry a category — use a default
        for item in items:
            item.setdefault("category", "Other")
        all_items.extend(items)

    # Deduplicate
    new_items = deduplicate(all_items, seen)

    # Group by category
    items_by_category: dict[str, list[dict]] = {}
    for item in new_items:
        cat = item.get("category", "Other")
        items_by_category.setdefault(cat, []).append(item)

    # Build HTML
    html = build_html(items_by_category)
    if not html:
        log.info("No new items — skipping send.")
        sys.exit(0)

    # Send
    send_email(html)

    # Persist seen hashes only after successful send
    now_iso = datetime.now(timezone.utc).isoformat()
    for item in new_items:
        seen[item["_hash"]] = now_iso
    save_seen(seen)

    n = len(new_items)
    k = len(items_by_category)
    log.info("Digest sent: %d items across %d categories.", n, k)


if __name__ == "__main__":
    main()
