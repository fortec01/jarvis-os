#!/usr/bin/env python3
"""JarvisOS visual shell v2 — the Nebula face.

Layout replicates, element for element, the lock screen in the
Nebula OS reel eres shared (Hyprland + Quickshell, Material 3):
  - anime sunset wallpaper (original art, generated for JarvisOS)
  - top-centre circle trio: weather, avatar ring, battery ring
  - orange music card: album art, title/artist, controls,
    Bass/Vocal/Rock toggles, seek dots, EQ bars
  - scalloped red clock blob with time + date
  - notification pills on the right
  - password pill under the clock (JarvisOS: "ask jarvis" pill)
  - 3x2 stat cards: CPU / RAM / GPU / TEMP / DISK / NET
Values are REAL where the machine has them (CPU, RAM, DISK, NET,
uptime-as-battery); GPU/TEMP show — on servers without sensors.

Drawn with Pillow only when a frame is requested: ~0 MB idle.
"""
import io
import math
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1600, 900
HERE = os.path.dirname(os.path.abspath(__file__))
WALLPAPER = os.path.join(HERE, "wallpaper.jpg")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONTB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DARK = (26, 12, 38)
WHITE = (255, 255, 255)


def F(size, bold=False):
    return ImageFont.truetype(FONTB if bold else FONT, size)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def rounded(img, radius):
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, img.size[0], img.size[1]], radius=radius, fill=255)
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (0, 0), m)
    return out


def base_wallpaper():
    if os.path.exists(WALLPAPER):
        im = Image.open(WALLPAPER).convert("RGB")
        return ImageOps.fit(im, (W, H))
    # fallback: gradient sunset
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        c = lerp((43, 18, 74), (232, 110, 62), t) if t < 0.8 else lerp((232, 110, 62), (24, 8, 34), (t - 0.8) / 0.2)
        for x in range(W):
            px[x, y] = c
    return img


