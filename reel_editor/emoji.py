"""Colour emoji images, cross-platform (no Apple Color Emoji needed).

OpenMoji (https://openmoji.org, CC BY-SA 4.0) 618x618 PNGs are downloaded once per emoji and cached;
Twemoji (https://github.com/jdecked/twemoji, graphics CC BY 4.0) 72x72 PNGs are the fallback.
Attribution is in THIRD-PARTY-LICENSES.md; keep it if you publish videos that use emoji stickers.
"""
import os
import urllib.request

from . import config as C

OPENMOJI = "https://cdn.jsdelivr.net/gh/hfg-gmuend/openmoji@15.1.0/color/618x618/{}.png"
TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{}.png"


def codepoints(ch, upper=True, keep_fe0f=False):
    cps = [f"{ord(c):X}" if upper else f"{ord(c):x}" for c in ch if keep_fe0f or ord(c) != 0xFE0F]
    return "-".join(cps)


def emoji_file(ch):
    """local PNG path for one emoji (downloaded on first use), or None when offline/unknown"""
    os.makedirs(C.EMOJI_CACHE, exist_ok=True)
    key = codepoints(ch)
    for name, url in ((f"om-{key}.png", OPENMOJI.format(key)), (f"tw-{key.lower()}.png", TWEMOJI.format(key.lower()))):
        p = os.path.join(C.EMOJI_CACHE, name)
        if os.path.exists(p) and os.path.getsize(p) > 100:
            return p
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ai-reel-editor/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            if data[:4] == b"\x89PNG":
                with open(p, "wb") as fh:
                    fh.write(data)
                return p
        except Exception:
            continue
    return None


def emoji_image(ch, px):
    """PIL RGBA image of the emoji, longest side px, trimmed; None if unavailable"""
    from PIL import Image
    p = emoji_file(ch)
    if not p:
        return None
    im = Image.open(p).convert("RGBA")
    bb = im.getbbox()
    if bb:
        im = im.crop(bb)
    k = px / max(im.width, im.height)
    return im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.LANCZOS)
