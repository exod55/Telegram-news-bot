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

def fetch_latest_post():
    rss_url = "https://rss-bridge.org/bridge01/?action=display&bridge=InstagramBridge&context=Username&u=igndotcom&media_type=picture&format=Mrss"
    feed = feedparser.parse(rss_url)
    if feed.entries:
        return feed.entries[0] # Return the very latest
    return None

# --- COMMANDS ---
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "🤖 Bot is online and connected to MongoDB!")

@bot.message_handler(commands=['snd'])
def send_latest(message):
    bot.send_message(message.chat.id, "🔍 Fetching latest post...")
    entry = fetch_latest_post()
    if entry:
        caption = f"🎬 {entry.title}\n\n🔗 {entry.link}"
        try:
            bot.send_message(CHANNEL_ID, caption)
            mark_as_sent(entry.link)
            bot.send_message(message.chat.id, "✅ Successfully sent to channel!")
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
    else:
        bot.send_message(message.chat.id, "❌ Could not find any posts.")

# --- AUTOMATION ---
def run_scheduler():
    while True:
        entry = fetch_latest_post()
        if entry and not is_link_sent(entry.link):
            caption = f"🎬 {entry.title}\n\n🔗 {entry.link}"
            try:
                bot.send_message(CHANNEL_ID, caption)
                mark_as_sent(entry.link)
            except Exception as e:
                print(f"Auto-send error: {e}")
        time.sleep(300)

if __name__ == "__main__":
    # Start Web Server
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    
    # Start Automation
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # Run Bot
    print("Bot is polling...")
    bot.infinity_polling(none_stop=True, timeout=30)
