#!/usr/bin/env python3
"""Dynamic on-screen text for LANDSCAPE (16:9) videos, rendered as a transparent overlay track (.mov, qtrle).

Landscape videos do not get word-by-word subtitles; they get frequent, relevant animated text instead (a new item
every few seconds where it fits): punch words, icon badges, key-takeaway cards, count-up numbers, tag lists and
numbered steps. Colours and fonts follow the video's own caption look (unique per video, see captions.make_look).

Input JSON (FINAL-timeline seconds):
  [{"type": "pop", "start": 3.1, "dur": 2.2, "text": "ONE TAKE"},
   {"type": "icon", "icon": "scissors", "text": "Cuts the mess-ups", ...},
   {"type": "callout", "text": "Runs on your regular plan"}, {"type": "stat", "value": 30, "suffix": "",
   "label": "posts a day"}, {"type": "chips", "items": ["TikTok", "YouTube"]}, {"type": "steps", "num": 2,
   "text": "Open BotFather"}]
usage: python -m reel_editor.dyntext events.json DURATION out.mov [--theme theme.json|name] [--preview N]
"""
import json, math, os, sys, tempfile, subprocess, argparse
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
W, H, FPS = 1920, 1080, 30
INK = (20, 12, 26); PAPER = (246, 240, 236); WHITE = (255, 255, 255)
A1, A2 = (255, 79, 163), (255, 196, 46)          # accent colours (replaced by the video's look)
HEAVY = ("Rubik-ExtraBold.ttf", None)            # punch words / numbers (replaced by the look's font)
ICON_NAMES = ["check", "x", "scissors", "play", "clock", "phone", "mic", "robot", "cloud", "upload", "download",
              "folder", "film", "sparkle", "bolt", "chat", "send", "gear", "lock", "shield", "dollar", "chart", "star",
              "link", "camera", "heart", "eye", "layers", "key", "rocket", "gift", "target", "brain", "globe",
              "captions"]
TYPES = ("pop", "icon", "callout", "stat", "chips", "steps")


_F = {}
def _font(size, spec=None):
    spec = spec or ("Rubik-ExtraBold.ttf", None)
    if isinstance(spec, str):
        spec = (spec, None)
    k = (spec, size)
    if k not in _F:
        f = ImageFont.truetype(os.path.join(FONTS, spec[0]), size)
        if spec[1]:
            try:
                f.set_variation_by_name(spec[1])
            except Exception:
                pass
        _F[k] = f
    return _F[k]


def tw(f, s):
    b = f.getbbox(s)
    return b[2] - b[0]


def ease(x):
    x = min(1.0, max(0.0, x))
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def back_out(x, k=1.70158):
    x = min(1.0, max(0.0, x)) - 1
    return 1 + (k + 1) * x ** 3 + k * x ** 2


