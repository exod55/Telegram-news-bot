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
def get_clean_link(link):
    """Removes tracking junk (like ?igshid=) and trailing slashes."""
    return link.split('?')[0].rstrip('/')

def is_link_sent(link):
    clean_link = get_clean_link(link)
    response = supabase.table("posted_links").select("link").eq("link", clean_link).execute()
    return len(response.data) > 0

def mark_as_sent(link):
    clean_link = get_clean_link(link)
    try:
        supabase.table("posted_links").insert({"link": clean_link}).execute()
    except Exception as e:
        print(f"Database insertion error (likely duplicate): {e}")

# --- RSS FUNCTIONS ---
def fetch_feed():
    rss_url = "https://rss-bridge.org/bridge01/?action=display&bridge=InstagramBridge&context=Username&u=igndotcom&media_type=picture&format=Mrss"
    try:
        # User-Agent is important for RSS-Bridge to work reliably
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(rss_url, headers=headers, timeout=15)
        feed = feedparser.parse(response.content)
        return feed.entries
    except Exception as e:
        print(f"Error fetching feed: {e}")
        return []

def send_post(entry):
    image_url = entry.media_content[0]['url'] if 'media_content' in entry else None
    # Truncate caption to 1024 characters (Telegram limit for photos)
    caption = (f"🎬 {entry.title}\n\n🔗 {entry.link}")[:1024]
    
    if image_url:
        try:
            headers = {'User-Agent': 'Mozilla/5.0'}
            response = requests.get(image_url, headers=headers, timeout=15)
            if response.status_code == 200:
                bot.send_photo(CHANNEL_ID, response.content, caption=caption)
            else:
                bot.send_message(CHANNEL_ID, caption)
        except Exception as e:
            print(f"Image error: {e}")
            bot.send_message(CHANNEL_ID, caption)
    else:
        bot.send_message(CHANNEL_ID, caption)

# --- AUTOMATION ---
def process_new_posts():
    entries = fetch_feed()
    # Process in reverse (oldest to newest) to preserve order
    for entry in reversed(entries):
        if not entry.get('link'): continue 
        
        if not is_link_sent(entry.link):
            print(f"New post found: {entry.title}")
            send_post(entry)
            mark_as_sent(entry.link)
        else:
            # Uncomment the line below if you want to see the spam of "Skipping" in logs
            # print(f"Skipping: {entry.title}")
            pass

def run_scheduler():
    while True:
        process_new_posts()
        time.sleep(300) # Check every 5 minutes

# --- COMMANDS ---
@bot.message_handler(commands=['snd'])
def manual_send(message):
    bot.reply_to(message, "🔍 Checking for new posts...")
    process_new_posts()
    bot.reply_to(message, "✅ Check complete.")

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "🤖 Bot is running and connected to Supabase.")

if __name__ == "__main__":
    # Start Web Server
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    
    # Start Automation
    threading.Thread(target=run_scheduler, daemon=True).start()
    
    # Run Bot
    print("Bot is polling...")
    # Add skip_pending to clear out old interrupted connections
    bot.infinity_polling(none_stop=True, skip_pending=True)
