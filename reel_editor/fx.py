#!/usr/bin/env python3
"""Animated stickers, kinetic text cards, emoji stickers and top titles, rendered as one transparent overlay
track (.mov, qtrle with alpha). The pipeline plans the events; this file draws them. Zooms and colour filters are
done elsewhere (camera.py / ffmpeg).

Events (final-timeline seconds):
  {"type": "sticker", "start": s, "dur": d, "icon": "piggy-bank", "label": "SAVE", "side": "left"|"right",
   "color": "pink"|"teal"|"gold"|"white"}
  {"type": "text", "start": s, "dur": d, "text": "$50 A MONTH", "style": "pink"|"teal"|"white"}
  {"type": "emoji", "start": s, "dur": d, "emoji": "\U0001F4B0", "side": "left"|"right"}
  {"type": "title", "start": s, "dur": d, "text": "Is this worth it?", "style": "white"|"red"}

Layout (1080x1920 selfie): face ~x 280-760, y 300-900; captions centred at 69% (y ~1325).
  stickers sit beside the head (y ~640), text cards above the head (y ~250), titles at the top (y ~215).
Every sticker/card pops in (scale overshoot + small rotation, 0.23 s) and pops out (0.13 s).
"""
import json, os, subprocess, sys, tempfile, argparse, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
FONT = os.path.join(FONTS, "Rubik-ExtraBold.ttf")
ICON_FONT = os.path.join(FONTS, "fa-solid-900.ttf")          # Font Awesome Free (solid)
ICONS = json.load(open(os.path.join(FONTS, "fa-icons.json")))
W, H, FPS = 1080, 1920, 30
COLORS = {"pink": (255, 79, 163), "teal": (30, 230, 184), "gold": (255, 196, 46), "white": (255, 255, 255)}
INK = (24, 24, 28)
POP_IN, POP_OUT = 7, 4            # frames
STICKER_Y, STICKER_X = 640, {"left": 178, "right": 902}
TEXT_Y = 250


def ease_in(k):
    """pop-in scale for frame k of POP_IN: 0.25 -> 1.12 overshoot -> 1.0"""
    t = (k + 1) / POP_IN
    c1, c3 = 1.9, 2.9
    return 0.25 + 0.75 * (1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2)