# ---------------------------------------------------------------- icons (white line art on a 200x200 canvas)
def icon_img(name, S=200):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    W = WHITE + (255,); L = 13
    c = S / 2
    def line(*p, w=L): d.line(p, fill=W, width=w, joint="curve")
    def circ(x, y, r, fill=False, w=L):
        d.ellipse((x - r, y - r, x + r, y + r), outline=None if fill else W, fill=W if fill else None, width=w)
    def rrect(b, r=14, fill=False, w=L): d.rounded_rectangle(b, r, outline=None if fill else W, fill=W if fill else None, width=w)
    n = name
    if n == "check":
        line(52, 104, 86, 138, 150, 66, w=18)
    elif n == "x":
        line(60, 60, 140, 140, w=18); line(140, 60, 60, 140, w=18)
    elif n == "scissors":
        circ(66, 140, 22); circ(134, 140, 22); line(80, 122, 140, 40); line(120, 122, 60, 40)
    elif n == "play":
        d.polygon([(74, 52), (74, 148), (152, 100)], fill=W)
    elif n == "clock":
        circ(c, c, 64); line(c, c, c, 62); line(c, c, 130, 116)
    elif n == "phone":
        rrect((66, 34, 134, 166), 16); line(90, 146, 110, 146)
    elif n == "mic":
        rrect((78, 34, 122, 112), 22); d.arc((58, 70, 142, 140), 0, 180, fill=W, width=L); line(c, 140, c, 166); line(76, 166, 124, 166)
    elif n == "robot":
        rrect((50, 70, 150, 156), 20); circ(80, 110, 10, True); circ(120, 110, 10, True); line(84, 136, 116, 136)
        line(c, 70, c, 44); circ(c, 38, 9, True)
    elif n == "cloud":
        d.ellipse((40, 90, 100, 150), fill=W); d.ellipse((72, 56, 142, 126), fill=W); d.ellipse((110, 92, 164, 150), fill=W)
        d.rectangle((70, 112, 136, 150), fill=W)
    elif n in ("upload", "download"):
        if n == "upload":
            line(c, 140, c, 50); line(66, 84, c, 50, 134, 84)
        else:
            line(c, 50, c, 136); line(66, 102, c, 136, 134, 102)
        line(50, 160, 150, 160)
    elif n == "folder":
        d.polygon([(40, 64), (86, 64), (98, 78), (160, 78), (160, 150), (40, 150)], outline=W, width=L)
    elif n == "film":
        rrect((44, 54, 156, 146), 10)
        for x in (62, 86, 110, 134):
            d.rectangle((x - 5, 62, x + 5, 74), fill=W); d.rectangle((x - 5, 126, x + 5, 138), fill=W)
    elif n == "sparkle":
        for (x, y, r) in ((92, 100, 52), (146, 58, 22)):
            d.polygon([(x, y - r), (x + r * .25, y - r * .25), (x + r, y), (x + r * .25, y + r * .25), (x, y + r),
                       (x - r * .25, y + r * .25), (x - r, y), (x - r * .25, y - r * .25)], fill=W)
    elif n == "bolt":
        d.polygon([(112, 30), (58, 112), (96, 112), (84, 170), (142, 86), (104, 86)], fill=W)
    elif n == "chat":
        rrect((40, 50, 160, 132), 26); d.polygon([(70, 128), (66, 162), (100, 128)], fill=W)
    elif n == "send":
        d.polygon([(40, 98), (162, 44), (122, 160), (98, 112)], outline=W, width=L); line(98, 112, 162, 44)
    elif n == "gear":
        for k in range(8):
            a = k * math.pi / 4
            line(c + 44 * math.cos(a), c + 44 * math.sin(a), c + 70 * math.cos(a), c + 70 * math.sin(a), w=22)
        circ(c, c, 46); circ(c, c, 16, True)
    elif n == "lock":
        rrect((56, 92, 144, 164), 14, True); d.arc((70, 40, 130, 120), 180, 360, fill=W, width=L)
        line(70, 80, 70, 96); line(130, 80, 130, 96)
    elif n == "shield":
        d.polygon([(c, 34), (156, 58), (148, 118), (c, 168), (52, 118), (44, 58)], outline=W, width=L); line(76, 100, 94, 120, 128, 80)
    elif n == "dollar":
        d.text((c, c + 4), "$", fill=W, anchor="mm", font=_font(150, "Rubik-ExtraBold.ttf"))
    elif n == "chart":
        line(44, 156, 156, 156)
        for k, h in enumerate((40, 70, 104)):
            d.rectangle((58 + k * 34, 150 - h, 80 + k * 34, 150), fill=W)
        line(54, 92, 92, 70, 118, 82, 156, 40, w=9)
    elif n == "star":
        pts = []
        for k in range(10):
            r = 70 if k % 2 == 0 else 30
            a = -math.pi / 2 + k * math.pi / 5
            pts.append((c + r * math.cos(a), c + 6 + r * math.sin(a)))
        d.polygon(pts, fill=W)
    elif n == "link":
        rrect((34, 76, 112, 124), 24); rrect((88, 76, 166, 124), 24)
    elif n == "camera":
        rrect((36, 66, 164, 150), 16); circ(c, 108, 26); d.rectangle((76, 50, 124, 66), fill=W)
    elif n == "heart":
        d.ellipse((42, 52, 104, 114), fill=W); d.ellipse((96, 52, 158, 114), fill=W)
        d.polygon([(46, 96), (154, 96), (c, 160)], fill=W)
    elif n == "eye":
        d.chord((30, 56, 170, 144), 0, 360, outline=W, width=L); circ(c, c, 22, True)
    elif n == "layers":
        for k in range(3):
            y = 60 + k * 30
            d.polygon([(c, y), (160, y + 26), (c, y + 52), (40, y + 26)], outline=W, width=10)
    elif n == "key":
        circ(66, c, 30); line(96, c, 160, c); line(140, c, 140, 126); line(158, c, 158, 120)
    elif n == "rocket":
        d.polygon([(c, 30), (128, 80), (128, 136), (72, 136), (72, 80)], outline=W, width=L)
        circ(c, 92, 12, True); d.polygon([(72, 112), (48, 150), (72, 140)], fill=W); d.polygon([(128, 112), (152, 150), (128, 140)], fill=W)
        line(88, 150, 88, 170); line(112, 150, 112, 170)
    elif n == "gift":
        rrect((44, 84, 156, 160), 8); d.rectangle((40, 70, 160, 92), fill=W); line(c, 70, c, 160)
        d.arc((64, 40, 104, 80), 90, 360, fill=W, width=10); d.arc((96, 40, 136, 80), 180, 90, fill=W, width=10)
    elif n == "target":
        circ(c, c, 66); circ(c, c, 40); circ(c, c, 14, True)
    elif n == "brain":
        d.ellipse((44, 50, 110, 150), outline=W, width=L); d.ellipse((90, 50, 156, 150), outline=W, width=L)
        line(c, 60, c, 146); line(70, 92, 92, 92); line(108, 112, 130, 112)
    elif n == "globe":
        circ(c, c, 64); d.ellipse((72, 36, 128, 164), outline=W, width=9); line(38, c, 162, c, w=9)
    elif n == "captions":
        rrect((34, 54, 166, 146), 16); line(56, 104, 96, 104, w=11); line(108, 104, 144, 104, w=11); line(56, 126, 128, 126, w=11)
    else:   # fallback dot
        circ(c, c, 30, True)
    return im




