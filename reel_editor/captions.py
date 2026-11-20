#!/usr/bin/env python3
"""Animated word-by-word captions, rendered as a transparent overlay track (.mov, qtrle with alpha).

Layout (1080x1920 grid): centred block ~69% down, one line preferred, two max; each caption fills the width
"minus one step"; key words in the theme's accent colour; the spoken word highlighted (a rounded box, or it grows).
Each video gets ONE theme (consistent inside the video), rotated between videos so reels look similar but not
identical.

Input JSON (list of captions, FINAL-timeline seconds):
  [{"start": s, "end": s, "words": [{"w": "Then", "s": 1.0, "e": 1.2, "key": false}, ...]}, ...]
--hide <json list of [s, e]> hides captions while full-screen B-roll covers the speaker.
"""
import json, os, subprocess, sys, tempfile, argparse
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
FONT = os.path.join(FONTS, "Rubik-ExtraBold.ttf")
W, H = 1080, 1920
WHITE = (255, 255, 255, 255)
PINK = (255, 79, 163, 255)
TEAL = (30, 230, 184, 255)
CENTER_Y = 0.69 * H          # vertical centre of the caption block
SPACE_K = 1.0                # word-gap multiplier (looks with a growing active word use more)
MAX_W = 1030                 # widest line in the reference spans ~25..1055 px
MAX_SIZE = 96                # short lines get bigger text but not full width ("that's your gap." ~3/4 width)
SPLIT_CHARS = 40             # a 43-character line becomes 2 lines; up to ~39 characters stay on 1
LINE_GAP = 0.18              # extra line spacing (fraction of size)
FEATURES = ["-liga", "-clig"]  # no fi ligature
try:
    from PIL import features as _pf
    if not _pf.check("raqm"):
        FEATURES = None          # Pillow without libraqm (some Windows/Linux builds): plain layout
except Exception:
    FEATURES = None

# Every look keeps the same layout (position, sizing, 1-2 lines, white base, key words in a pop colour, spoken word
# highlighted); a video gets ONE look, rotated between videos.
#   font: (file, variable-weight name or None)   active: "box" = word on a rounded box, "scale" = word grows + colour
#   enter: how each caption appears - None (hard cut), "pop" (scale up), "rise" (slides up)
THEMES = {
    "classic":   {"font": ("Rubik-ExtraBold.ttf", None), "key": (255, 79, 163), "box": (30, 230, 184),
                  "active_text": (255, 79, 163), "active": "box", "enter": None},
    "sunrise":   {"font": ("Montserrat-VF.ttf", "ExtraBold"), "key": (255, 196, 46), "box": (255, 196, 46),
                  "active_text": (24, 24, 28), "active": "box", "enter": "pop"},
    "ocean":     {"font": ("Poppins-ExtraBold.ttf", None), "key": (64, 200, 255), "box": (64, 200, 255),
                  "active_text": (255, 255, 255), "active": "scale", "enter": "rise"},
    "citrus":    {"font": ("Rubik-VF.ttf", "Black"), "key": (150, 240, 70), "box": (150, 240, 70),
                  "active_text": (20, 30, 20), "active": "box", "enter": "pop"},
    "ember":     {"font": ("Montserrat-VF.ttf", "Black"), "key": (255, 128, 64), "box": (255, 128, 64),
                  "active_text": (255, 255, 255), "active": "box", "enter": "rise"},
    "violet":    {"font": ("Poppins-ExtraBold.ttf", None), "key": (190, 130, 255), "box": (255, 79, 163),
                  "active_text": (255, 255, 255), "active": "scale", "enter": "pop"},
}
# "lowkey" looks: small ALL-CAPS captions low in the frame (block centre ~85.5%), lines LEFT-aligned inside a centred
# block, tilted up ~2 degrees, plain white words (no key-word colour), the spoken word on a red box.
LOWKEY = {"align": "left", "caps": True, "center_y": 0.855, "max_w": 760, "max_size": 62, "split": 18, "rotate": 2.0,
          "key": (255, 255, 255), "active_text": (255, 255, 255), "active": "box", "lowkey": True}
THEMES.update({
    "lowkey_red":    dict(LOWKEY, font=("Rubik-Italic-VF.ttf", "ExtraBold Italic"), box=(232, 32, 42), enter=None),
    "lowkey_plain":  dict(LOWKEY, font=("Rubik-VF.ttf", "Medium"), box=(232, 32, 42), enter=None, rotate=1.5),
    "lowkey_rose":   dict(LOWKEY, font=("Montserrat-Italic-VF.ttf", "ExtraBold Italic"), box=(226, 36, 72), enter="pop"),
    "lowkey_bold":   dict(LOWKEY, font=("Poppins-BoldItalic.ttf", None), box=(214, 25, 37), enter="rise", max_size=58),
    "lowkey_cherry": dict(LOWKEY, font=("Montserrat-Italic-VF.ttf", "Black Italic"), box=(200, 16, 46), enter="pop",
                          rotate=2.5),
})
THEME = THEMES["classic"]

