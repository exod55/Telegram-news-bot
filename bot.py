import os
import time
import json
import threading
import feedparser
import telebot
from flask import Flask

# --- FLASK SERVER FOR RENDER FREE WEB SERVICE ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running live!"

def run_flask():
    # Render assigns a dynamic port via environment variable PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
CHECK_INTERVAL = 1800  # Check every 30 minutes

SENT_POSTS_FILE = "posted_links.json"

FEEDS = [
    "https://feeds.feedburner.com/ign/news",
    "https://screenrant.com/feed/",
    "https://variety.com/feed/",
]

if not BOT_TOKEN:
    raise ValueError("Missing BOT_TOKEN environment variable!")

bot = telebot.TeleBot(BOT_TOKEN)

def load_posted():
    if os.path.exists(SENT_POSTS_FILE):
        try:
            with open(SENT_POSTS_FILE, "r") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_posted(posted):
    try:
        with open(SENT_POSTS_FILE, "w") as f:
            json.dump(list(posted), f)
    except Exception as e:
        print(f"Error saving history: {e}")

def extract_image(entry):
    if "media_content" in entry and len(entry.media_content) > 0:
        return entry.media_content[0].get("url")
    if "enclosures" in entry and len(entry.enclosures) > 0:
        for enc in entry.enclosures:
            if enc.get("type", "").startswith("image/"):
                return enc.get("href")
    if "media_thumbnail" in entry and len(entry.media_thumbnail) > 0:
        return entry.media_thumbnail[0].get("url")
    return None

def process_feeds():
    posted = load_posted()
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:3]:
                link = entry.link
                if link in posted:
                    continue

                title = entry.title
                img = extract_image(entry)
                caption = f"🎬 **{title}**\n\n🔗 [Read More]({link})"

                if img:
                    bot.send_photo(CHANNEL_ID, photo=img, caption=caption, parse_mode="Markdown")
                else:
                    bot.send_message(CHANNEL_ID, text=caption, parse_mode="Markdown")

                posted.add(link)
                save_posted(posted)
                time.sleep(5)
        except Exception as e:
            print(f"Feed error: {e}")

def bot_loop():
    print("Starting bot execution loop...")
    while True:
        process_feeds()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    # 1. Start Flask web server in a separate background thread
    threading.Thread(target=run_flask, daemon=True).start()
    
    # 2. Run the Telegram bot news fetching loop on the main thread
    bot_loop()