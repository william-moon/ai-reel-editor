"""Style learning: study a creator's own finished edits and write THEIR rulebook (STYLE.md).

  python -m reel_editor learn-style "<folder with 3-10 finished reels>" --name my-style [--activate]

What it measures in each finished video (nothing transcript-like is kept: words are only counted):
  - format: resolution, fps, length
  - rhythm: hard cuts / visual changes per 10 s and the median time between them (ffmpeg scene detection)
  - pacing: words per minute and the median pause between words (speech recognition, counts only)
  - loudness (LUFS)
  - looks: a labelled contact sheet of frames every ~1.5 s (caption font/colour/position, B-roll share,
    stickers, emoji, text cards, zooms, filters, titles, logo)
Claude (smart model) then looks at the sheets + numbers and writes styles/<name>/STYLE.md in the same format as
styles/default/STYLE.md: a plain-English rulebook every future edit follows, plus the JSON settings the code
enforces. Edit the file by hand any time; re-run to refresh it.
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import tempfile

from . import brain
from . import config as C
from .storage import VIDEO_EXT

MAX_SHEETS = 6


def probe(p):
    d = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                            "format=duration:stream=codec_type,width,height,avg_frame_rate",
                                            "-of", "json", p]))
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    n, dd = v["avg_frame_rate"].split("/")
    return float(d["format"]["duration"]), int(v["width"]), int(v["height"]), round(float(n) / float(dd or 1), 2)


def scene_cuts(p, thr=0.30):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", p, "-an", "-vf",
                          f"scale=270:-2,select='gt(scene,{thr})',showinfo", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    return [float(x) for x in re.findall(r"pts_time:([0-9.]+)", err)]


def loudness(p):
    e = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", p, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[0-9.]+) LUFS", e)
    return float(m[-1]) if m else None


def pacing(p, tmp):
    """words per minute + median pause, from speech recognition (words are counted, never stored)"""
    try:
        from . import transcribe as TR
        from . import audio as A
        wav = os.path.join(tmp, "a.wav")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", p, "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
        isl = A.speech_islands(wav, min_sil=0.6)
        files = []
        for k, (a, b) in enumerate(isl):
            f = os.path.join(tmp, f"c{k}.wav")
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", wav, f],
                           check=True)
            files.append(f)
        words = TR.transcribe_islands(isl, files, log=lambda *a: None)
        for f in files + [wav]:
            os.remove(f)
        if len(words) < 5:
            return {}
        span = words[-1]["e"] - words[0]["s"]
        gaps = [b["s"] - a["e"] for a, b in zip(words, words[1:])]
        n = len(words)
        del words
        return {"words_per_min": round(n / max(1e-6, span) * 60), "median_pause_sec": round(statistics.median(gaps), 3),
                "pauses_over_0.4s": sum(1 for g in gaps if g > 0.4)}
    except Exception as ex:
        return {"pacing_error": str(ex)[:80]}


def frame_sheet(p, out, dur, step=1.5, cols=6, max_frames=24):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(os.path.join(C.FONTS, "Rubik-Bold.ttf"), 18)
    n = min(max_frames, max(1, int(dur / step)))
    step = dur / n
    tiles = []
    for k in range(n):
        t = (k + 0.5) * step
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", p, "-frames:v", "1", "-vf",
                              "scale=180:320:force_original_aspect_ratio=decrease,pad=180:320:(ow-iw)/2:(oh-ih)/2",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        im = Image.frombytes("RGB", (180, 320), raw) if len(raw) == 180 * 320 * 3 else Image.new("RGB", (180, 320))
        tile = Image.new("RGB", (180, 344))
        tile.paste(im)
        ImageDraw.Draw(tile).text((4, 322), f"{t:.1f}s", font=f, fill=(255, 255, 0))
        tiles.append(tile)
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (180 * cols, 344 * rows))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * 180, (k // cols) * 344))
    sheet.save(out, quality=85)
    return out


PROMPT = """You are an expert short-form video editor. The images are contact sheets of a creator's OWN finished,
posted vertical reels (frames every ~1.5 s, time under each frame). Below are measurements of those same videos.
Study them and write this creator's personal edit rulebook, so an automated editor can edit their future raw
recordings exactly the way they edit.

