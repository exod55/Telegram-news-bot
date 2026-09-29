import os
import re
import threading
import time
import traceback
import html
import feedparser
import telebot
import requests
from flask import Flask, jsonify, send_from_directory
from supabase import create_client

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# --- SUPABASE SETUP ---
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__, static_folder="static", static_url_path="")


@app.route("/")
def home():
    """Serve the Mini App frontend."""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/posts")
def get_posts():
    """JSON feed for the Telegram Mini App."""
    try:
        response = (
            supabase.table("posted_links")
            .select("id, link, caption, image_url, created_at")
            .order("created_at", desc=True)
            .limit(25)
            .execute()
        )
        return jsonify({"ok": True, "posts": response.data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/health")
def health():
    return "Bot is live!"


# --- DATABASE FUNCTIONS ---
def get_clean_link(link):
    """Removes tracking junk and trailing slashes."""
    return link.split("?")[0].rstrip("/")


def is_link_sent(link):
    clean_link = get_clean_link(link)
    response = (
        supabase.table("posted_links")
        .select("link")
        .eq("link", clean_link)
        .execute()
    )
    return len(response.data) > 0


def mark_as_sent(link, caption="", image_url=""):
    clean_link = get_clean_link(link)
    try:
        supabase.table("posted_links").insert(
            {
                "link": clean_link,
                "caption": caption,
                "image_url": image_url,
            }
        ).execute()
    except Exception as e:
        print(f"Database insertion error (likely duplicate): {e}")


def update_post_fields(link, caption=None, image_url=None):
    """Update caption and/or image_url for an existing row."""
    clean_link = get_clean_link(link)
    payload = {}
    if caption is not None:
        payload["caption"] = caption
    if image_url is not None:
        payload["image_url"] = image_url
    if not payload:
        return False
    try:
        supabase.table("posted_links").update(payload).eq("link", clean_link).execute()
        return True
    except Exception as e:
        print(f"Database update error: {e}")
        return False


def get_rows_needing_refill():
    """
    Rows where caption or image_url is null or empty.
    Fetches in pages to stay within API limits.
    """
    try:
        response = (
            supabase.table("posted_links")
            .select("id, link, caption, image_url")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        rows = response.data or []
        needs = []
        for row in rows:
            cap = row.get("caption")
            img = row.get("image_url")
            if not cap or not str(cap).strip() or not img or not str(img).strip():
                needs.append(row)
        return needs
    except Exception as e:
        print(f"[Refill] Error listing rows: {e}")
        return []


# --- CAPTION HELPERS ---
def strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def remove_bio_phrases(text):
    if not text:
        return text

    patterns = [
        r"(?i)\s*link\s+in\s+(?:the\s+)?(?:bio|comments?)\b[^.]*\.?\s*$",
        r"(?i)\s*read\s+more\s+in\s+(?:bio|comments?)\b[^.]*\.?\s*$",
        r"(?i)\s*full\s+(?:review|article|story)\s+in\s+(?:bio|comments?)\b[^.]*\.?\s*$",
        r"(?i)\s*more\s+(?:info|details?|here)\s+in\s+(?:bio|comments?)\b[^.]*\.?\s*$",
        r"(?i)\s*link\s+in\s+bio\.?\s*$",
        r"\s*\.{2,}\s*$",
    ]

    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned)

    return cleaned.strip(" \t\n\r.-–—")


def extract_caption(entry):
    candidates = []
    for key in ("summary", "description"):
        raw = entry.get(key)
        if raw:
            plain = strip_html(raw)
            if plain:
                candidates.append(plain)

    title = (entry.get("title") or "").strip()
    if title:
        candidates.append(title)

    caption = max(candidates, key=len) if candidates else ""
    caption = remove_bio_phrases(caption)

    if not caption:
        caption = "New post"

    return caption


# --- RSS & BOT LOGIC ---
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

    mark_as_sent(entry.link, caption_body, image_url or "")


def process_new_posts():
    print("[Scheduler] Checking for new posts...")
    try:
        entries = fetch_feed()
        new_count = 0
        for entry in reversed(entries):
            if not entry.get("link"):
                continue

            if not is_link_sent(entry.link):
                print(f"[Scheduler] New post: {extract_caption(entry)[:80]}...")
                send_post(entry)
                new_count += 1
                time.sleep(2)

        print(f"[Scheduler] Check complete. Posted {new_count} item(s).")
    except Exception as e:
        print(f"[Scheduler] Error: {e}")
        traceback.print_exc()


def refill_null_fields():
    """
    Re-fetch the RSS feed and fill null/empty caption or image_url
    for posts that still appear in the feed.
    Returns (updated_count, skipped_count, message).
    """
    print("[Refill] Starting backfill of null caption/image_url...")
    entries = fetch_feed()
    if not entries:
        return 0, 0, "Could not fetch RSS feed (empty or error)."

    # Map cleaned link -> (caption, image_url) from live feed
    feed_map = {}
    for entry in entries:
        if not entry.get("link"):
            continue
        clean = get_clean_link(entry.link)
        img = None
        if "media_content" in entry and entry.media_content:
            img = entry.media_content[0].get("url")
        feed_map[clean] = {
            "caption": extract_caption(entry),
            "image_url": img or "",
        }

    needs = get_rows_needing_refill()
    if not needs:
        return 0, 0, "No rows with empty caption or image_url."

    updated = 0
    skipped = 0

    for row in needs:
        clean = get_clean_link(row.get("link") or "")
        if clean not in feed_map:
            skipped += 1
            continue

        data = feed_map[clean]
        new_caption = row.get("caption")
        new_image = row.get("image_url")

        # Only fill missing pieces
        if not new_caption or not str(new_caption).strip():
            new_caption = data["caption"]
        if not new_image or not str(new_image).strip():
            new_image = data["image_url"]

        if update_post_fields(clean, caption=new_caption, image_url=new_image):
            updated += 1
            print(f"[Refill] Updated: {clean}")
        else:
            skipped += 1

    msg = (
        f"Refill done. Updated {updated} row(s). "
        f"Skipped {skipped} (not in current RSS feed or update failed)."
    )
    print(f"[Refill] {msg}")
    return updated, skipped, msg


def run_scheduler():
    print("[Scheduler] Started. Checking every 5 minutes.")
    process_new_posts()
    while True:
        try:
            time.sleep(300)
            process_new_posts()
        except Exception as e:
            print(f"[Scheduler] Unexpected error: {e}")
            traceback.print_exc()
            time.sleep(60)


# --- COMMANDS ---
@bot.message_handler(commands=["snd"])
def manual_send(message):
    bot.reply_to(message, "🔍 Checking for new posts...")
    process_new_posts()
    bot.reply_to(message, "✅ Check complete.")


@bot.message_handler(commands=["refill", "backfill"])
def refill_command(message):
    bot.reply_to(
        message,
        "🔄 Refilling empty caption/image_url from the live RSS feed…",
    )
    try:
        updated, skipped, msg = refill_null_fields()
        bot.reply_to(message, f"✅ {msg}")
    except Exception as e:
        traceback.print_exc()
        bot.reply_to(message, f"❌ Refill failed: {e}")


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "🤖 Bot is running.\n"
        "Commands:\n"
        "/snd — check for new posts\n"
        "/refill — fill null caption/image_url from RSS\n"
        "Open the Mini App from the menu button.",
    )


if __name__ == "__main__":
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 8080)),
            use_reloader=False,
        ),
        daemon=True,
    ).start()

    scheduler_thread = threading.Thread(
        target=run_scheduler, daemon=False, name="scheduler"
    )
    scheduler_thread.start()

    print("Bot is polling...")
    bot.infinity_polling(none_stop=True, skip_pending=True)
