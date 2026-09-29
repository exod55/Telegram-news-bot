# Telegram News Bot (Instagram → Telegram)

Automatically reposts new Instagram posts from **@igndotcom** into a Telegram channel.

The bot polls an RSS feed (via [RSS-Bridge](https://rss-bridge.org)), deduplicates posts with Supabase, cleans captions (removes “Link in bio” style phrases), and sends the image + caption to your channel every 5 minutes.

## Features

- Automatic checks every 5 minutes (plus an immediate check on startup)
- Caption cleanup: strips “Link in bio”, “Read more in bio”, “Link in the comments”, etc.
- Uses the fuller caption from the RSS description when available
- Deduplication via Supabase (`posted_links` table)
- Manual force-check with `/snd`
- Simple Flask health endpoint (`/`) for keep-alive on PaaS hosts
- Docker-ready

## Requirements

- Python 3.11+
- A Telegram bot token ([@BotFather](https://t.me/BotFather))
- A Telegram channel (bot must be an admin)
- A [Supabase](https://supabase.com) project with a table named `posted_links`

### Supabase table

Create a table `posted_links` with at least:

| Column | Type    | Notes                          |
|--------|---------|--------------------------------|
| `link` | text    | Primary key / unique recommended |
| `id`   | bigint  | Optional auto-increment PK     |

Example SQL:

```sql
create table posted_links (
  id bigint generated always as identity primary key,
  link text unique not null,
  created_at timestamptz default now()
);
```

## Environment variables

| Variable       | Description                          |
|----------------|--------------------------------------|
| `BOT_TOKEN`    | Telegram bot token                   |
| `CHANNEL_ID`   | Target channel ID (e.g. `-100…`)     |
| `SUPABASE_URL` | Supabase project URL                 |
| `SUPABASE_KEY` | Supabase service or anon key         |
| `PORT`         | Optional; Flask port (default 8080)  |

## Local setup

```bash
git clone https://github.com/YOUR_USERNAME/Telegram-news-bot.git
cd Telegram-news-bot
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
export BOT_TOKEN=...
export CHANNEL_ID=...
export SUPABASE_URL=...
export SUPABASE_KEY=...
python bot.py
```

## Docker

```bash
docker build -t telegram-news-bot .
docker run -d \
  -e BOT_TOKEN=... \
  -e CHANNEL_ID=... \
  -e SUPABASE_URL=... \
  -e SUPABASE_KEY=... \
  -p 8080:8080 \
  telegram-news-bot
```

## Bot commands

| Command | Description                |
|---------|----------------------------|
| `/start`| Confirm the bot is running |
| `/snd`  | Manually check for new posts |

## How captions work

1. Prefer the plain-text body from the RSS `description` / `summary` (fuller than the truncated `title`).
2. Strip HTML and trailing “Link in bio / comments / read more…” phrases.
3. Post as: `🎬 {cleaned caption}` + link to the Instagram post.

## License

MIT
