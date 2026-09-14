import os
import json
import feedparser
import telebot
from flask import Flask
from threading import Thread

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
SENT_POSTS_FILE = "posted_links.json"
FEEDS = ["https://rss-bridge.org/bridge01/?action=display&bridge=InstagramBridge&context=Username&u=IGN&media_type=picture&format=Mrss"]

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

@app.route("/")
def home(): return "Bot is running!"

# --- UTILS ---
def load_posted():
    if os.path.exists(SENT_POSTS_FILE):
        with open(SENT_POSTS_FILE, "r") as f:
            try: return set(json.load(f))
            except: return set()
    return set()

def save_posted(posted):
    with open(SENT_POSTS_FILE, "w") as f:
        json.dump(list(posted), f)

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "🤖 Bot is alive! I am monitoring your Instagram feed and will forward new posts to the channel.")

# --- FEED CHECKER ---
def check_feeds():
    print("Checking feeds...")
    posted = load_posted()
    for feed_url in FEEDS:
        feed = feedparser.parse(feed_url)
        # Process from oldest to newest to avoid spamming at once
        for entry in reversed(feed.entries[:5]):
            if entry.link not in posted:
                caption = f"🎬 **{entry.title}**\n\n🔗 [Read More]({entry.link})"
                try:
                    bot.send_message(CHANNEL_ID, caption, parse_mode="Markdown")
                    posted.add(entry.link)
                    save_posted(posted)
                except Exception as e:
                    print(f"Error sending: {e}")

# --- MAIN EXECUTION ---
def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

if __name__ == "__main__":
    # Start web server
    Thread(target=run_web, daemon=True).start()
    
    # Run bot polling in the main thread
    # We use a timer to check feeds every 5 minutes without blocking the bot
    print("Bot started...")
    
    def schedule_check():
        while True:
            check_feeds()
            import time
            time.sleep(300)
            
    Thread(target=schedule_check, daemon=True).start()
    
    bot.infinity_polling()
