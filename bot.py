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

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__, static_folder="static", static_url_path="")

ALLOWED_IMAGE_HOSTS = {
    "www.instagram.com",
    "instagram.com",
}

IG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.instagram.com/",
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


# --- LINK / CAPTION HELPERS ---
def get_clean_link(link):
    return (link or "").split("?")[0].rstrip("/")


def instagram_shortcode(link: str):
    m = re.search(r"instagram\.com/(?:p|reel|tv)/([A-Za-z0-9_-]+)", link or "")
    return m.group(1) if m else None


def media_url_from_link(link: str) -> str:
    """Stable image URL derived only from the Instagram post link."""
    code = instagram_shortcode(link)
    if not code:
        return ""
    return f"https://www.instagram.com/p/{code}/media/?size=l"


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
    """Strip Instagram CTA phrases like 'Link in bio for more' anywhere in the text."""
    if not text:
        return text
    patterns = [
        r"(?i)\s*link\s+in\s+(?:the\s+)?(?:bio|comments?)\b[^.!\n]*[.!]?",
        r"(?i)\s*read\s+more\s+in\s+(?:bio|comments?)\b[^.!\n]*[.!]?",
        r"(?i)\s*full\s+(?:review|article|story)\s+in\s+(?:bio|comments?)\b[^.!\n]*[.!]?",
        r"(?i)\s*more\s+(?:info|details?|here)\s+in\s+(?:bio|comments?)\b[^.!\n]*[.!]?",
        r"(?i)\s*(?:check|see|click)(?:\s+out)?\s+(?:the\s+)?link\s+in\s+bio\b[^.!\n]*[.!]?",
        r"(?i)\s*link\s+in\s+bio\b[.!]?",
        r"(?i)\s*(?:check|see|click)(?:\s+out)?\s+the\s*$",
        r"(?i)\s*view\s+all\s+\d+\s+comments\b[.!]?",
        r"\s*\.{2,}\s*$",
    ]
    cleaned = text
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n\s*\n+", "\n\n", cleaned)
    return cleaned.strip(" \t\n\r.-–—|")


def extract_caption_from_rss_entry(entry):
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
    return remove_bio_phrases(caption)


def fetch_info_from_instagram_link(link: str) -> dict:
    """
    Use ONLY the Instagram post URL to build the data we store in Supabase:
      - image_url  → /p/{code}/media/?size=l
      - caption    → public embed page text
    """
    clean = get_clean_link(link)
    code = instagram_shortcode(clean)
    result = {"link": clean, "caption": "", "image_url": ""}

    if not code:
        print(f"[IG] Not an Instagram post link: {link}")
        return result

    result["image_url"] = media_url_from_link(clean)

    embed_url = f"https://www.instagram.com/p/{code}/embed/captioned/"
    try:
        r = requests.get(embed_url, headers=IG_HEADERS, timeout=20)
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
                result["caption"] = remove_bio_phrases(html.unescape(raw).strip())
        else:
            print(f"[IG] Embed HTTP {r.status_code} for {code}")
    except Exception as e:
        print(f"[IG] Embed fetch failed for {code}: {e}")

    if not result["caption"]:
        result["caption"] = "New post"

    print(
        f"[IG] Resolved {code}: "
        f"caption={result['caption'][:60]!r}... "
        f"image={bool(result['image_url'])}"
    )
    return result


# --- DATABASE ---
def is_link_sent(link):
    clean_link = get_clean_link(link)
    response = (
        supabase.table("posted_links")
        .select("link")
        .eq("link", clean_link)
        .execute()
    )
    return len(response.data) > 0


def save_post_to_supabase(link, caption="", image_url="", telegram_message_id=None):
    """Insert a full row using Instagram-derived fields (+ optional Telegram message id)."""
    clean_link = get_clean_link(link)
    if not image_url:
        image_url = media_url_from_link(clean_link)
    row = {
        "link": clean_link,
        "caption": caption or "New post",
        "image_url": image_url or "",
    }
    if telegram_message_id is not None:
        row["telegram_message_id"] = int(telegram_message_id)
    try:
        supabase.table("posted_links").insert(row).execute()
        print(f"[DB] Saved {clean_link} (msg_id={telegram_message_id})")
    except Exception as e:
        print(f"[DB] Insert error (likely duplicate): {e}")


