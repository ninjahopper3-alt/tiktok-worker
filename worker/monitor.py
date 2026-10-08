#!/usr/bin/env python3
"""
Quran AutoPost — external TikTok monitor worker.

Runs anywhere yt-dlp works (recommended: GitHub Actions on a schedule).
The website stays on cheap shared hosting; this worker does the heavy
TikTok listing from a healthy IP and pushes discoveries back.

Flow per run (stateless — safe to restart any time):
  1. GET {SITE_URL}/api/monitors            (Bearer WORKER_KEY)
  2. For each monitor: yt-dlp --flat-playlist (user @ or tag #)
  3. POST {SITE_URL}/api/discoveries        (new items only, server dedupes)
The website updates heartbeats itself on every API hit.

Env:
  SITE_URL    https://your-domain (no trailing slash)
  WORKER_KEY  key from the website's TikTok Monitor page
  LIMIT       videos per monitor per run (default 12, max 30)
  TIMEOUT     seconds per yt-dlp call (default 120)

Exit code is ALWAYS 0 on handled errors (a failed monitor must not fail
other monitors, and a failed run must not page anyone at 3am).
"""

import json
import os
import subprocess
import sys
import urllib.request

SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
WORKER_KEY = os.environ.get("WORKER_KEY", "")
LIMIT = max(1, min(30, int(os.environ.get("LIMIT", "12"))))
TIMEOUT = max(30, int(os.environ.get("TIMEOUT", "120")))
TT_DLP_BIN = os.environ.get("TT_DLP_BIN", "tt-dlp")
# Optional: paste a Netscape cookies.txt content to defeat bot-checks.
# Only needed if listings start failing — tt-dlp works without it today.
TIKTOK_COOKIES = os.environ.get("TIKTOK_COOKIES", "")

SEP = "\\x1f"  # NOTE: yt-dlp prints these 4 chars literally — split on them


def api(method, path, payload=None):
    url = SITE_URL + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": "Bearer " + WORKER_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "QuranAutoPost-Worker/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except Exception as e:  # network errors, HTTP errors, bad JSON
        print(f"[worker] API {method} {path} failed: {e}", flush=True)
        return 0, {}


def profile_url(kind, target):
    target = target.strip().lstrip("@#")
    if kind == "hashtag":
        return "https://www.tiktok.com/tag/" + target
    return "https://www.tiktok.com/@" + target


def list_videos(kind, target, limit):
    """tt-dlp first (proven against bot-checks), yt-dlp fallback.
    Returns (items, error). Never raises."""
    if kind == "user":
        items, err = list_via_ttdlp(target, limit)
        if items:
            return items, ""
        first_err = err
    else:
        first_err = ""
    items, err = list_via_ytdlp(kind, target, limit)
    if items:
        return items, ""
    return [], first_err or err


def list_via_ttdlp(target, limit):
    """Dry-run profile scan: parses '[dry-run] <id> <title>.mp4' lines."""
    import re
    import tempfile
    cookies_file = ""
    try:
        cmd = [TT_DLP_BIN, "--dry-run", "--no-stories",
               "--limit", str(limit), "-o", tempfile.gettempdir() + "/ttdlp"]
        if TIKTOK_COOKIES.strip():
            fd, cookies_file = tempfile.mkstemp(prefix="ttcookies", suffix=".txt")
            with os.fdopen(fd, "w") as f:
                f.write(TIKTOK_COOKIES)
            cmd += ["--cookies", cookies_file]
        cmd += ["@" + target.lstrip("@")]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
        items = []
        for line in (p.stdout or "").splitlines():
            line = line.strip()
            if not line.startswith("[dry-run]"):
                continue
            rest = line[len("[dry-run]"):].strip()
            m = re.match(r"(\d{10,25})\s+(.*)$", rest)
            if not m:
                continue
            vid, title = m.group(1), m.group(2)
            if title.lower().endswith(".mp4"):
                title = title[:-4]
            items.append({
                "tid": vid,
                "url": f"https://www.tiktok.com/@{target.lstrip('@')}/video/{vid}",
                "title": title[:500],
                "caption": title[:2000],
                "posted_at": None,
                "author": target.lstrip("@")[:64],
            })
            if len(items) >= limit:
                break
        if items:
            return items, ""
        err = (p.stderr or "").strip().splitlines()
        return [], err[-1][-300:] if err else "tt-dlp listed nothing"
    except FileNotFoundError:
        return [], "tt-dlp binary not found"
    except subprocess.TimeoutExpired:
        return [], "tt-dlp timed out"
    finally:
        if cookies_file:
            try:
                os.unlink(cookies_file)
            except OSError:
                pass


