# TikTok Monitor Worker (external, free)

Your website stays on cheap shared hosting. **This worker runs somewhere
yt-dlp actually works** — recommended: GitHub Actions (free, scheduled,
zero servers). It only discovers videos (IDs/URLs/titles) and pushes them
to your website; the website still downloads MP4s itself on demand.

## How it works

```
GitHub runner (every 30 min)
  │  GET {SITE}/api/monitors            (Bearer key)
  │  yt-dlp --flat-playlist per monitor (users AND hashtags)
  ▼
Website: new rows appear in Videos → Discover
  │  (you press Download — existing chain handles the MP4)
```

- Stateless: every run is independent; restarts are always safe.
- Per-monitor isolation: one failing monitor never stops the others.
- Duplicates impossible: the website dedupes by TikTok video ID (UNIQUE).
- Heartbeats: every API hit updates the worker pill on your dashboard.

## Extraction backend: tt-dlp first

Discovery runs [`tt-dlp`](https://github.com/bajaam/tt-dlp) (dry-run profile
scans — no media downloaded by the worker) with yt-dlp as automatic
fallback. tt-dlp was verified against real profiles including bot-check
conditions; it also supports an optional cookies file:

- `TT_DLP_BIN` — path/command for tt-dlp (default `tt-dlp`, must be on PATH)
- `TIKTOK_COOKIES` — paste a Netscape `cookies.txt` content **only if**
  listings start failing with bot-checks. Export it from your browser while
  logged into TikTok, keep it secret (it is a login session — never commit
  it, pass it as a GitHub secret like `WORKER_KEY`).

Requires Python 3.10+ (the workflow uses 3.12).

## Setup (5 minutes)

1. **Website:** open the TikTok Monitor page → copy the worker API key
   (and note the Site URL shown next to it).
2. **GitHub:** put this project (at least `worker/`) in any repo →
   Settings → Secrets and variables → Actions → add secrets:
   - `SITE_URL` — e.g. `https://quranpost.site.je` (no trailing slash)
   - `WORKER_KEY` — the key from step 1
3. **Actions tab** → enable workflows. It runs every 30 minutes from then
   on. Press **Run workflow** any time to check immediately.

## Run it anywhere else

Anything with Python 3.10+ and network works (Render cron, a VPS timer,
your laptop):

```bash
pip install -r worker/requirements.txt
SITE_URL=https://your-domain WORKER_KEY=xxx LIMIT=12 python worker/monitor.py
```

## Notes & limits

- TikTok rate-limits datacenter IPs too — GitHub runners usually pass,
  but a run can come back empty; the next one retries automatically.
- Hashtag pages are flakier than user profiles (TikTok-side limitation);
  per-monitor errors show on the website's Monitors page.
- The worker never sees your admin password and can't download or delete
  anything — the key only allows monitor-list reads and discovery pushes.