def update_post_fields(link, caption=None, image_url=None, telegram_message_id=None):
    clean_link = get_clean_link(link)
    payload = {}
    if caption is not None:
        payload["caption"] = caption
    if image_url is not None:
        payload["image_url"] = image_url
    if telegram_message_id is not None:
        payload["telegram_message_id"] = int(telegram_message_id)
    if not payload:
        return False
    try:
        supabase.table("posted_links").update(payload).eq("link", clean_link).execute()
        return True
    except Exception as e:
        print(f"[DB] Update error: {e}")
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


# --- RSS ---
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
    """
    Discover posts via RSS (link only is enough), then ALWAYS resolve
    caption + image_url from the Instagram link before Telegram + Supabase.
    """
    post_link = entry.get("link")
    if not post_link:
        return

    ig = fetch_info_from_instagram_link(post_link)

    if not ig["caption"] or ig["caption"] == "New post":
        rss_cap = extract_caption_from_rss_entry(entry)
        if rss_cap:
            ig["caption"] = rss_cap

    # Always strip bio CTAs from final caption
    ig["caption"] = remove_bio_phrases(ig["caption"] or "") or "New post"

    if not ig["image_url"]:
        if "media_content" in entry and entry.media_content:
            ig["image_url"] = entry.media_content[0].get("url") or ""
        if not ig["image_url"]:
            ig["image_url"] = media_url_from_link(post_link)

    caption_body = remove_bio_phrases(ig["caption"]) or "New post"
    image_url = ig["image_url"]
    tg_caption = f"🎬 {caption_body}\n\n🔗 {get_clean_link(post_link)}"[:1024]

    msg = None
    if image_url:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.instagram.com/",
            }
            response = requests.get(image_url, headers=headers, timeout=20)
            if response.status_code == 200 and "image" in response.headers.get(
                "Content-Type", ""
            ):
                msg = bot.send_photo(CHANNEL_ID, response.content, caption=tg_caption)
            else:
                msg = bot.send_message(CHANNEL_ID, tg_caption)
        except Exception as e:
            print(f"[Send] Image error: {e}")
            msg = bot.send_message(CHANNEL_ID, tg_caption)
    else:
        msg = bot.send_message(CHANNEL_ID, tg_caption)

    msg_id = getattr(msg, "message_id", None) if msg is not None else None
    save_post_to_supabase(post_link, caption_body, image_url, telegram_message_id=msg_id)
    time.sleep(1.0)



def process_new_posts():
    print("[Scheduler] Checking for new posts...")
    try:
        entries = fetch_feed()
        new_count = 0
        for entry in reversed(entries):
            if not entry.get("link"):
                continue
            if not is_link_sent(entry.link):
                print(f"[Scheduler] New post link: {entry.link}")
                send_post(entry)
                new_count += 1
                time.sleep(1.5)
        print(f"[Scheduler] Check complete. Posted {new_count} item(s).")
    except Exception as e:
        print(f"[Scheduler] Error: {e}")
        traceback.print_exc()