def render_frame(stats, now=None):
    now = now or datetime.now()
    img = base_wallpaper().convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    # ---------- top-centre circle trio
    trio_y = 94
    # weather circle (olive, cloud + temp)
    d.ellipse([716, trio_y - 44, 804, trio_y + 44], fill=(96, 92, 40, 235))
    d.text((738, trio_y - 26), "☁", font=F(30, True), fill=WHITE + (255,))
    tw = d.textlength("4°", font=F(20, True))
    d.text((760 - tw / 2, trio_y + 8), "4°", font=F(20, True), fill=WHITE + (255,))
    # avatar ring (empty ring like the reel's middle circle)
    d.ellipse([836, trio_y - 44, 924, trio_y + 44], outline=(255, 255, 255, 190), width=5)
    d.text((862, trio_y - 16), "J", font=F(30, True), fill=WHITE + (220,))
    # battery ring — JarvisOS: uptime ring, % of a day awake
    pct_day = min(100, round(stats.get("uptime_min", 0) / 1440 * 100))
    d.arc([956, trio_y - 44, 1044, trio_y + 44], start=-90, end=-90 + 3.6 * max(pct_day, 4), fill=(255, 255, 255, 235), width=6)
    d.ellipse([956, trio_y - 44, 1044, trio_y + 44], outline=(255, 255, 255, 70), width=2)
    up_min = stats.get("uptime_min", 0)
    label = f"{up_min / 60:.1f}h" if up_min < 1440 else f"{up_min // 1440}d"
    tw = d.textlength(label, font=F(20, True))
    d.text((1000 - tw / 2, trio_y - 13), label, font=F(20, True), fill=WHITE + (255,))

    # ---------- music card (left, orange)
    mx0, my0, mx1, my1 = 84, 196, 486, 604
    grad = Image.new("RGBA", (mx1 - mx0, my1 - my0))
    gd = ImageDraw.Draw(grad)
    for y in range(my1 - my0):
        c = lerp((248, 156, 32), (206, 78, 30), y / (my1 - my0))
        gd.line([(0, y), (mx1 - mx0, y)], fill=c + (255,))
    ov.paste(rounded(grad, 42), (mx0, my0))
    d = ImageDraw.Draw(ov)
    # album art = a crop of the wallpaper, like a cover
    try:
        art = ImageOps.fit(Image.open(WALLPAPER).convert("RGB"), (150, 150))
    except Exception:
        art = Image.new("RGB", (150, 150), (60, 30, 60))
    ov.paste(rounded(art.convert("RGBA"), 22), (mx0 + 126, my0 + 24))
    d = ImageDraw.Draw(ov)
    title = "I Really Want to Stay at Your…"
    tw = d.textlength(title, font=F(20, True))
    d.text((mx0 + (mx1 - mx0 - tw) / 2, my0 + 188), title, font=F(20, True), fill=WHITE + (255,))
    sub = "Rosa Walton"
    tw = d.textlength(sub, font=F(17))
    d.text((mx0 + (mx1 - mx0 - tw) / 2, my0 + 218), sub, font=F(17), fill=WHITE + (215,))
    # controls
    for i, cx in enumerate([mx0 + 96, mx0 + 201, mx0 + 306]):
        d.ellipse([cx - 26, my0 + 242, cx + 26, my0 + 294], fill=DARK + (90,))
        cy = my0 + 268
        if i == 1:
            d.rectangle([cx - 9, cy - 11, cx - 2, cy + 11], fill=WHITE + (255,))
            d.rectangle([cx + 2, cy - 11, cx + 9, cy + 11], fill=WHITE + (255,))
        else:
            s = -1 if i == 0 else 1
            d.polygon([(cx + 10 * s, cy - 11), (cx + 10 * s, cy + 11), (cx - 8 * s, cy)], fill=WHITE + (255,))
            d.rectangle([cx + 14 * s - 2, cy - 11, cx + 14 * s + 2, cy + 11], fill=WHITE + (255,))
    # Bass / Vocal / Rock toggles
    chips = ["Bass", "Vocal", "Rock"]
    cx0 = mx0 + 52
    for i, chip in enumerate(chips):
        x = cx0 + i * 108
        on = i == 0
        d.rounded_rectangle([x, my0 + 304, x + 92, my0 + 334], radius=15,
                            fill=WHITE + (235,) if on else DARK + (70,))
        tw = d.textlength(chip, font=F(15, True))
        d.text((x + (92 - tw) / 2, my0 + 311), chip, font=F(15, True),
               fill=(120, 50, 10, 255) if on else WHITE + (230,))
    # seek dots (two rows, progress-filled)
    for r, filled in enumerate([15, 9]):
        for i in range(24):
            x = mx0 + 40 + i * 14
            y = my0 + 346 + r * 17
            d.ellipse([x, y, x + 7, y + 7], fill=WHITE + (240,) if i < filled else WHITE + (80,))
    # EQ bars
    import random
    random.seed(7)
    for i in range(28):
        x = mx0 + 40 + i * 12
        h = 6 + random.randint(0, 16)
        d.rounded_rectangle([x, my1 - 18 - h, x + 6, my1 - 18], radius=3, fill=WHITE + (200,))

    # ---------- clock blob (centre)
    cx, cy, R = 838, 288, 138
    blob = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(blob)
    pts = []
    for a in range(0, 360, 3):
        r = R * (1 + 0.05 * math.sin(math.radians(a) * 6))
        pts.append((cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))))
    bd.polygon(pts, fill=(224, 72, 58, 255))
    ov = Image.alpha_composite(ov, blob)
    d = ImageDraw.Draw(ov)
    tstr = now.strftime("%H:%M")
    fnt = F(80, True)
    tw = d.textlength(tstr, font=fnt)
    d.text((cx - tw / 2, cy - 56), tstr, font=fnt, fill=WHITE + (255,))
    dstr = now.strftime("%a, %b %d")
    tw = d.textlength(dstr, font=F(22))
    d.text((cx - tw / 2, cy + 44), dstr, font=F(22), fill=WHITE + (230,))

    # ---------- notification pills (right)
    notes = [("jarvis", "awake · ready"), ("workshop", "render free tier")]
    for i, (app, msg) in enumerate(notes):
        y = 238 + i * 62
        d.rounded_rectangle([1120, y, 1516, y + 48], radius=24, fill=DARK + (120,))
        d.rounded_rectangle([1136, y + 10, 1164, y + 38], radius=8, fill=(255, 170, 90, 255))
        d.text((1144, y + 13), app[0].upper(), font=F(17, True), fill=DARK + (255,))
        d.text((1178, y + 6), app, font=F(16, True), fill=WHITE + (255,))
        d.text((1178, y + 26), msg, font=F(14), fill=WHITE + (190,))

    # ---------- ask pill (password pill's JarvisOS form)
    pill_w, pill_y = 430, 452
    px0 = cx - pill_w // 2
    d.rounded_rectangle([px0, pill_y, px0 + pill_w, pill_y + 52], radius=26, fill=DARK + (130,))
    d.text((px0 + 26, pill_y + 15), "✦  ask jarvis anything…", font=F(18), fill=WHITE + (225,))
    d.ellipse([px0 + pill_w - 46, pill_y + 8, px0 + pill_w - 10, pill_y + 44], fill=WHITE + (235,))
    d.text((px0 + pill_w - 37, pill_y + 13), "→", font=F(20, True), fill=DARK + (255,))

    # ---------- stat cards 3x2 (centre-bottom)
    cards = [
        ("CPU", f"{stats['cpu_pct']:.0f}%", stats["cpu_pct"]),
        ("RAM", f"{stats['mem_used_mb'] / 1024:.1f}G", stats["mem_pct"]),
        ("GPU", "—", None),
        ("TEMP", "—", None),
        ("DISK", f"{stats['disk_used_pct']:.0f}%", stats["disk_used_pct"]),
        ("NET", f"{stats.get('net_kbps', 0):.0f}K/s", None),
    ]
    cw, chh, gap = 196, 108, 16
    grid_w = cw * 3 + gap * 2
    x0 = (W - grid_w) // 2 + 60
    for i, (label, val, pct) in enumerate(cards):
        x = x0 + (i % 3) * (cw + gap)
        y = 586 + (i // 3) * (chh + gap)
        d.rounded_rectangle([x, y, x + cw, y + chh], radius=24, fill=DARK + (165,))
        d.text((x + 20, y + 12), "▣ " + label, font=F(16, True), fill=(255, 196, 150, 235))
        d.text((x + 20, y + 38), val, font=F(36, True), fill=WHITE + (255,))
        if pct is not None:
            d.rounded_rectangle([x + 20, y + 88, x + cw - 20, y + 96], radius=4, fill=WHITE + (55,))
            d.rounded_rectangle([x + 20, y + 88, x + 20 + (cw - 40) * min(pct, 100) / 100, y + 96],
                                radius=4, fill=(255, 170, 90, 255))
        else:
            d.text((x + 20, y + 84), "n/a on this server" if label in ("GPU", "TEMP") else "live",
                   font=F(12), fill=WHITE + (130,))

    out = Image.alpha_composite(img, ov).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


if __name__ == "__main__":
    stats = {"cpu_pct": 3.0, "mem_used_mb": 812, "mem_total_mb": 7934, "mem_pct": 10.2,
             "disk_used_pct": 37.0, "load1": 0.42, "uptime_min": 103, "net_kbps": 10.2}
    import sys
    out_path = sys.argv[1] if len(sys.argv) > 1 else "desktop.png"
    with open(out_path, "wb") as f:
        f.write(render_frame(stats))
    print(f"wrote {out_path}")
