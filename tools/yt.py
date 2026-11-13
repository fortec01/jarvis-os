#!/usr/bin/env python3
"""yt — JarvisOS's YouTube data tool (stdlib only).

Routes (all proven live from this box, 2026-11-13):
  - Invidious public API (instances auto-discovered from
    api.invidious.io, api-enabled only, health-tried in order)
    for search / info / transcripts / channel lookup.
  - Channel RSS feeds (feeds/videos.xml) for latest uploads.
  - oEmbed as the last-resort fallback for basic info.
Media bytes do NOT come through here — use `getmedia` (Cobalt).

Usage:
  yt search <query...>
  yt info <url|video-id>
  yt transcript <url|video-id>
  yt channel <@handle|channel-id>
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
SEED_INSTANCES = ["yt.chocolatemoo53.com"]
CACHE = "/tmp/yt-instances.json"
TIMEOUT = 25


def http(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def http_json(url, timeout=TIMEOUT):
    return json.loads(http(url, timeout))


def instances():
    hosts = list(SEED_INSTANCES)
    try:
        if os.path.exists(CACHE) and time.time() - os.path.getmtime(CACHE) < 3600:
            raw = open(CACHE).read()
        else:
            raw = http("https://api.invidious.io/instances.json", timeout=15)
            try:
                open(CACHE, "w").write(raw)
            except OSError:
                pass
        for name, info in json.loads(raw):
            if info.get("api") and info.get("type") == "https" and name not in hosts:
                hosts.append(name)
    except Exception:
        pass
    return hosts


def via_api(path):
    """Try each api-enabled instance in order; return (host, json)."""
    errors = []
    for host in instances():
        try:
            return host, http_json(f"https://{host}{path}")
        except Exception as e:
            errors.append(f"{host}: {type(e).__name__}")
    raise RuntimeError("all Invidious instances failed (" + "; ".join(errors) + ")")


def video_id(text):
    text = text.strip()
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})", text)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", text):
        return text
    raise SystemExit(f"yt: cannot find a video id in: {text}")


def fmt_dur(seconds):
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "?"
    return f"{s // 60}:{s % 60:02d}"


def cmd_search(query):
    host, data = via_api("/api/v1/search?q=" + urllib.parse.quote(query) + "&type=video")
    print(f"via {host}")
    n = 0
    for item in data:
        if item.get("type") != "video":
            continue
        n += 1
        print(f"{n}. {item.get('title')}")
        print(f"   {item.get('author')} | {fmt_dur(item.get('lengthSeconds'))} | "
              f"{item.get('viewCountText', '')} | id {item.get('videoId')}")
        if n >= 10:
            break
    if n == 0:
        print("no results")


def cmd_info(vid):
    try:
        host, d = via_api(f"/api/v1/videos/{vid}")
        print(f"via {host}")
        print(f"TITLE: {d.get('title')}")
        print(f"AUTHOR: {d.get('author')} ({d.get('authorId')})")
        print(f"LENGTH: {fmt_dur(d.get('lengthSeconds'))} | VIEWS: {d.get('viewCount')} | "
              f"LIKES: {d.get('likeCount')} | PUBLISHED: {d.get('publishedText')}")
        caps = [c.get("label") for c in d.get("captions", [])]
        print("CAPTIONS: " + (", ".join(caps) if caps else "none"))
        desc = (d.get("description") or "").strip()
        if desc:
            print("DESCRIPTION:")
            print(desc[:1200])
    except Exception:
        d = http_json("https://www.youtube.com/oembed?url="
                      f"https://www.youtube.com/watch?v={vid}&format=json")
        print("via oembed (fallback)")
        print(f"TITLE: {d.get('title')}")
        print(f"AUTHOR: {d.get('author_name')}")


def clean_vtt(vtt):
    lines, seen = [], None
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line:
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line and line != seen:
            lines.append(line)
            seen = line
    return "\n".join(lines)


def cmd_transcript(vid):
    host, d = via_api(f"/api/v1/captions/{vid}")
    caps = d.get("captions", [])
    if not caps:
        raise SystemExit("yt: this video has no captions")
    def rank(c):
        label = (c.get("label") or "").lower()
        if "english" in label and "auto" not in label:
            return 0
        if "english" in label:
            return 1
        if "auto" not in label:
            return 2
        return 3
    caps.sort(key=rank)
    pick = caps[0]
    vtt = http(f"https://{host}{pick.get('url')}")
    text = clean_vtt(vtt)
    print(f"via {host} | track: {pick.get('label')}")
    print(text if text else "(caption track was empty)")


def rss_latest(cid, count=10):
    xml = http(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    entries = xml.split("<entry>")[1:]
    out = []
    for e in entries[:count]:
        title = re.search(r"<title>([^<]*)</title>", e)
        link = re.search(r'<link rel="alternate" href="([^"]+)"', e)
        pub = re.search(r"<published>([^<]*)</published>", e)
        out.append((title.group(1) if title else "?",
                    pub.group(1)[:10] if pub else "",
                    link.group(1) if link else ""))
    return out


def cmd_channel(who):
    who = who.strip()
    cid = None
    if re.fullmatch(r"UC[A-Za-z0-9_-]{22}", who):
        cid = who
    else:
        handle = who if who.startswith("@") else "@" + who
        host, data = via_api("/api/v1/search?q=" + urllib.parse.quote(handle) + "&type=channel")
        for item in data:
            if item.get("type") == "channel":
                cid = item.get("authorId")
                print(f"via {host}: {item.get('author')} ({cid}) | subs {item.get('subCount')}")
                break
        if not cid:
            raise SystemExit(f"yt: channel not found: {who}")
    print("LATEST UPLOADS (via RSS):")
    for title, date, link in rss_latest(cid):
        print(f"- [{date}] {title}")
        print(f"  {link}")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, rest = argv[1], argv[2:]
    try:
        if cmd == "search":
            cmd_search(" ".join(rest))
        elif cmd == "info":
            cmd_info(video_id(rest[0]))
        elif cmd == "transcript":
            cmd_transcript(video_id(rest[0]))
        elif cmd == "channel":
            cmd_channel(rest[0])
        else:
            print(__doc__)
            return 2
    except SystemExit:
        raise
    except Exception as e:
        print(f"yt: {cmd} failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