def refill_null_fields():
    """
    For every row missing caption or image_url, use its stored Instagram
    link to fetch and write the missing fields.
    """
    print("[Refill] Filling from Instagram links...")
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

    for row in needs:
        link = row.get("link") or ""
        if not instagram_shortcode(link):
            skipped += 1
            continue

        ig = fetch_info_from_instagram_link(link)

        new_caption = row.get("caption")
        new_image = row.get("image_url")

        if not new_caption or not str(new_caption).strip():
            new_caption = ig["caption"]
        if not new_image or not str(new_image).strip():
            new_image = ig["image_url"] or media_url_from_link(link)

        got_cap = bool(new_caption and str(new_caption).strip())
        got_img = bool(new_image and str(new_image).strip())
        if not got_cap and not got_img:
            skipped += 1
            time.sleep(1.0)
            continue

        if update_post_fields(
            link,
            caption=new_caption if got_cap else None,
            image_url=new_image if got_img else None,
        ):
            updated += 1
            print(f"[Refill] Updated from IG link: {get_clean_link(link)}")
        else:
            skipped += 1

        time.sleep(1.2)

    if updated > 0:
        message = (
            f"✅ Refill worked!\n"
            f"• Updated: {updated} post(s) from Instagram links\n"
            f"• Skipped: {skipped}\n"
            f"Open the Mini App to see captions and pictures."
        )
        ok = True
    else:
        message = (
            f"❌ Refill did not update any rows.\n"
            f"• Candidates: {len(needs)}\n"
            f"• Skipped: {skipped}\n"
            f"Instagram may be rate-limiting — try again in a few minutes."
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



def send_channel_post(caption_body, image_url, post_link):
    """Send photo/text to the channel; return message_id or None."""
    tg_caption = f"🎬 {caption_body}\n\n🔗 {get_clean_link(post_link)}"[:1024]
    msg = None
    if image_url:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.instagram.com/",
            }
            response = requests.get(image_url, headers=headers, timeout=20)
            if response.status_code == 200 and "image" in response.headers.get(
                "Content-Type", ""
            ):
                msg = bot.send_photo(CHANNEL_ID, response.content, caption=tg_caption)
            else:
                msg = bot.send_message(CHANNEL_ID, tg_caption)
        except Exception as e:
            print(f"[Send] Image error: {e}")
            msg = bot.send_message(CHANNEL_ID, tg_caption)
    else:
        msg = bot.send_message(CHANNEL_ID, tg_caption)
    return getattr(msg, "message_id", None) if msg is not None else None


