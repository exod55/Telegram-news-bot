import os
import time
import json
import threading
import feedparser
import telebot
from flask import Flask

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
CHECK_INTERVAL = 300
SENT_POSTS_FILE = "posted_links.json"
FEEDS = ["https://rss-bridge.org/bridge01/?action=display&bridge=InstagramBridge&context=Username&u=IGN&media_type=picture&format=Mrss"]

app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running!"

bot = telebot.TeleBot(BOT_TOKEN)

# --- UTILS ---
def load_posted():
    if os.path.exists(SENT_POSTS_FILE):
        try:
            with open(SENT_POSTS_FILE, "r") as f: return set(json.load(f))
        except: return set()
    return set()

def save_posted(posted):
    with open(SENT_POSTS_FILE, "w") as f: json.dump(list(posted), f)

# --- BOT COMMANDS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    desc = (
        "🤖 **Bot is Online!**\n\n"
        "This bot monitors Instagram updates and forwards them to your Telegram channel.\n"
        "Status: Active and checking for new posts every 5 minutes."
    )
    bot.reply_to(message, desc, parse_mode="Markdown")

# --- FEED LOGIC ---
def process_feeds():
    print("Checking feeds...")
    posted = load_posted()
    for feed_url in FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            # Process entries (reverse to post oldest to newest if needed)
            for entry in reversed(feed.entries[:5]): 
                link = entry.link
                if link in posted:
                    continue

                title = entry.title
                # Logic to find image
                img = None
                if "media_content" in entry: img = entry.media_content[0]['url']
                
                caption = f"🎬 **{title}**\n\n🔗 [Read More]({link})"
                
                try:
                    if img: bot.send_photo(CHANNEL_ID, photo=img, caption=caption, parse_mode="Markdown")
                    else: bot.send_message(CHANNEL_ID, text=caption, parse_mode="Markdown")
                    
                    posted.add(link)
                    save_posted(posted)
                    time.sleep(2)
                except Exception as e:
                    print(f"Failed to send: {e}")
        except Exception as e:
            print(f"Feed error: {e}")

def run_scheduler():
    while True:
        process_feeds()
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    # Start Flask
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    
    # Start Scheduler
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # Run Bot Polling (The Main Thread)
    print("Bot is polling...")
    bot.infinity_polling()
