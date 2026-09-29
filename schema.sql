-- Run this once in the Supabase SQL Editor
-- Adds caption + image_url for the Telegram Mini App feed

ALTER TABLE public.posted_links
  ADD COLUMN IF NOT EXISTS caption text,
  ADD COLUMN IF NOT EXISTS image_url text;

-- Optional: ensure unique links and a timestamp
-- (skip if your table already has these)
-- ALTER TABLE public.posted_links ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();
-- CREATE UNIQUE INDEX IF NOT EXISTS posted_links_link_key ON public.posted_links (link);
