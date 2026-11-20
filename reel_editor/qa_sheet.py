"""QA: contact sheet of the final video at each visual event (mid-point), labelled.
usage: python -m reel_editor.qa_sheet final.mp4 visuals.json out.jpg"""
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts", "Rubik-Bold.ttf")


def make(video, plan, out):
    f = ImageFont.truetype(FONT, 22)
    tiles = []
    for e in plan:
        t = e["start"] + min(0.6, e["dur"] / 2) if e["type"] in ("sticker", "text", "emoji") else e["start"] + e["dur"] / 2
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf",
                              "scale=216:384", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        im = Image.frombytes("RGB", (216, 384), raw) if len(raw) == 216 * 384 * 3 else Image.new("RGB", (216, 384))
        tile = Image.new("RGB", (216, 430))
        tile.paste(im, (0, 0))
        d = ImageDraw.Draw(tile)
        lab = e.get("text") or e.get("label") or e.get("title") or ""
        if e["type"] == "emoji":
            lab = " ".join(f"U+{ord(c):X}" for c in str(e.get("emoji", "")) if ord(c) > 0x2000)
        d.text((4, 388), f"{e['start']:.1f} {e['type']}", font=f, fill=(255, 255, 0))
        d.text((4, 408), str(lab)[:20], font=f, fill=(255, 255, 255))
        tiles.append(tile)
    if not tiles:
        return None
    cols = min(8, len(tiles))
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (216 * cols, 430 * rows))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * 216, (k // cols) * 430))
    sheet.save(out, quality=85)
    return out


if __name__ == "__main__":
    make(sys.argv[1], json.load(open(sys.argv[2])), sys.argv[3])
