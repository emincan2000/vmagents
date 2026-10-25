"""Puts a recording (phone.mjs, desktop.mjs) together, the slow parts sped
up as marks.json says: phone: each frame in a phone's frame, its caption on
the right; window: each frame in a browser window, its caption below.
Writes the frames and a GIF of them (ImageMagick). Needs Pillow.
Usage: video.py DIR OUT.gif [phone|window] [TITLE]"""
import json, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

src, gif = sys.argv[1], sys.argv[2]
layout = sys.argv[3] if len(sys.argv) > 3 else "phone"
title = sys.argv[4] if len(sys.argv) > 4 else ""
W, H, BG, FPS = 1024, 646, "#272822", 10
frames = json.load(open(f"{src}/frames.json"))
marks = json.load(open(f"{src}/marks.json"))
big = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
small = ImageFont.truetype("DejaVuSans.ttf", 19)
mid = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)


def frame_at(t):
    """The last frame that came by time t."""
    i = max((i for i, ft in enumerate(frames) if ft <= t), default=0)
    return i


def wrap(text, font, width, draw):
    lines, line = [], ""
    for word in text.split():
        if draw.textlength((line + " " + word).strip(), font=font) > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    return lines + [line]


ph_h = H - 56
out = []  # [(frame index, caption, speed), how many 1/FPS steps]
for m, nxt in zip(marks, marks[1:]):
    t, step = m["t"], m["speed"] / FPS
    while t < nxt["t"]:
        key = (frame_at(t), m["caption"], m["speed"])
        if out and out[-1][0] == key:
            out[-1][1] += 1
        else:
            out.append([key, 1])
        t += step

def phone_frame(shot, caption, speed):
    phone = shot.resize((round(shot.width * ph_h / shot.height), ph_h), Image.LANCZOS)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    x, y = 120, 28
    d.rounded_rectangle((x - 12, y - 12, x + phone.width + 12, y + ph_h + 12), 34, fill="#101210", outline="#4a564d", width=2)
    mask = Image.new("L", phone.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, phone.width - 1, ph_h - 1), 24, fill=255)
    img.paste(phone, (x, y), mask)
    tx = x + phone.width + 70
    d.text((tx, 200), "vmforai phone", font=small, fill="#a6e22e")
    ty = 236
    for line in wrap(caption, big, W - tx - 50, d):
        d.text((tx, ty), line, font=big, fill="#f8f8f2")
        ty += 42
    if speed > 1:
        d.text((tx, ty + 14), f"▶▶ {speed}× speed", font=small, fill="#9ba79d")
    return img


def window_frame(shot, caption, speed):
    """A browser window (a title bar with the page's address) and the caption below it."""
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    bar, x, y = 30, 24, 14
    w = W - 2 * x
    page = shot.resize((w, round(shot.height * w / shot.width)), Image.LANCZOS)
    d.rounded_rectangle((x, y, x + w, y + bar + page.height), 10, fill="#e8e8e6")
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        d.ellipse((x + 12 + i * 20, y + 9, x + 24 + i * 20, y + 21), fill=c)
    d.rounded_rectangle((x + 90, y + 5, x + w - 90, y + bar - 5), 6, fill="#ffffff")
    d.text((x + 102, y + 7), title, font=small, fill="#555555")
    img.paste(page, (x, y + bar))
    text = caption + (f"   ▶▶ {speed}× speed" if speed > 1 else "")
    d.text((W // 2, y + bar + page.height + (H - y - bar - page.height) // 2), text, font=mid, fill="#f8f8f2",
           anchor="mm")
    return img


files = []
for n, ((i, caption, speed), count) in enumerate(out):
    shot = Image.open(f"{src}/frames/{i:05d}.jpg").convert("RGB")
    img = (window_frame if layout == "window" else phone_frame)(shot, caption, speed)
    files.append((f"{src}/v{n:04d}.png", count * 100 // FPS))
    img.save(files[-1][0])

args = ["magick"]
for f, delay in files:
    args += ["-delay", str(delay), f]
subprocess.run(args + ["-loop", "0", gif], check=True)
print(gif, len(files), "frames,", sum(d for _, d in files) / 100, "s")