# ---------------------------------------------------------------- sprites
def shadowed(im, blur=16, off=10, a=150):
    pad = 40
    out = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    alpha = im.split()[3].point(lambda v: v * a // 255)
    sh.paste(Image.new("RGBA", im.size, (0, 0, 0, 255)), (pad, pad + off), alpha)
    out = Image.alpha_composite(out, sh.filter(ImageFilter.GaussianBlur(blur)))
    out.alpha_composite(im, (pad, pad))
    return out


def card(w, h, r, outline=None):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=(18, 10, 24, 232),
                                         outline=outline or (255, 255, 255, 40), width=2)
    return im


def grad_circle(D, c0, c1):
    im = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    px = im.load()
    r = D / 2
    for y in range(D):
        for x in range(D):
            if (x - r + .5) ** 2 + (y - r + .5) ** 2 <= r * r:
                k = (x + y) / (2 * D)
                px[x, y] = tuple(int(c0[i] * (1 - k) + c1[i] * k) for i in range(3)) + (255,)
    return im


def spr_pop(text, k):
    f = _font(124, HEAVY)
    acc = A1 if k % 2 == 0 else A2
    text = text.upper()
    w = tw(f, text) + 90; h = 190
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((18, 150, w - 18, 170), 10, fill=acc + (255,))
    d.text((w / 2, 82), text, font=f, fill=WHITE + (255,), anchor="mm", stroke_width=7, stroke_fill=INK + (255,))
    return shadowed(im), None


