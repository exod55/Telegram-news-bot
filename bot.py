import os
import threading
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

# --- BOT SETUP ---
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

@app.route("/")
def home(): return "Bot is live!"

# --- FUNCTIONS ---
def is_link_sent(link):
    return links_collection.find_one({"link": link}) is not None

def mark_as_sent(link):
    links_collection.insert_one({"link": link})

def check_feeds():
    rss_url = "https://rss-bridge.org/bridge01/?action=display&bridge=InstagramBridge&context=Username&u=IGN&media_type=picture&format=Mrss"
    print(f"Fetching RSS: {rss_url}")
    feed = feedparser.parse(rss_url)
    
    print(f"Found {len(feed.entries)} entries in feed.")
    
    for entry in reversed(feed.entries[:5]):
        print(f"Checking entry: {entry.title} - {entry.link}")
        if not is_link_sent(entry.link):
            print(f"New post found! Sending: {entry.title}")
            caption = f"🎬 **{entry.title}**\n\n🔗 [Read More]({entry.link})"
            
            try:
                # Let's try sending just text first to verify channel access
                bot.send_message(CHANNEL_ID, caption, parse_mode="Markdown")
                mark_as_sent(entry.link)
                print("Successfully sent and saved to DB.")
            except Exception as e:
                print(f"CRITICAL ERROR sending to channel: {e}")
        else:
            print("Post already in database, skipping.")

# --- THREADS ---
def run_scheduler():
    import time
    while True:
        try:
            check_feeds()
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(300)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "🤖 Bot is online and connected to MongoDB!")

if __name__ == "__main__":
    # Start Web Server
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    # Start Scheduler
    threading.Thread(target=run_scheduler, daemon=True).start()
    # Start Bot
    bot.infinity_polling()
