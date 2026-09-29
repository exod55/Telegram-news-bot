import os
import re
import threading
import time
import traceback
import html
from urllib.parse import urlparse

import feedparser
import telebot
import requests
from flask import Flask, jsonify, send_from_directory, request, Response
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

ALLOWED_IMAGE_HOSTS = {
    "www.instagram.com",
    "instagram.com",
}


def _host_allowed(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
        if host in ALLOWED_IMAGE_HOSTS:
            return True
        if host.endswith(".cdninstagram.com") or host.endswith(".instagram.com"):
            return True
        return False
    except Exception:
        return False


@app.route("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/posts")
def get_posts():
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


@app.route("/api/image")
def proxy_image():
    """Proxy Instagram media for the Mini App."""
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "error": "missing url"}), 400
    if not url.startswith("https://"):
        return jsonify({"ok": False, "error": "only https urls allowed"}), 400
    if not _host_allowed(url):
        return jsonify({"ok": False, "error": "host not allowed"}), 403

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Referer": "https://www.instagram.com/",
        }
        r = requests.get(url, headers=headers, timeout=20, stream=True)
        if r.status_code != 200:
            return jsonify({"ok": False, "error": f"upstream {r.status_code}"}), 502

        content_type = r.headers.get("Content-Type", "image/jpeg")
        if "text/html" in content_type:
            return jsonify({"ok": False, "error": "upstream returned html"}), 502

        return Response(
            r.iter_content(chunk_size=16 * 1024),
            status=200,
            content_type=content_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )
    except Exception as e:
        print(f"[Proxy] Image error: {e}")
        return jsonify({"ok": False, "error": str(e)}), 502


@app.route("/health")
def health():
    return "Bot is live!"


# --- DATABASE FUNCTIONS ---
def get_clean_link(link):
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
        r"(?i)\s*view\s+all\s+\d+\s+comments\.?\s*$",
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
    return caption or "New post"


def instagram_shortcode(link: str):
    """Extract shortcode from /p/CODE/ or /reel/CODE/ URLs."""
    m = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", link or "")
    return m.group(1) if m else None


def media_url_from_link(link: str):
    """Build a working Instagram media URL from a post link."""
    code = instagram_shortcode(link)
    if not code:
        return ""
    return f"https://www.instagram.com/p/{code}/media/?size=l"