def spr_badge(text, k, icon=None, num=None):
    D = 132
    c = grad_circle(D, A1, A2) if k % 2 == 0 else grad_circle(D, A2, A1)
    if num is not None:
        ImageDraw.Draw(c).text((D / 2, D / 2 + 2), str(num), font=_font(80, HEAVY), fill=WHITE + (255,), anchor="mm")
    else:
        ic = icon_img(icon if icon in ICON_NAMES else "sparkle").resize((84, 84), Image.LANCZOS)
        c.alpha_composite(ic, ((D - 84) // 2, (D - 84) // 2))
    f = _font(50, ("Rubik-Bold.ttf", None))
    tx = D // 2 + 38
    lw = tw(f, text) + tx + 44
    lab = card(lw, 92, 46)
    ImageDraw.Draw(lab).text((tx, 46), text, font=f, fill=PAPER + (255,), anchor="lm")
    im = Image.new("RGBA", (D // 2 + lw + 4, D + 4), (0, 0, 0, 0))
    im.alpha_composite(lab, (D // 2 - 10, (D - 92) // 2))
    ring = Image.new("RGBA", (D + 4, D + 4), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, D + 3, D + 3), fill=(255, 255, 255, 60))
    im.alpha_composite(ring, (0, 0)); im.alpha_composite(c, (2, 2))
    return shadowed(im), D + 40 + 40            # sprite, where the label starts (sprite px, incl. shadow pad)


def spr_callout(text):
    f = _font(52, ("Rubik-Bold.ttf", None))
    w = tw(f, text) + 110; h = 104
    im = card(w, h, 24)
    d = ImageDraw.Draw(im)
    for y in range(16, h - 16):
        kk = (y - 16) / (h - 33)
        d.line((22, y, 29, y), fill=tuple(int(A1[i] * (1 - kk) + A2[i] * kk) for i in range(3)) + (255,))
    d.text((56, h / 2), text, font=f, fill=PAPER + (255,), anchor="lm")
    return shadowed(im), None


def spr_stat(full, txt, label):
    fn, fl = _font(150, HEAVY), _font(46, ("Rubik-Bold.ttf", None))
    w = max(tw(fn, full), tw(fl, label)) + 120; h = 270
    im = card(w, h, 30)
    d = ImageDraw.Draw(im)
    d.text((w / 2, 112), txt, font=fn, fill=A2 + (255,), anchor="mm")
    d.text((w / 2, 220), label, font=fl, fill=PAPER + (255,), anchor="mm")
    return shadowed(im)


def spr_chip(text, k):
    f = _font(44, ("Rubik-Bold.ttf", None))
    w = tw(f, text) + 64; h = 78
    im = card(w, h, 39, outline=(A1 if k % 2 == 0 else A2) + (210,))
    ImageDraw.Draw(im).text((w / 2, h / 2), text, font=f, fill=PAPER + (255,), anchor="mm")
    return shadowed(im, blur=10, off=6)


# ---------------------------------------------------------------- one event -> frames
ANCHORS = {"pop": ["low_center", "high_center"], "icon": ["top_left", "low_left", "top_right", "low_right"],
           "steps": ["top_left", "low_left"], "callout": ["low_left", "low_right"],
           "stat": ["mid_left", "mid_right"], "chips": ["low_center", "high_center"]}


def anchor_xy(name, w, h):
    m = -6
    return {"low_center": ((W - w) / 2, H - h - m - 20), "high_center": ((W - w) / 2, m + 40),
            "low_left": (m, H - h - m), "low_right": (W - w - m, H - h - m), "top_left": (m, m + 10),
            "top_right": (W - w - m, m + 10), "mid_left": (m + 20, (H - h) / 2),
            "mid_right": (W - w - m - 20, (H - h) / 2)}[name]


def paste(canvas, im, x, y, alpha=1.0, scale=1.0, rot=0.0):
    if alpha <= 0.01:
        return
    if abs(scale - 1) > 1e-3 or abs(rot) > 1e-3:
        cw, ch = im.size
        im = im.resize((max(1, int(cw * scale)), max(1, int(ch * scale))), Image.BICUBIC)
        if rot:
            im = im.rotate(rot, resample=Image.BICUBIC, expand=True)
        x += (cw - im.width) / 2; y += (ch - im.height) / 2
    if alpha < 0.999:
        im = im.copy(); im.putalpha(im.split()[3].point(lambda v: int(v * alpha)))
    canvas.alpha_composite(im, (int(round(x)), int(round(y))))


class Event:
    def __init__(self, ev, k):
        self.ev, self.k, self.ty = ev, k, ev["type"]
        self.start, self.dur = float(ev["start"]), float(ev["dur"])
        ty = self.ty
        if ty == "pop":
            self.sp, _ = spr_pop(ev["text"], k)
        elif ty in ("icon", "steps"):
            self.sp, self.reveal = spr_badge(ev["text"], k, icon=ev.get("icon"),
                                             num=ev.get("num") if ty == "steps" else None)
        elif ty == "callout":
            self.sp, _ = spr_callout(ev["text"])
        elif ty == "stat":
            v = ev["value"]
            self.full = f"{ev.get('prefix', '')}{v}{ev.get('suffix', '')}"
            self.sp = spr_stat(self.full, self.full, ev.get("label", ""))
        elif ty == "chips":
            self.chips = [spr_chip(s, k + i) for i, s in enumerate(ev["items"][:6])]
            self.gap = -40
            wsum = sum(c.width for c in self.chips) + self.gap * (len(self.chips) - 1)
            self.sp = Image.new("RGBA", (wsum, max(c.height for c in self.chips)), (0, 0, 0, 0))
        names = ANCHORS[ty]
        self.anchor = names[(k // 2) % len(names)] if ty in ("icon", "steps") else names[k % len(names)]
        self.x, self.y = anchor_xy(self.anchor, *self.sp.size)

    def frame(self, x):
        """the overlay at local time x (seconds into the event)"""
        cv = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        out = ease((self.dur - x) / 0.25)
        X, Y, ty = self.x, self.y, self.ty
        if ty == "pop":
            s = (0.55 + 0.45 * back_out(x / 0.32, 2.2)) * (1 - 0.08 * (1 - out))
            paste(cv, self.sp, X, Y, min(1.0, x / 0.12) * out, s, (2.5 if self.k % 2 else -3.0) * min(1.0, x / 0.32))
        elif ty in ("icon", "steps"):
            cx = self.reveal
            s = 0.4 + 0.6 * back_out(x / 0.3, 2.0)
            rv = ease((x - 0.12) / 0.38)
            cut = int(cx + (self.sp.width - cx) * rv)
            if cut > cx:
                paste(cv, self.sp.crop((cx, 0, cut, self.sp.height)), X + cx, Y, out)
            paste(cv, self.sp.crop((0, 0, cx, self.sp.height)), X, Y, min(1.0, x / 0.1) * out, s)
        elif ty == "callout":
            a = ease(x / 0.35)
            paste(cv, self.sp, X - 60 * (1 - a), Y, a * out)
        elif ty == "stat":
            v = self.ev["value"]; e = ease(x / 0.9)
            cur = int(round(v * e)) if isinstance(v, int) else round(v * e, 1)
            im = spr_stat(self.full, f"{self.ev.get('prefix', '')}{cur}{self.ev.get('suffix', '')}",
                          self.ev.get("label", ""))
            paste(cv, im, X, Y, min(1.0, x / 0.12) * out, 0.7 + 0.3 * back_out(x / 0.3, 1.8))
        elif ty == "chips":
            cx = X
            for i, c in enumerate(self.chips):
                xi = x - 0.16 * i
                if xi > 0:
                    paste(cv, c, cx, Y + 18 * (1 - ease(xi / 0.28)), min(1.0, xi / 0.1) * out,
                          0.5 + 0.5 * back_out(xi / 0.28, 2.0))
                cx += c.width + self.gap
        return cv

    def schedule(self):
        """(local start, local end) spans: every frame while animating, one held frame in between"""
        anim_in = 0.9 if self.ty == "stat" else (0.16 * len(self.chips) + 0.3 if self.ty == "chips" else 0.5)
        spans, t, f = [], 0.0, 1 / FPS
        while t < min(anim_in, self.dur - 0.27):
            spans.append((t, t + f)); t += f
        hold_end = self.dur - 0.27
        if hold_end > t:
            spans.append((t, hold_end)); t = hold_end
        while t < self.dur - 1e-6:
            spans.append((t, min(self.dur, t + f))); t += f
        return spans


def apply_look(look):
    """stickers follow the video's caption look: accent colours + heavy font"""
    global A1, A2, HEAVY
    if not look:
        return
    if not look.get("lowkey"):
        A1, A2 = tuple(look["key"]), tuple(look["box"])
    else:
        A1, A2 = tuple(look["box"]), (255, 255, 255)
    HEAVY = tuple(look["font"]) if isinstance(look.get("font"), (list, tuple)) else HEAVY


def clean(events, duration):
    """valid types only, sorted, no overlaps, inside the video"""
    out, end = [], -1.0
    for e in sorted(events, key=lambda e: float(e.get("start", 0))):
        if e.get("type") not in TYPES:
            continue
        if e["type"] == "chips" and not e.get("items"):
            continue
        if e["type"] != "chips" and e["type"] != "stat" and not str(e.get("text", "")).strip():
            continue
        if e["type"] == "stat" and not isinstance(e.get("value"), (int, float)):
            continue
        s = max(float(e["start"]), end + 0.25)
        d = min(3.4, max(1.4, float(e.get("dur", 2.4))))
        if s + d > duration - 0.1:
            continue
        e = dict(e, start=s, dur=d)
        out.append(e); end = s + d
    return out


def build(events, duration, out):
    tmp = tempfile.mkdtemp(prefix="dyn")
    blank = os.path.join(tmp, "blank.png")
    Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(blank)
    ev, n = [], 0
    for k, e in enumerate(clean(events, duration)):
        E = Event(e, k)
        for a, b in E.schedule():
            p = os.path.join(tmp, f"d{n:06d}.png"); n += 1
            E.frame(a).save(p, compress_level=1)
            ev.append((E.start + a, E.start + b, p))
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
                    "-vf", f"fps={FPS},format=argb", "-t", f"{duration:.3f}", "-c:v", "qtrle", out], check=True)
    for fn in os.listdir(tmp):
        os.remove(os.path.join(tmp, fn))
    os.rmdir(tmp)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("events_json"); ap.add_argument("duration", type=float); ap.add_argument("out")
    ap.add_argument("--theme", default="classic", help="theme.json path or a look name")
    ap.add_argument("--preview", type=int, help="write event N at 1.0 s to OUT (png)")
    a = ap.parse_args()
    from . import captions as _C
    apply_look(_C.load_theme(a.theme))
    evs = json.load(open(a.events_json))
    if a.preview is not None:
        e = clean(evs, 1e9)[a.preview]
        Event(e, a.preview).frame(1.0).save(a.out); sys.exit()
    build(evs, a.duration, a.out)
