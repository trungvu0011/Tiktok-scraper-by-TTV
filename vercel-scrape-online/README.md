# Scrape online — Vercel deployment

Standalone, **HTTP-only** TikTok profile scraper packaged as a Vercel project.
No browser, no database, no background workers — just one serverless function +
a static page, so it runs on Vercel's free tier.

```
vercel-scrape-online/
├── api/
│   └── scrape_online.py   # serverless function → GET /api/scrape_online?username=
├── index.html             # static UI (served at /)
├── vercel.json            # function config (maxDuration)
└── requirements.txt       # empty — stdlib only
```

## Deploy — Option A: GitHub import (recommended, no CLI)

1. Push this repo to GitHub (already at `trungvu0011/Tiktok-scraper-by-TTV`).
2. Go to <https://vercel.com/new> → **Import** that repo.
3. Set **Root Directory** = `vercel-scrape-online`.
4. Framework Preset = **Other** (leave build/output empty). Click **Deploy**.
5. Open the deployment URL → enter a `@username`.

## Deploy — Option B: Vercel CLI

```bash
cd vercel-scrape-online
npx vercel            # first run: log in (browser/email), then follow prompts
npx vercel --prod     # promote to production
```

## ⚠️ The catch: datacenter IPs

TikTok serves an **anti-bot interstitial** to many datacenter IPs (Vercel
included), so a live request may return:

```json
{ "status": "error", "method": "http",
  "error": { "message": "TikTok đã chặn truy cập HTTP (trang chống bot)…" } }
```

This is expected for HTTP-only scraping from cloud IPs — it is **not** a bug in
the function. To make it succeed reliably:

- Add a **residential/mobile proxy** as an environment variable in Vercel:
  **Project → Settings → Environment Variables** → `PROXY_URL` =
  `http://user:pass@host:port` → **Redeploy**.

For full data (the whole video grid, charts, competitor tracking, alerts) keep
running the main dashboard on a persistent host (VPS / Railway / Render); only
this lightweight profile lookup is serverless-friendly.

## Local test

```bash
cd vercel-scrape-online
python -c "from api.scrape_online import scrape_profile; import json; \
print(json.dumps(scrape_profile('oppo.talent'), ensure_ascii=False, indent=2))"
```
