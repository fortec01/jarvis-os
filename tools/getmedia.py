#!/usr/bin/env python3
"""getmedia — JarvisOS's media downloader (stdlib only).

Powered by public Cobalt API instances (open-source downloader;
supports YouTube, Instagram, X/Twitter, TikTok, Vimeo,
SoundCloud, Reddit, Dailymotion, Twitch clips and more).
A Cobalt instance fetches on ITS infrastructure and tunnels the
file through, which is why downloads work from this box while
direct extraction (yt-dlp etc.) is IP-walled — proven live
2026-11-13 with a full 11.8 MB MP4.

Instances are tried in order (seed list below; override with
GETMEDIA_API, comma-separated). Downloads land in the current
directory. Size cap: GETMEDIA_MAX_MB (default 300).

Usage:
  getmedia <url> [--audio] [--quality 720]
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
SEEDS = [
    "https://api.cobalt.liubquanti.click",
    "https://cobalt-api.kwiatekmiki.com",
    "https://api.cobalt.canine.tools",
]


def api_hosts():
    env = os.environ.get("GETMEDIA_API", "").strip()
    if env:
        return [h.strip().rstrip("/") for h in env.split(",") if h.strip()]
    return SEEDS


def post_json(url, payload, timeout=45):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"User-Agent": UA, "Accept": "application/json",
                 "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def safe_name(name, fallback):
    name = os.path.basename((name or "").strip())
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name if name and name not in (".", "..") else fallback


def download(url, filename, max_bytes):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    total = 0
    with urllib.request.urlopen(req, timeout=120) as r, open(filename, "wb") as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                f.close()
                os.unlink(filename)
                raise RuntimeError(f"download exceeded the {max_bytes // (1 << 20)} MB cap")
            f.write(chunk)
    return total, time.time() - t0


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    flags = [a for a in argv[1:] if a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    url = args[0]
    audio = "--audio" in flags
    quality = "720"
    for i, a in enumerate(argv):
        if a == "--quality" and i + 1 < len(argv):
            quality = argv[i + 1]
    max_bytes = int(os.environ.get("GETMEDIA_MAX_MB", "300")) * (1 << 20)

    payload = {"url": url, "videoQuality": quality,
               "downloadMode": "audio" if audio else "auto",
               "filenameStyle": "basic"}
    if audio:
        payload["audioFormat"] = "mp3"

    errors = []
    for host in api_hosts():
        try:
            resp = post_json(host + "/", payload)
        except Exception as e:
            errors.append(f"{host}: {type(e).__name__}")
            continue
        status = resp.get("status")
        if status == "error":
            errors.append(f"{host}: {(resp.get('error') or {}).get('code', 'error')}")
            continue
        jobs = []
        if status in ("tunnel", "redirect", "local-processing"):
            jobs.append((resp.get("url"), resp.get("filename")))
        elif status == "picker":
            for i, item in enumerate(resp.get("picker", [])[:5]):
                jobs.append((item.get("url"), None))
            if resp.get("audio"):
                jobs.append((resp.get("audio"), resp.get("audioFilename")))
        if not jobs or not jobs[0][0]:
            errors.append(f"{host}: unexpected response {str(resp)[:80]}")
            continue
        saved = []
        for i, (dl_url, fname) in enumerate(jobs):
            if not dl_url:
                continue
            tail = urllib.parse.urlparse(dl_url).path.rsplit("/", 1)[-1]
            name = safe_name(fname, tail if "." in tail else f"download-{int(time.time())}-{i}")
            size, secs = download(dl_url, name, max_bytes)
            saved.append(name)
            print(f"SAVED {name} | {size / (1 << 20):.1f} MB in {secs:.1f}s | via {host}")
        if saved:
            return 0
        errors.append(f"{host}: no downloadable items")
    print("getmedia: all instances failed (" + "; ".join(errors) + ")")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
