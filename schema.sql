-- Run this once in the Supabase SQL Editor

ALTER TABLE public.posted_links
  ADD COLUMN IF NOT EXISTS caption text,
  ADD COLUMN IF NOT EXISTS image_url text,
  ADD COLUMN IF NOT EXISTS telegram_message_id bigint;