def apply_theme(name):
    global THEME, FONT, PINK, TEAL, MAX_W, SPACE_K, CENTER_Y, MAX_SIZE, SPLIT_CHARS
    THEME = THEMES.get(name, THEMES["classic"])
    if THEME.get("lowkey"):
        CENTER_Y, MAX_W, MAX_SIZE, SPLIT_CHARS = THEME["center_y"] * H, THEME["max_w"], THEME["max_size"], THEME["split"]
    if THEME["active"] == "scale":   # the growing word needs room: wider word gaps, a little less line width
        MAX_W, SPACE_K = 960, 1.6
    FONT = os.path.join(FONTS, THEME["font"][0])
    PINK = THEME["key"] + (255,); TEAL = THEME["box"] + (255,)

_FONTS = {}
def font(size):
    k = (FONT, size)
    if k not in _FONTS:
        f = ImageFont.truetype(FONT, size)
        if THEME["font"][1]:
            f.set_variation_by_name(THEME["font"][1])
        _FONTS[k] = f
    return _FONTS[k]

def text_w(f, s):
    try:
        return f.getlength(s, features=FEATURES)
    except Exception:
        return f.getlength(s)

def line_width(f, words):
    return text_w(f, " ".join(words)) + text_w(f, " ") * (SPACE_K - 1) * max(0, len(words) - 1)

def fit_size(lines):
    lo, hi = 20, MAX_SIZE
    while lo < hi:
        mid = (lo + hi + 1) // 2
        f = font(mid)
        if max(line_width(f, l) for l in lines) <= MAX_W:
            lo = mid
        else:
            hi = mid - 1
    return lo

def layout(words):
    one = [words]
    if len(" ".join(words)) < SPLIT_CHARS or len(words) < 3:
        return one, fit_size(one)
    # balanced two-line split: first line slightly longer or equal
    best = None
    for k in range(1, len(words)):
        lines = [words[:k], words[k:]]
        s = fit_size(lines)
        f = font(s)
        diff = abs(line_width(f, lines[0]) - line_width(f, lines[1]))
        score = (s, -diff)
        if best is None or score > best[0]:
            best = (score, lines, s)
    return best[1], best[2]

