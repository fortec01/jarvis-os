#!/usr/bin/env python3
"""webfetch — Jarvis's fingerprint-clean web reader (Scrapling HTTP tier).

Fetches a URL impersonating a real browser's TLS fingerprint and
headers (via Scrapling's Fetcher / curl_cffi), then prints the page
text, or the text of elements matching a CSS selector.

Usage:
  webfetch <url> [--impersonate chrome|firefox|safari] [--css SELECTOR]

Runs on the JarvisOS venv python (see tools/webfetch wrapper).
Part of JarvisOS v0.3 — the web ladder: curl -> webfetch (clean
fingerprint) -> lightpanda (real JavaScript) -> remote stealth.
"""
import argparse
import sys


def main() -> int:
    ap = argparse.ArgumentParser(prog="webfetch")
    ap.add_argument("url")
    ap.add_argument("--impersonate", default="chrome",
                    help="browser identity to impersonate (default: chrome)")
    ap.add_argument("--css", default="",
                    help="CSS selector; print matching elements' text only")
    args = ap.parse_args()

    from scrapling.fetchers import Fetcher

    page = Fetcher.get(args.url, impersonate=args.impersonate, timeout=60)
    print(f"STATUS {page.status} {args.url}")
    if args.css:
        for el in page.css(args.css):
            print(el.get_all_text(strip=True))
    else:
        body = page.css("body")
        if body:
            print(body[0].get_all_text(strip=True)[:8000])
        else:
            print("(no body text extracted)")
    return 0 if page.status < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
