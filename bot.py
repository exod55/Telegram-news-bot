import os
import threading
import time
import feedparser
import telebot
from flask import Flask
from pymongo import MongoClient

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
MONGO_URI = os.environ.get("MONGO_URI")

# --- DATABASE SETUP ---
client = MongoClient(MONGO_URI)
db = client.bot_database
links_collection = db.posted_links

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

@app.route("/")
def home(): return "Bot is live!"

# --- FUNCTIONS ---
def is_link_sent(link):
    return links_collection.find_one({"link": link}) is not None

def mark_as_sent(link):
    if not is_link_sent(link):
        links_collection.insert_one({"link": link})

def fetch_feed():
    rss_url = "https://rss-bridge.org/bridge01/?action=display&bridge=InstagramBridge&context=Username&u=igndotcom&media_type=picture&format=Mrss"
    try:
        feed = feedparser.parse(rss_url)
        return feed.entries
    except Exception as e:
        print(f"Error fetching feed: {e}")
        return []

def send_post(entry):
    """Helper to send photo or text based on availability"""
    image_url = entry.media_content[0]['url'] if 'media_content' in entry else None
    caption = f"🎬 {entry.title}\n\n🔗 {entry.link}"
    
    if image_url:
        bot.send_photo(CHANNEL_ID, image_url, caption=caption)
    else:
        bot.send_message(CHANNEL_ID, caption)

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "🤖 Bot is online!")

@bot.message_handler(commands=['snd'])
def send_latest(message):
    bot.send_message(message.chat.id, "🔍 Checking for new posts...")
    entries = fetch_feed()
    count = 0
    # Process in reverse to maintain chronological order
    for entry in reversed(entries):
        if not is_link_sent(entry.link):
            send_post(entry)
            mark_as_sent(entry.link)
            count += 1
    bot.send_message(message.chat.id, f"✅ Processed {count} new posts.")

# --- AUTOMATION ---
def run_scheduler():
    while True:
        entries = fetch_feed()
        for entry in reversed(entries):
            if not is_link_sent(entry.link):
                try:
                    send_post(entry)
                    mark_as_sent(entry.link)
                except Exception as e:
                    print(f"Auto-send error: {e}")
        time.sleep(300) # Check every 5 minutes

if __name__ == "__main__":
    # Start Web Server
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    
    # Start Automation
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # Run Bot
    print("Bot is polling...")
    bot.infinity_polling(none_stop=True)
