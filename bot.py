import os
import re
import threading
import time
import traceback
import html
import feedparser
import telebot
import requests
from flask import Flask
from supabase import create_client

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- SUPABASE SETUP ---
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)


@app.route("/")
def home():
    return "Bot is live!"


# --- DATABASE FUNCTIONS ---
def get_clean_link(link):
    """Removes tracking junk (like ?igshid=) and trailing slashes."""
    return link.split("?")[0].rstrip("/")


def is_link_sent(link):
    clean_link = get_clean_link(link)
    response = supabase.table("posted_links").select("link").eq("link", clean_link).execute()
    return len(response.data) > 0


def mark_as_sent(link):
    clean_link = get_clean_link(link)
    try:
        supabase.table("posted_links").insert({"link": clean_link}).execute()
    except Exception as e:
        print(f"Database insertion error (likely duplicate): {e}")


# --- CAPTION HELPERS ---
def strip_html(text):
    """Remove HTML tags and decode entities."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def remove_bio_phrases(text):
    """
    Strip Instagram-style endings like:
    - Link in bio for more.
    - Link in the comments for more info.
    - Read more in bio.
    - Full review in bio.
    """
    if not text:
        return text

    patterns = [
        # "Link in bio for more / for our full review / for more info."
        r"(?i)\s*link\s+in\s+(?:the\s+)?(?:bio|comments?)\b[^.]*\.?\s*$",
        # "Read more in bio."
        r"(?i)\s*read\s+more\s+in\s+(?:bio|comments?)\b[^.]*\.?\s*$",
        # "Full review / article / story in bio."
        r"(?i)\s*full\s+(?:review|article|story)\s+in\s+(?:bio|comments?)\b[^.]*\.?\s*$",
        # "More info / details in bio."
        r"(?i)\s*more\s+(?:info|details?|here)\s+in\s+(?:bio|comments?)\b[^.]*\.?\s*$",
        # Trailing "Link in bio" without extra words
        r"(?i)\s*link\s+in\s+bio\.?\s*$",
        # Trailing ellipsis often left after truncation before "Link in..."
        r"\s*\.{2,}\s*$",
    ]

    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned)

    return cleaned.strip(" \t\n\r.-–—")


def extract_caption(entry):
    """
    Prefer the longer plain-text body from description/summary;
    fall back to title. Then remove bio-link phrases.
    """
    candidates = []

    # description / summary often has the fuller caption (after the img tag)
    for key in ("summary", "description"):
        raw = entry.get(key)
        if raw:
            plain = strip_html(raw)
            # Drop pure image-alt leftovers that are just the truncated title
            if plain:
                candidates.append(plain)

    title = (entry.get("title") or "").strip()
    if title:
        candidates.append(title)

    # Pick the longest non-empty candidate
    caption = max(candidates, key=len) if candidates else ""
    caption = remove_bio_phrases(caption)

    # If still empty, use a generic fallback
    if not caption:
        caption = "New post"

    return caption


# --- RSS FUNCTIONS ---
def fetch_feed():
    rss_url = (
        "https://rss-bridge.org/bridge01/?action=display"
        "&bridge=InstagramBridge&context=Username&u=igndotcom"
        "&media_type=picture&format=Mrss"
    )
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(rss_url, headers=headers, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        print(f"[RSS] Fetched {len(feed.entries)} entries")
        return feed.entries
    except Exception as e:
        print(f"[RSS] Error fetching feed: {e}")
        return []


def send_post(entry):
    image_url = entry.media_content[0]["url"] if "media_content" in entry else None
    caption_body = extract_caption(entry)
    # Caption + link; Telegram photo caption limit is 1024 chars
    caption = f"🎬 {caption_body}\n\n🔗 {entry.link}"[:1024]

    if image_url:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(image_url, headers=headers, timeout=15)
            if response.status_code == 200:
                bot.send_photo(CHANNEL_ID, response.content, caption=caption)
            else:
                bot.send_message(CHANNEL_ID, caption)
        except Exception as e:
            print(f"[Send] Image error: {e}")
            bot.send_message(CHANNEL_ID, caption)
    else:
        bot.send_message(CHANNEL_ID, caption)


# --- AUTOMATION ---
def process_new_posts():
    print("[Scheduler] Checking for new posts...")
    try:
        entries = fetch_feed()
        new_count = 0
        # Process in reverse (oldest to newest) to preserve order
        for entry in reversed(entries):
            if not entry.get("link"):
                continue

            if not is_link_sent(entry.link):
                print(f"[Scheduler] New post found: {extract_caption(entry)[:80]}...")
                send_post(entry)
                mark_as_sent(entry.link)
                new_count += 1
                # Small delay between posts to avoid Telegram rate limits
                time.sleep(2)

        print(f"[Scheduler] Check complete. Posted {new_count} new item(s).")
    except Exception as e:
        print(f"[Scheduler] Error in process_new_posts: {e}")
        traceback.print_exc()


def run_scheduler():
    print("[Scheduler] Started. Checking every 5 minutes.")
    # Run once immediately on startup
    process_new_posts()

    while True:
        try:
            time.sleep(300)  # Check every 5 minutes
            process_new_posts()
        except Exception as e:
            print(f"[Scheduler] Unexpected error in loop: {e}")
            traceback.print_exc()
            # Keep the loop alive even after errors
            time.sleep(60)


# --- COMMANDS ---
@bot.message_handler(commands=["snd"])
def manual_send(message):
    bot.reply_to(message, "🔍 Checking for new posts...")
    process_new_posts()
    bot.reply_to(message, "✅ Check complete.")


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(message, "🤖 Bot is running and connected to Supabase.")


if __name__ == "__main__":
    # Start Web Server (keep-alive / health check)
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))),
        daemon=True,
    ).start()

    # Start Automation
    scheduler_thread = threading.Thread(target=run_scheduler, daemon=False, name="scheduler")
    scheduler_thread.start()

    # Run Bot
    print("Bot is polling...")
    bot.infinity_polling(none_stop=True, skip_pending=True)