def shadowed(el, blur=10, off=(0, 8), alpha=150):
    """element RGBA -> RGBA with a soft drop shadow, padded"""
    pad = blur * 3
    out = Image.new("RGBA", (el.width + 2 * pad, el.height + 2 * pad), (0, 0, 0, 0))
    a = el.split()[3].point(lambda v: v * alpha // 255)
    sh = Image.new("RGBA", el.size, (0, 0, 0, 255)); sh.putalpha(a)
    out.alpha_composite(sh, (pad + off[0], pad + off[1]))
    out = out.filter(ImageFilter.GaussianBlur(blur))
    out.alpha_composite(el, (pad, pad))
    return out


def sticker_el(icon, label, color):
    col = COLORS.get(color, COLORS["pink"])
    D, ring = 236, 11
    el = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    d = ImageDraw.Draw(el)
    d.ellipse((0, 0, D - 1, D - 1), fill=(255, 255, 255, 255))
    d.ellipse((ring, ring, D - 1 - ring, D - 1 - ring), fill=col + (255,))
    glyph = chr(int(ICONS.get(icon, ICONS["star"]), 16))
    f = ImageFont.truetype(ICON_FONT, 118)
    bb = d.textbbox((0, 0), glyph, font=f)
    ic = INK if color == "white" or color == "gold" else (255, 255, 255)
    d.text(((D - (bb[2] - bb[0])) / 2 - bb[0], (D - (bb[3] - bb[1])) / 2 - bb[1]), glyph, font=f, fill=ic)
    if not label:
        return el
    lf = ImageFont.truetype(FONT, 50)
    tw = lf.getlength(label); px, py = 26, 14
    pw, ph = int(tw + 2 * px), 50 + 2 * py
    full = Image.new("RGBA", (max(D, pw), D + ph - 18), (0, 0, 0, 0))
    full.alpha_composite(el, ((full.width - D) // 2, 0))
    dd = ImageDraw.Draw(full)
    x0 = (full.width - pw) // 2; y0 = D - 18
    dd.rounded_rectangle((x0, y0, x0 + pw, y0 + ph), radius=ph // 2, fill=INK + (240,), outline=(255, 255, 255, 255),
                         width=5)
    lb = dd.textbbox((0, 0), label, font=lf)
    dd.text((x0 + (pw - (lb[2] - lb[0])) / 2 - lb[0], y0 + (ph - (lb[3] - lb[1])) / 2 - lb[1]), label, font=lf,
            fill=(255, 255, 255))
    return full


def text_el(text, style):
    text = text.upper()
    if style == "teal":
        fill, ink = COLORS["teal"], INK
    elif style == "white":
        fill, ink = (255, 255, 255), COLORS["pink"]
    else:
        fill, ink = COLORS["pink"], (255, 255, 255)
    size = 118
    while size > 50 and ImageFont.truetype(FONT, size).getlength(text) > 860:
        size -= 4
    f = ImageFont.truetype(FONT, size)
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bb = d0.textbbox((0, 0), text, font=f)
    px, py = 44, 26
    w, h = bb[2] - bb[0] + 2 * px, bb[3] - bb[1] + 2 * py
    el = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(el)
    d.rounded_rectangle((0, 0, w - 1, h - 1), radius=30, fill=fill + (255,))
    d.text((px - bb[0], py - bb[1]), text, font=f, fill=ink)
    return el.rotate(3, resample=Image.BICUBIC, expand=True)


# ---- emoji stickers (cut-out emoji, no badge) and plain top titles
TITLE_FONT = os.path.join(FONTS, "Poppins-SemiBold.ttf")
TITLE_Y = 215
EMOJI_POS = {"left": (215, 1060), "right": (865, 1060), "center": (540, 1080)}

def is_emoji(ch):
    o = ord(ch)
    return o >= 0x1F000 or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF or o in (0x200D, 0xFE0F)

def emoji_img(ch, px):
    from .emoji import emoji_image
    return emoji_image(ch.replace("\ufe0f", ""), px)

def emoji_el(chars):
    """1-2 emoji side by side, ~190 px, soft shadow (cut-out sticker look)"""
    parts = [c for c in chars if ord(c) >= 0x2600 and c not in ("\u200d", "\ufe0f")][:2]
    ims = [i for i in (emoji_img(c, 190) for c in parts) if i]
    if not ims:
        ims = [i for i in [emoji_img("\u2728", 190)] if i]
    if not ims:                       # offline and nothing cached: draw nothing rather than fail the render
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    w = sum(i.width for i in ims) + 20 * (len(ims) - 1); h = max(i.height for i in ims)
    el = Image.new("RGBA", (w, h), (0, 0, 0, 0)); x = 0
    for i in ims:
        el.alpha_composite(i, (x, (h - i.height) // 2)); x += i.width + 20
    return shadowed(el, blur=8, off=(0, 6), alpha=110)

def title_el(text, style):
    """platform-native looking text: sentence case, white with a thin black outline (or red with white), emoji inline,
    wrapped to 2 lines max"""
    size = 50
    f = ImageFont.truetype(TITLE_FONT, size)
    fill, stroke = ((235, 45, 58), (255, 255, 255)) if style == "red" else ((255, 255, 255), (0, 0, 0))
    def runs(t):   # [(is_emoji, text)]
        out = []
        for ch in t:
            e = is_emoji(ch)
            if out and out[-1][0] == e:
                out[-1] = (e, out[-1][1] + ch)
            else:
                out.append((e, ch))
        return out
    def width(t):
        return sum((size * 1.15 * len([c for c in r if ord(c) >= 0x2600 and c not in ("\u200d", "\ufe0f")])) if e
                   else f.getlength(r) for e, r in runs(t))
    words, lines, cur = [], [], ""
    for wd in text.split():   # an emoji stays on the line of the word before it
        if words and all(is_emoji(c) for c in wd):
            words[-1] += " " + wd
        else:
            words.append(wd)
    for wd in words:
        nxt = (cur + " " + wd).strip()
        if cur and width(nxt) > 940:
            lines.append(cur); cur = wd
        else:
            cur = nxt
    lines.append(cur)
    lines = lines[:2]
    lh = int(size * 1.25)
    el = Image.new("RGBA", (W, lh * len(lines) + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(el)
    for li, ln in enumerate(lines):
        x = (W - width(ln)) / 2; y = 10 + li * lh
        for e, r in runs(ln):
            if e:
                for ch in r:
                    if ord(ch) < 0x2600 or ch in ("\u200d", "\ufe0f"):
                        continue
                    im = emoji_img(ch, int(size * 1.05))
                    if im:
                        el.alpha_composite(im, (int(x), int(y + size * 0.12))); x += size * 1.15
            else:
                d.text((x, y), r, font=f, fill=fill, stroke_width=3, stroke_fill=stroke); x += f.getlength(r)
    return el

def place(frame, el, cx, cy, scale=1.0, rot=0.0, alpha=1.0):
    e = el
    if scale != 1.0:
        e = e.resize((max(1, int(e.width * scale)), max(1, int(e.height * scale))), Image.BICUBIC)
    if rot:
        e = e.rotate(rot, resample=Image.BICUBIC, expand=True)
    if alpha < 1.0:
        e = e.copy(); e.putalpha(e.split()[3].point(lambda v: int(v * alpha)))
    frame.alpha_composite(e, (int(cx - e.width / 2), int(cy - e.height / 2)))


def event_frames(ev):
    """-> list of (duration_sec, PIL frame) for one event"""
    if ev["type"] == "title":   # appears and disappears on a hard cut, like in-app text
        el = title_el(ev["text"], ev.get("style", "white"))
        fr = Image.new("RGBA", (W, H), (0, 0, 0, 0)); fr.alpha_composite(el, (0, int(TITLE_Y - el.height / 2)))
        return [(ev["dur"], fr)]
    if ev["type"] == "emoji":
        el = emoji_el(ev.get("emoji", "\u2728"))
        cx, cy = EMOJI_POS.get(ev.get("side", "right"), EMOJI_POS["right"])
        cx = min(max(cx, el.width / 2 + 8), W - el.width / 2 - 8)
        rot0 = -14 if ev.get("side") == "right" else 14
    elif ev["type"] == "sticker":
        el = shadowed(sticker_el(ev.get("icon", "star"), ev.get("label", ""), ev.get("color", "pink")))
        side = ev.get("side", "right")
        cx = STICKER_X.get(side, STICKER_X["right"])
        cx = min(max(cx, el.width / 2 + 8), W - el.width / 2 - 8)
        cy, rot0 = STICKER_Y, (-14 if side == "right" else 14)
    else:
        el = shadowed(text_el(ev["text"], ev.get("style", "pink")))
        cx, cy, rot0 = W / 2, TEXT_Y, -8
    out = []
    dur = ev["dur"]
    hold = dur - (POP_IN + POP_OUT) / FPS
    for k in range(POP_IN):
        fr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        place(fr, el, cx, cy, ease_in(k), rot0 * (1 - (k + 1) / POP_IN))
        out.append((1 / FPS, fr))
    fr = Image.new("RGBA", (W, H), (0, 0, 0, 0)); place(fr, el, cx, cy)
    out.append((max(1 / FPS, hold), fr))
    for k in range(POP_OUT):
        t = (k + 1) / POP_OUT
        fr = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        place(fr, el, cx, cy, 1 - 0.6 * t, 0, 1 - t)
        out.append((1 / FPS, fr))
    return out


def build(events, duration, out):
    tmp = tempfile.mkdtemp(prefix="fx")
    blank = os.path.join(tmp, "blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    lst = os.path.join(tmp, "list.txt")
    t, n = 0.0, 0
    with open(lst, "w") as fh:
        for ev in sorted(events, key=lambda e: e["start"]):
            if ev["start"] < t - 1e-3:
                continue  # overlapping events are refused upstream; never stack here
            if ev["start"] > t + 1e-3:
                fh.write(f"file '{blank}'\nduration {ev['start'] - t:.4f}\n")
                t = ev["start"]
            for d, fr in event_frames(ev):
                p = os.path.join(tmp, f"f{n:05d}.png"); n += 1
                fr.save(p, compress_level=1)
                fh.write(f"file '{p}'\nduration {d:.4f}\n")
                t += d
        if duration > t:
            fh.write(f"file '{blank}'\nduration {duration - t:.4f}\n")
        fh.write(f"file '{blank}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-vf", f"fps={FPS},format=argb", "-t", f"{duration:.3f}", "-c:v", "qtrle", out], check=True)
    for fn in os.listdir(tmp):
        os.remove(os.path.join(tmp, fn))
    os.rmdir(tmp)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("events_json"); ap.add_argument("duration", type=float); ap.add_argument("out")
    ap.add_argument("--preview", type=int, help="write the held frame of event N to OUT (png)")
    ap.add_argument("--theme", default="classic")
    a = ap.parse_args()
    # stickers/text cards follow the video's caption look: "pink" = its key colour, "teal" = its highlight colour
    from . import captions as _C
    _t = _C.load_theme(a.theme)
    if not _t.get("lowkey"):
        COLORS["pink"], COLORS["teal"] = tuple(_t["key"]), tuple(_t["box"])
    evs = json.load(open(a.events_json))
    if a.preview is not None:
        fr = event_frames(evs[a.preview])
        max(fr, key=lambda x: x[0])[1].save(a.out); sys.exit()
    build(evs, a.duration, a.out)