def render(cap, active_idx, path, enter=1.0):
    toks = [w["w"].upper() if THEME.get("caps") else w["w"] for w in cap["words"]]
    lines, size = layout(toks)
    f = font(size)
    asc, desc = f.getmetrics()
    lh = int(size * (1 + LINE_GAP))
    block_h = lh * len(lines)
    y0 = int(CENTER_Y - block_h / 2)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ds = ImageDraw.Draw(shadow)
    sp = text_w(f, " ") * SPACE_K
    idx = 0
    boxes = []
    block_w = max(line_width(f, l) for l in lines)
    for li, line in enumerate(lines):
        lw = line_width(f, line)
        x = (W - block_w) / 2 if THEME.get("align") == "left" else (W - lw) / 2
        y = y0 + li * lh
        for tok in line:
            ww = text_w(f, tok)
            meta = cap["words"][idx]
            color = PINK if (meta.get("key") or idx == active_idx) else WHITE  # key words in the accent colour
            if THEME.get("lowkey") and idx != active_idx:
                color = WHITE   # lowkey: plain white words, only the spoken word is marked
            if idx == active_idx and THEME["active"] == "box":
                color = THEME["active_text"] + (255,)
                pad_x, pad_y = size * 0.10, size * 0.06
                top = y + size * 0.10 - pad_y
                bot = y + asc - size * 0.02 + pad_y + desc * 0.5
                boxes.append((x - pad_x, top, x + ww + pad_x, bot))
            if idx == active_idx and THEME["active"] == "scale":
                # spoken word grows ~15% in the key colour, drawn on its own so the line does not reflow
                big = font(int(size * 1.12)); bw = text_w(big, tok)
                bx, by = x + ww / 2 - bw / 2, y - size * 0.10
                ds.text((bx + 3, by + 4), tok, font=big, fill=(0, 0, 0, 190), features=FEATURES)
                d.text((bx, by), tok, font=big, fill=TEAL, features=FEATURES)
                x += ww + sp; idx += 1
                continue
            ds.text((x + 3, y + 4), tok, font=f, fill=(0, 0, 0, 170), features=FEATURES)
            d.text((x, y), tok, font=f, fill=color, features=FEATURES)
            x += ww + sp
            idx += 1
    shadow = shadow.filter(ImageFilter.GaussianBlur(4))
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    base.alpha_composite(shadow)
    bd = ImageDraw.Draw(base)
    for b in boxes:
        bd.rounded_rectangle(b, radius=max(6, size // 6), fill=TEAL)
    base.alpha_composite(img)
    if THEME.get("rotate"):   # lowkey: the caption block tilts up to the right
        base = base.rotate(THEME["rotate"], resample=Image.BICUBIC, center=(W / 2, CENTER_Y))
    if enter != 1.0:   # caption entry animation frame: scaled ("pop") or offset+faded ("rise") around its centre
        if THEME["enter"] == "pop":
            sc = enter; w2, h2 = int(W * sc), int(H * sc)
            small = base.resize((w2, h2), Image.BICUBIC)
            base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            base.alpha_composite(small, (int((W - w2) / 2), int(CENTER_Y - CENTER_Y * sc)))
        elif THEME["enter"] == "rise":
            off = int((1 - enter) * 120); a = base.split()[3].point(lambda v: int(v * enter))
            base.putalpha(a); moved = Image.new("RGBA", (W, H), (0, 0, 0, 0)); moved.alpha_composite(base, (0, off))
            base = moved
    base.save(path)

def build(caps, duration, out, hide=()):
    tmp = tempfile.mkdtemp(prefix="caps")
    blank = os.path.join(tmp, "blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    events = []  # (start, end, png)
    n = 0
    for c in caps:
        ws = c["words"]
        for i, w in enumerate(ws):
            s = c["start"] if i == 0 else w["s"]
            e = ws[i + 1]["s"] if i + 1 < len(ws) else c["end"]
            if e <= s:
                continue
            if i == 0 and THEME["enter"] and e - s > 0.2:
                steps = (0.78, 0.9, 0.97) if THEME["enter"] == "pop" else (0.35, 0.7, 0.92)
                for st in steps:   # three 1/30 s entry frames, then the normal frame
                    p = os.path.join(tmp, f"c{n:05d}.png"); n += 1
                    render(c, i, p, enter=st)
                    events.append((s, s + 1 / 30, p)); s += 1 / 30
            p = os.path.join(tmp, f"c{n:05d}.png"); n += 1
            render(c, i, p)
            events.append((s, e, p))
    # cut out hidden spans (B-roll) by splitting events
    def visible(s, e):
        parts = [(s, e)]
        for hs, he in hide:
            nxt = []
            for a, b in parts:
                if he <= a or hs >= b:
                    nxt.append((a, b))
                else:
                    if a < hs: nxt.append((a, hs))
                    if he < b: nxt.append((he, b))
            parts = nxt
        return parts
    ev = []
    for s, e, p in events:
        for a, b in visible(s, e):
            if b - a > 0.001:
                ev.append((a, b, p))
    ev.sort()
    # concat list with blanks filling gaps
    lst = os.path.join(tmp, "list.txt")
    t = 0.0
    with open(lst, "w") as fh:
        for s, e, p in ev:
            if s > t + 1e-3:
                fh.write(f"file '{blank}'\nduration {s - t:.4f}\n")
            fh.write(f"file '{p}'\nduration {e - max(s, t):.4f}\n")
            t = max(t, e)
        if duration > t:
            fh.write(f"file '{blank}'\nduration {duration - t:.4f}\n")
        fh.write(f"file '{blank}'\n")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-vf", "fps=30,format=argb", "-t", f"{duration:.3f}", "-c:v", "qtrle", out], check=True)
    for fn in os.listdir(tmp):
        os.remove(os.path.join(tmp, fn))
    os.rmdir(tmp)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("captions_json"); ap.add_argument("duration", type=float); ap.add_argument("out")
    ap.add_argument("--hide", default="[]")
    ap.add_argument("--theme", default="classic", choices=sorted(THEMES))
    ap.add_argument("--preview", help="render one caption (index) with first word active to PNG")
    a = ap.parse_args()
    apply_theme(a.theme)
    caps = json.load(open(a.captions_json))
    if a.preview:
        i = int(a.preview); render(caps[i], 1, a.out); sys.exit()
    build(caps, a.duration, a.out, json.load(open(a.hide)) if a.hide.endswith(".json") else json.loads(a.hide))