Write it in EXACTLY this format (same structure as the template below): YAML front matter with name and description,
then plain-English sections (who the videos are for, cutting/pacing, captions: font look, case, colours, position,
words per caption, highlighting; visual rhythm: how often something changes and what kinds of things - full-screen
B-roll, half-screen B-roll, icon stickers, emoji, text cards, top titles, zooms, filters, logo; B-roll: what kind of
footage and 4-6 GOOD and BAD example pairings in the style of the template, invented to fit their niche; camera;
sound; anything distinctive), then ONE ```json settings block using ONLY keys from the template's settings and these
extra keys: event_types (subset of "broll","pip","sticker","text","emoji","zoom","bw","title"), caption_themes
(subset of AVAILABLE_THEMES; "lowkey_*" = small all-caps captions low in the frame with the spoken word on a red box;
the others = bold centred captions with coloured key words; each video gets its own variation of these), key_words (false if they never
colour key words), caption_words ([min, max] words per caption), hook_no_broll, cta_no_broll, event_max_gap,
event_target_gap, event_min_gap, broll_every_sec, zoom, zoom_push, output_scale (2 only if their videos are 4K),
dur_limits. Base every number on what you measured/saw; when unsure keep the template value.
Describe only editing style. Do NOT write down anything personal (names, brands, what they said, contact details).

NAME: STYLE_NAME

MEASUREMENTS:
STATS

TEMPLATE (styles/default/STYLE.md):
TEMPLATE
Reply with ONLY the finished STYLE.md content."""


def learn(folder, name, activate=False, max_videos=8, log=print):
    from .captions import THEMES
    vids = sorted(os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(VIDEO_EXT))[:max_videos]
    if not vids:
        raise SystemExit(f"no videos found in {folder}")
    tmp = tempfile.mkdtemp(prefix="learnstyle")
    stats, sheets = [], []
    for k, v in enumerate(vids):
        log(f"studying video {k + 1}/{len(vids)}")
        dur, w, h, fps = probe(v)
        cuts = scene_cuts(v)
        gaps = [b - a for a, b in zip([0.0] + cuts, cuts + [dur])]
        st = {"video": k + 1, "resolution": f"{w}x{h}", "fps": fps, "length_sec": round(dur, 1),
              "visual_changes_per_10s": round(len(cuts) / max(1, dur) * 10, 2),
              "median_sec_between_changes": round(statistics.median(gaps), 2) if gaps else None,
              "loudness_lufs": loudness(v)}
        st.update(pacing(v, tmp))
        stats.append(st)
        if len(sheets) < MAX_SHEETS:
            sheets.append(frame_sheet(v, os.path.join(tmp, f"sheet{k + 1}.jpg"), dur))
    with open(os.path.join(C.ROOT, "styles", "default", "STYLE.md"), encoding="utf-8") as fh:
        template = fh.read()
    prompt = (PROMPT.replace("STYLE_NAME", name).replace("STATS", json.dumps(stats, indent=1))
              .replace("TEMPLATE", template).replace("AVAILABLE_THEMES", ", ".join(sorted(THEMES))))
    log(f"writing the rulebook (Claude smart model, {len(sheets)} contact sheets)")
    md = brain.ask(prompt, model="opus", images=sheets)
    md = re.sub(r"^```(?:markdown|md)?\s*\n", "", md.strip())
    md = re.sub(r"\n```\s*$", "", md) if md.count("```") % 2 else md
    if not md.startswith("---"):
        md = f"---\nname: {name}\ndescription: learned from {len(vids)} finished reels\n---\n" + md
    out_dir = os.path.join(C.ROOT, "styles", re.sub(r"[^\w-]+", "-", name))
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "STYLE.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(md.strip() + "\n")
    with open(os.path.join(out_dir, "measurements.json"), "w") as fh:
        json.dump(stats, fh, indent=1)
    from .style import Style
    Style(out)          # validates the JSON block
    for f in os.listdir(tmp):
        os.remove(os.path.join(tmp, f))
    os.rmdir(tmp)
    if activate:
        C.set_env_value("STYLE_FILE", os.path.relpath(out, C.ROOT))
    log(f"style written: {out}" + (" (now the active style)" if activate else ""))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m reel_editor learn-style")
    ap.add_argument("folder")
    ap.add_argument("--name", default="my-style")
    ap.add_argument("--activate", action="store_true", help="make it the style every edit uses (STYLE_FILE in .env)")
    ap.add_argument("--max-videos", type=int, default=8)
    a = ap.parse_args(argv)
    learn(a.folder, a.name, a.activate, a.max_videos)


if __name__ == "__main__":
    main()
