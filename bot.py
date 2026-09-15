import os
import threading
import time
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
def home(): return "Bot is live!"

# --- DATABASE FUNCTIONS ---
def is_link_sent(link):
    # Normalize link (strip trailing slashes to avoid duplicates)
    clean_link = link.rstrip('/')
    response = supabase.table("posted_links").select("link").eq("link", clean_link).execute()
    return len(response.data) > 0

def mark_as_sent(link):
    clean_link = link.rstrip('/')
    try:
        supabase.table("posted_links").insert({"link": clean_link}).execute()
    except Exception as e:
        print(f"Database insertion error: {e}")

# --- RSS FUNCTIONS ---
def fetch_feed():
    rss_url = "https://rss-bridge.org/bridge01/?action=display&bridge=InstagramBridge&context=Username&u=igndotcom&media_type=picture&format=Mrss"
    try:
        feed = feedparser.parse(rss_url)
        return feed.entries
    except Exception as e:
        print(f"Error fetching feed: {e}")
        return []

def send_post(entry):
    image_url = entry.media_content[0]['url'] if 'media_content' in entry else None
    caption = f"🎬 {entry.title}\n\n🔗 {entry.link}"
    
    if image_url:
        try:
            # We use a session to mimic a browser
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(image_url, headers=headers, timeout=15)
            if response.status_code == 200:
                bot.send_photo(CHANNEL_ID, response.content, caption=caption)
            else:
                bot.send_message(CHANNEL_ID, caption)
        except Exception as e:
            print(f"Image download error: {e}")
            bot.send_message(CHANNEL_ID, caption)
    else:
        bot.send_message(CHANNEL_ID, caption)

# --- AUTOMATION ---
def process_new_posts():
    entries = fetch_feed()
    # Reverse so we process oldest to newest
    for entry in reversed(entries):
        # Double check the link exists
        if not entry.get('link'): continue 
        
        if not is_link_sent(entry.link):
            print(f"Sending new post: {entry.title}")
            send_post(entry)
            mark_as_sent(entry.link)
        else:
            print(f"Skipping already sent: {entry.title}")

def run_scheduler():
    while True:
        process_new_posts()
        time.sleep(300) # 5 minutes

# --- COMMANDS ---
@bot.message_handler(commands=['snd'])
def manual_send(message):
    bot.reply_to(message, "🔍 Manually checking for new posts...")
    process_new_posts()
    bot.reply_to(message, "✅ Done.")

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    threading.Thread(target=run_scheduler, daemon=True).start()
    bot.infinity_polling()