def fetch_instagram_post_meta(link: str):
    """
    Fetch caption (and confirm media) for posts no longer in the RSS feed
    via Instagram's public embed page.
    """
    code = instagram_shortcode(link)
    if not code:
        return {"caption": "", "image_url": ""}

    image_url = media_url_from_link(link)
    caption = ""

    embed_url = f"https://www.instagram.com/p/{code}/embed/captioned/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.instagram.com/",
    }

    try:
        r = requests.get(embed_url, headers=headers, timeout=20)
        if r.status_code == 200 and r.text:
            m = re.search(
                r'class="Caption"[^>]*>(.*?)</div>\s*<div class="CaptionComments"',
                r.text,
                re.I | re.S,
            )
            if not m:
                m = re.search(r'class="Caption"[^>]*>(.*?)</div>', r.text, re.I | re.S)
            if m:
                raw = m.group(1)
                raw = re.sub(
                    r'<a class="CaptionUsername"[^>]*>.*?</a>',
                    "",
                    raw,
                    flags=re.I | re.S,
                )
                raw = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
                raw = re.sub(r"<[^>]+>", "", raw)
                caption = remove_bio_phrases(html.unescape(raw).strip())
    except Exception as e:
        print(f"[Refill] Embed fetch failed for {code}: {e}")

    return {"caption": caption, "image_url": image_url}


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
    if not image_url and entry.get("link"):
        image_url = media_url_from_link(entry.link)

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
    Fill null/empty caption or image_url.
    1) Match against live RSS feed
    2) For the rest, fetch Instagram embed page + construct media URL
       (works for posts still on Instagram but no longer in RSS)
    """
    print("[Refill] Starting backfill...")
    entries = fetch_feed()

    feed_map = {}
    for entry in entries:
        if not entry.get("link"):
            continue
        clean = get_clean_link(entry.link)
        img = None
        if "media_content" in entry and entry.media_content:
            img = entry.media_content[0].get("url")
        if not img:
            img = media_url_from_link(entry.link)
        feed_map[clean] = {
            "caption": extract_caption(entry),
            "image_url": img or "",
        }

    needs = get_rows_needing_refill()
    if not needs:
        return {
            "ok": True,
            "updated": 0,
            "skipped": 0,
            "message": "✅ Nothing to refill — every recent row already has caption and image_url.",
        }

    updated = 0
    skipped = 0
    from_rss = 0
    from_ig = 0

    for row in needs:
        clean = get_clean_link(row.get("link") or "")
        if not clean:
            skipped += 1
            continue

        new_caption = row.get("caption")
        new_image = row.get("image_url")
        source = None

        if clean in feed_map:
            data = feed_map[clean]
            if not new_caption or not str(new_caption).strip():
                new_caption = data["caption"]
            if not new_image or not str(new_image).strip():
                new_image = data["image_url"]
            source = "rss"
        else:
            # Still on Instagram, but not in the short RSS window
            data = fetch_instagram_post_meta(clean)
            if not new_caption or not str(new_caption).strip():
                new_caption = data.get("caption") or new_caption
            if not new_image or not str(new_image).strip():
                new_image = data.get("image_url") or media_url_from_link(clean)
            source = "instagram"
            # Be polite to Instagram
            time.sleep(1.2)

        # Require at least one improvement
        had_cap = bool(row.get("caption") and str(row.get("caption")).strip())
        had_img = bool(row.get("image_url") and str(row.get("image_url")).strip())
        got_cap = bool(new_caption and str(new_caption).strip())
        got_img = bool(new_image and str(new_image).strip())

        if (not had_cap and not got_cap) and (not had_img and not got_img):
            skipped += 1
            continue

        if update_post_fields(
            clean,
            caption=new_caption if got_cap else None,
            image_url=new_image if got_img else None,
        ):
            updated += 1
            if source == "rss":
                from_rss += 1
            else:
                from_ig += 1
            print(f"[Refill] Updated ({source}): {clean}")
        else:
            skipped += 1

    if updated > 0:
        message = (
            f"✅ Refill worked!\n"
            f"• Updated: {updated} post(s)\n"
            f"  – from RSS: {from_rss}\n"
            f"  – from Instagram pages: {from_ig}\n"
            f"• Skipped: {skipped}\n"
            f"Open the Mini App to see captions and pictures."
        )
        ok = True
    else:
        message = (
            f"❌ Refill did not update any rows.\n"
            f"• Candidates: {len(needs)}\n"
            f"• Skipped: {skipped}\n"
            f"Instagram may be rate-limiting; try again in a few minutes."
        )
        ok = False

    print(f"[Refill] {message}")
    return {"ok": ok, "updated": updated, "skipped": skipped, "message": message}


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


HELP_TEXT = (
    "🤖 *Bot commands*\n\n"
    "/start — Confirm the bot is running\n"
    "/help — Show this help message\n"
    "/snd — Manually check for new Instagram posts and send them to the channel\n"
    "/refill — Fill empty caption/image\\_url from RSS + Instagram pages "
    "(alias: /backfill)\n\n"
    "📱 Open the *Mini App* from the menu button to browse the 3D glass feed."
)


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(message, HELP_TEXT, parse_mode="Markdown")


@bot.message_handler(commands=["snd"])
def manual_send(message):
    bot.reply_to(message, "🔍 Checking for new posts...")
    try:
        process_new_posts()
        bot.reply_to(message, "✅ Check complete.")
    except Exception as e:
        traceback.print_exc()
        bot.reply_to(message, f"❌ Check failed: {e}")


@bot.message_handler(commands=["refill", "backfill"])
def refill_command(message):
    bot.reply_to(
        message,
        "🔄 Refilling empty caption/image_url…\n"
        "Using live RSS + Instagram pages for older posts. This can take a minute.",
    )
    try:
        result = refill_null_fields()
        bot.reply_to(message, result["message"])
    except Exception as e:
        traceback.print_exc()
        bot.reply_to(message, f"❌ Refill failed with an error:\n{e}")


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "🤖 Bot is running and connected to Supabase.\n\n"
        "Type /help to see all commands.\n"
        "Open the Mini App from the menu button for the 3D feed.",
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