def list_via_ytdlp(kind, target, limit):
    url = profile_url(kind, target)
    fmt = "%(id)s\\x1f%(webpage_url)s\\x1f%(title)s\\x1f%(description)s\\x1f%(upload_date)s\\x1f%(uploader)s"
    cmd = [
        "yt-dlp", "--flat-playlist", "--no-warnings",
        "--socket-timeout", "20", "--playlist-end", str(limit),
        "--print", fmt, url,
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return [], "yt-dlp binary not found"
    except subprocess.TimeoutExpired:
        return [], "yt-dlp timed out"
    items = []
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("WARNING", "ERROR", "[")):
            continue
        parts = (line.split(SEP) + [""] * 6)[:6]
        vid, vurl, title, desc, udate, uploader = [x.strip() for x in parts]
        if not vid.isdigit() or not (10 <= len(vid) <= 25):
            m = None
            import re
            mm = re.search(r"/video/(\d{10,25})", vurl or "")
            if mm:
                vid = mm.group(1)
            else:
                continue
        if "tiktok.com" not in (vurl or ""):
            vurl = f"https://www.tiktok.com/@{target}/video/{vid}"
        posted = None
        if len(udate) == 8 and udate.isdigit():
            posted = f"{udate[0:4]}-{udate[4:6]}-{udate[6:8]} 00:00:00"
        items.append({
            "tid": vid,
            "url": vurl,
            "title": title[:500],
            "caption": (desc or title)[:2000],
            "posted_at": posted,
            "author": "".join(c for c in uploader if c.isalnum() or c in "._")[:64],
        })
        if len(items) >= limit:
            break
    if not items:
        err = (p.stderr or "").strip().splitlines()
        return [], err[-1][-300:] if err else "no videos listed"
    return items, ""


def main():
    if not SITE_URL or not WORKER_KEY:
        print("[worker] SITE_URL and WORKER_KEY env vars are required", flush=True)
        return 0
    code, data = api("GET", "/api/monitors")
    monitors = data.get("monitors", []) if isinstance(data, dict) else []
    if code != 200 or not monitors:
        print(f"[worker] no monitors (http={code}); nothing to do", flush=True)
        return 0
    print(f"[worker] {len(monitors)} monitor(s)", flush=True)
    for mon in monitors:
        kind = "hashtag" if mon.get("type") == "hashtag" else "user"
        target = str(mon.get("target", "")).strip().lstrip("@#")
        if not target:
            continue
        label = ("#" if kind == "hashtag" else "@") + target
        try:
            items, err = list_videos(kind, target, LIMIT)
        except Exception as e:  # absolute last resort — never crash the run
            print(f"[worker] {label}: unexpected {e}", flush=True)
            continue
        if not items:
            print(f"[worker] {label}: 0 new ({err})", flush=True)
            continue
        code, res = api("POST", "/api/discoveries", {
            "monitor": {"type": kind, "target": target},
            "items": items,
        })
        if isinstance(res, dict) and res.get("ok"):
            print(f"[worker] {label}: +{res.get('added', 0)} new, {res.get('skipped', 0)} known", flush=True)
        else:
            print(f"[worker] {label}: push failed (http={code})", flush=True)
    print("[worker] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