def edit_posts_on_telegram(repost_if_missing=False):
    """
    For each stored Instagram link: fetch full caption (no link-in-bio),
    update Supabase, and:
      - edit Telegram message if telegram_message_id exists
      - OR, if repost_if_missing=True, send a NEW channel post and store its message_id
    """
    print(f"[EditPost] Starting (repost_if_missing={repost_if_missing})...")
    try:
        response = (
            supabase.table("posted_links")
            .select("id, link, caption, image_url, telegram_message_id")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        rows = response.data or []
    except Exception as e:
        return {
            "ok": False,
            "changed": 0,
            "message": f"❌ Could not load posts from Supabase: {e}",
        }

    if not rows:
        return {
            "ok": True,
            "changed": 0,
            "message": "✅ No posts found in Supabase.",
        }

    # Count how many lack message ids
    missing_ids = sum(1 for r in rows if not r.get("telegram_message_id"))

    if missing_ids == len(rows) and not repost_if_missing:
        return {
            "ok": False,
            "changed": 0,
            "message": (
                "⚠️ All posts have no telegram_message_id — Telegram cannot edit them.\n\n"
                "Supabase captions can still be updated, but channel messages need a repost.\n\n"
                "Run this to repost with full captions and save new message ids:\n"
                "/edit_post repost\n\n"
                f"({missing_ids} posts would be sent to the channel again.)"
            ),
        }

    changed = 0
    tg_edited = 0
    tg_reposted = 0
    db_only = 0
    skipped = 0
    errors = 0

    for row in rows:
        link = row.get("link") or ""
        if not instagram_shortcode(link):
            skipped += 1
            continue

        try:
            ig = fetch_info_from_instagram_link(link)
            new_caption = remove_bio_phrases(ig.get("caption") or "") or "New post"
            new_image = (
                ig.get("image_url")
                or media_url_from_link(link)
                or row.get("image_url")
                or ""
            )

            msg_id = row.get("telegram_message_id")
            tg_caption = f"🎬 {new_caption}\n\n🔗 {get_clean_link(link)}"[:1024]

            edited_tg = False
            reposted = False

            if msg_id:
                try:
                    bot.edit_message_caption(
                        chat_id=CHANNEL_ID,
                        message_id=int(msg_id),
                        caption=tg_caption,
                    )
                    edited_tg = True
                    tg_edited += 1
                except Exception:
                    try:
                        bot.edit_message_text(
                            tg_caption,
                            chat_id=CHANNEL_ID,
                            message_id=int(msg_id),
                        )
                        edited_tg = True
                        tg_edited += 1
                    except Exception as e2:
                        print(f"[EditPost] TG edit failed for {link}: {e2}")
                        if repost_if_missing:
                            new_id = send_channel_post(new_caption, new_image, link)
                            if new_id:
                                msg_id = new_id
                                reposted = True
                                tg_reposted += 1
                            else:
                                db_only += 1
                        else:
                            db_only += 1
            else:
                if repost_if_missing:
                    new_id = send_channel_post(new_caption, new_image, link)
                    if new_id:
                        msg_id = new_id
                        reposted = True
                        tg_reposted += 1
                    else:
                        db_only += 1
                else:
                    db_only += 1

            # Always update Supabase caption/image; store message id when we edited or reposted
            kwargs = {"caption": new_caption, "image_url": new_image}
            if edited_tg or reposted:
                kwargs["telegram_message_id"] = msg_id
            update_post_fields(link, **kwargs)

            changed += 1
            print(
                f"[EditPost] {get_clean_link(link)} "
                f"edit={edited_tg} repost={reposted}"
            )
        except Exception as e:
            errors += 1
            print(f"[EditPost] Error on {link}: {e}")

        time.sleep(1.2)

    message = (
        f"✅ /edit_post finished\n"
        f"• Posts processed: {changed}\n"
        f"• Telegram captions edited: {tg_edited}\n"
        f"• Telegram reposted (new message ids): {tg_reposted}\n"
        f"• Supabase-only: {db_only}\n"
        f"• Skipped: {skipped}\n"
        f"• Errors: {errors}"
    )
    if changed == 0 and errors:
        message = f"❌ /edit_post failed for all rows ({errors} errors)."
        ok = False
    else:
        ok = True

    print(message)
    return {"ok": ok, "changed": changed, "message": message}


HELP_TEXT = (
    "🤖 Bot commands\n\n"
    "/start — Confirm the bot is running\n"
    "/help — Show this help message\n"
    "/snd — Manually check for new Instagram posts and send them to the channel\n"
    "/refill — Fill empty caption/image_url using each row's Instagram link "
    "(alias: /backfill)\n"
    "/edit_post — Update captions from Instagram; edit Telegram if message id exists\n"
    "/edit_post repost — Repost to channel when message id is missing (saves new ids)\n\n"
    "📱 Open the Mini App from the menu button to browse the feed."
)


@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(message, HELP_TEXT)


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
        "🔄 Refilling from each post's Instagram link…\n"
        "This may take a minute for older rows.",
    )
    try:
        result = refill_null_fields()
        bot.reply_to(message, result["message"])
    except Exception as e:
        traceback.print_exc()
        bot.reply_to(message, f"❌ Refill failed with an error:\n{e}")


@bot.message_handler(commands=["edit_post"])
def edit_post_command(message):
    parts = (message.text or "").split()
    repost = any(p.lower() in ("repost", "resend", "force") for p in parts[1:])

    if repost:
        bot.reply_to(
            message,
            "✏️ Updating from Instagram links…\n"
            "Posts without a message id will be REPOSTED to the channel "
            "with full captions. This can take a few minutes.",
        )
    else:
        bot.reply_to(
            message,
            "✏️ Updating captions from each Instagram link…\n"
            "Edits Telegram posts when message ids are stored.\n"
            "If all ids are missing, you will be told to run /edit_post repost.",
        )
    try:
        result = edit_posts_on_telegram(repost_if_missing=repost)
        bot.reply_to(message, result["message"])
    except Exception as e:
        traceback.print_exc()
        bot.reply_to(message, f"❌ /edit_post failed:\n{e}")


@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "🤖 Bot is running and connected to Supabase.\n\n"
        "Type /help to see all commands.\n"
        "Open the Mini App from the menu button for the feed.",
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
