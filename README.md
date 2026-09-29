# Telegram News Bot + 3D Mini App

Automatically reposts Instagram posts from **@igndotcom** into a Telegram channel, and serves a **Telegram Mini App** with a 3D glassmorphism feed.

## Features

- RSS polling every 5 minutes (plus check on startup)
- Clean captions (strips “Link in bio”, “Read more in bio”, etc.)
- Deduplication via Supabase
- `/api/posts` JSON API for the Mini App
- Single-page Mini App: Three.js background, glass cards, tilt effect, Telegram theme sync
- Manual force-check: `/snd`

## Project layout

```
Telegram-news-bot-main/
├── bot.py              # Bot + Flask API + scheduler
├── static/
│   └── index.html      # Mini App frontend
├── schema.sql          # Supabase table updates
├── requirements.txt
├── Dockerfile
└── README.md
```

## 1. Database (Supabase)

Run in the Supabase SQL editor:

```sql
ALTER TABLE public.posted_links
  ADD COLUMN IF NOT EXISTS caption text,
  ADD COLUMN IF NOT EXISTS image_url text;
```

Full reference table:

| Column       | Type        | Notes                    |
|--------------|-------------|--------------------------|
| `id`         | bigint      | PK (auto)                |
| `link`       | text        | Unique Instagram URL     |
| `caption`    | text        | Cleaned caption          |
| `image_url`  | text        | Media URL from RSS       |
| `created_at` | timestamptz | Default `now()`          |

## 2. Environment variables

| Variable       | Description                    |
|----------------|--------------------------------|
| `BOT_TOKEN`    | Telegram bot token             |
| `CHANNEL_ID`   | Channel ID (e.g. `-100…`)      |
| `SUPABASE_URL` | Supabase project URL           |
| `SUPABASE_KEY` | Supabase key                   |
| `PORT`         | Optional (default `8080`)      |

## 3. Local run

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN=... CHANNEL_ID=... SUPABASE_URL=... SUPABASE_KEY=...
python bot.py
```

- Mini App UI: `http://localhost:8080/`
- API: `http://localhost:8080/api/posts`
- Health: `http://localhost:8080/health`

## 4. Docker

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

## 5. Telegram Mini App setup

1. Deploy the service over **HTTPS** (Render, Railway, Fly.io, etc.).
2. In [@BotFather](https://t.me/BotFather):
   - `/newapp` or **Bot Settings → Menu Button / Configure Mini App**
   - Set the URL to your deployed root, e.g. `https://your-app.onrender.com/`
3. Open the bot → Menu / Mini App button → feed loads from `/api/posts`.

## Bot commands

| Command    | Description |
|------------|-------------|
| `/start`   | Confirm the bot is running |
| `/help`    | List all commands and descriptions |
| `/snd`     | Manually check for new posts |
| `/refill`  | Fill null/empty `caption` and `image_url` from the live RSS feed (alias: `/backfill`) |

### `/refill` notes

- Replies clearly whether it **worked** (updated N posts) or **did not**.
- **Step 1:** match posts still in the live RSS feed.
- **Step 2:** for older posts still on Instagram but out of RSS, fetch the public embed page for caption and build a media URL for the image.
- Safe to run multiple times; it only writes missing fields.
- May take a minute if many rows need Instagram page fetches (rate-limit friendly delay).

### Mini App images

Instagram media URLs often block direct hotlinking. The backend exposes `/api/image?url=...` which proxies allowed Instagram hosts so pictures show in the Mini App.

## Notes

- New posts store `caption` + `image_url` in Supabase so the Mini App can render them.
- Older rows without those columns will show without image/caption until new posts arrive (or you backfill).
- Instagram media URLs may require a browser User-Agent; the bot already uses one when downloading for Telegram.

## License

MIT
