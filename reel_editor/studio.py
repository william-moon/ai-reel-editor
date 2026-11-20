"""The reel editor pipeline: raw selfie recording -> finished vertical reel.

Steps (each writes a decision file in the work dir; an existing file is reused, so a person or the bot can review
or hand-edit any step and re-run; --redo STEP deletes that step's file and everything after it):
  1 transcribe  every stretch of speech transcribed on its own, word times snapped to the voice   words.json
  2 takes       Claude keeps ONLY the last good take of every line, drops slates/retake cues/off-script talk
                                                                                                 takes.json
  3 spans       dead air removed, every cut snapped to the measured voice edge so joins flow      spans.json
  4 sync        lip-sync offset (measured again on the real cut before the final render)          sync.json
  5 captions    Claude groups captions + applies the punctuation/key-word rules                   captions.json
  6 visuals     something new on screen every 3-4 s: B-roll (matched to the words, picked by Claude
                looking at thumbnails, then checked again by Claude), stickers, text cards, punch-ins,
                B&W; the rhythm is enforced in code                                               visuals.json
  7 render      speed-up, denoise, loudness, captions (hidden under B-roll), stickers, smooth punch-ins
                (locked framing + one eased push), logo, thumbnail; then an automatic final check   final .mp4

The creator's STYLE.md (see style.py / learn-style) is read by Claude before every decision and sets the numbers
the code enforces.

Usage: python -m reel_editor edit <input.mp4> [--thumb img] [--out final.mp4] [--style STYLE.md] [--notes "..."]
       [--work DIR] [--until STEP] [--redo STEP] [--caption-theme NAME]
"""
import argparse
import json
import math
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import audio as A
from . import brain
from . import config as C
from .brain import extract_json
from .style import Style

T0 = time.time()
ST = Style()          # replaced in run()
PROVIDER = None       # B-roll provider (or None)
PY = sys.executable
CAPTION_THEME = None
LANDSCAPE = False    # 16:9 output (set per video in edit(): style "format", or auto from the raw video)
SYNC_BIAS = 0.04      # natural mouth-leads-voice lag (audio slightly behind the lips looks right)
FADE = 0.012          # click-free audio edge at every cut (equal-power, inside the silence)
TAIL = 0.45           # hold after the very last word
PAD_BEFORE = 0.08     # silence kept before the very first word
SR = 48000
LOGO_W, LOGO_CX, LOGO_CY = 469, 845, 234
PIP_W, PIP_H, PIP_Y = 1080, 960, 960      # "pip" = B-roll filling the whole bottom half
STICKER_ICONS = sorted(json.load(open(os.path.join(C.FONTS, "fa-icons.json"))))


def log(*a):
    print(f"[reel {time.time() - T0:6.1f}s]", *a, flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def probe(p):
    d = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                                            "format=duration:stream=codec_type,width,height,avg_frame_rate",
                                            "-of", "json", p]))
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    n, dd = v["avg_frame_rate"].split("/")
    return float(d["format"]["duration"]), int(v["width"]), int(v["height"]), float(n) / float(dd or 1)


def jload(p):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def jsave(p, o):
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(o, fh, indent=1, ensure_ascii=False)


def speed():
    return float(ST["speed"])


def FPS():
    return int(ST["fps"])


ORIENT_SWAPS = [("a vertical (9:16) talking-head reel", "a landscape (16:9) video"),
                ("vertical (9:16)", "landscape (16:9)"), ("short vertical reels", "videos"),
                ("this vertical reel", "this landscape video"), ("a vertical reel", "a landscape (16:9) video"),
                ("chosen for a vertical reel", "chosen for a landscape (16:9) video"),
                ("prefer vertical clips", "prefer horizontal 16:9 clips"), ("cropped away in 9:16", "cropped away in 16:9"),
                ("clear on a phone", "clear on a big screen"), ("The reel says", "The video says")]


def orient(prompt):
    """the prompts are written for vertical reels; a landscape video gets the 16:9 wording"""
    if not LANDSCAPE:
        return prompt
    for a, b in ORIENT_SWAPS:
        prompt = prompt.replace(a, b)
    return prompt


def ask(prompt, model="sonnet", images=()):
    return brain.ask(ST.rulebook() + orient(prompt), model=model, images=images)


def ask_json(prompt, model="sonnet", images=()):
    return brain.ask_json(ST.rulebook() + orient(prompt), model=model, images=images)


def source_is_landscape(src):
    """wider than tall as displayed (phone rotation metadata taken into account)"""
    try:
        d = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                                "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
                                                "-of", "json", src]))
        v = d["streams"][0]
        rot = abs(int(float((v.get("tags") or {}).get("rotate", 0) or 0)))
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = abs(int(float(sd["rotation"])))
        w, h = int(v["width"]), int(v["height"])
        if rot % 180 == 90:
            w, h = h, w
        return w > h
    except Exception:
        return False


# ------------------------------------------------------------------ 1 transcribe
def step_transcribe(src, work):
    out = os.path.join(work, "words.json")
    if os.path.exists(out):
        return jload(out)
    from . import transcribe as TR
    dur = probe(src)[0]
    wav = os.path.join(work, "a16.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", wav])
    isl = A.speech_islands(wav)
    cdir = os.path.join(work, "chunks")
    os.makedirs(cdir, exist_ok=True)
    files = []
    for k, (a, b) in enumerate(isl):
        f = os.path.join(cdir, f"c{k:04d}.wav")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", wav, f])
        files.append(f)
    words = TR.transcribe_islands(isl, files, log=log)
    shutil.rmtree(cdir, ignore_errors=True)
    if not words:
        raise RuntimeError("no speech found in the recording")
    voiced, _ = A.load_energy(wav)
    A.refine_words(words, A.Energy(voiced), dur)
    A.fix_long_words(words, voiced)
    res = [{"i": k, "w": w["text"], "s": round(w["s"], 3), "e": round(w["e"], 3)} for k, w in enumerate(words)]
    jsave(out, res)
    log(f"{len(res)} words")
    return res


def phrases_of(words, gap=0.35):
    ph, cur = [], []
    for w in words:
        if cur and (w["s"] - cur[-1]["e"] >= gap or cur[-1]["w"][-1:] in ".?!"):
            ph.append(cur)
            cur = []
        cur.append(w)
    if cur:
        ph.append(cur)
    return ph


# ------------------------------------------------------------------ 2 takes
TAKES_RULES = """You are the video editor for a creator's short vertical reels. Below is the word-level transcript
of a RAW selfie recording (talking head). The creator records each script line, and after a mistake simply says the
line again (sometimes many times), or says things like "redo that", "wait", "I messed up", "let me start over",
"here we go again". There may be a slate or test phrase before the takes. Decide which words survive into the final
video:
- Keep ONLY the LAST complete, clean take of each script line. A take was good when the speaker moves on to the NEXT
  script line after it. Earlier repeats, partial lines and false starts are removed.
- Remove EVERYTHING that is not the script: talking to someone off camera, stage directions about the script
  ("moving on to step three", "here we go"), slates, every retake cue ("again", "redo", "wait", "I messed up",
  "let me try that again"), comments to themselves, and fillers (um, uh) when they can be cut cleanly.
- Never remove real script content. The result must read as one continuous, natural script in the original order,
  with no line duplicated and no line missing.
- A take can restart mid-sentence: keep the beginning from the earlier take only if the join reads naturally,
  otherwise keep the full later take.
- Every kept range ends at the END of a sentence or clause: never drop the last word of a line.
- Speech recognition sometimes mishears words; judge by meaning.
Output ONLY JSON: {"keep": [[first_word_index, last_word_index], ...], "script": "<the final script as it will be
heard, plain text>"} with ranges inclusive, ascending, non-overlapping.

TRANSCRIPT (each phrase: P<n> <start sec> | index:word ...):
"""

META = re.compile(r"\b(recording right now|leave me alone|in my script|continuing on|redo that|let me redo|"
                  r"i messed up|let me try that again|here we go again|start over)\b", re.I)


def step_takes(words, work):
    out = os.path.join(work, "takes.json")
    if os.path.exists(out):
        return jload(out)
    lines = [f"P{k} {p[0]['s']:.1f}s | " + " ".join(f"{w['i']}:{w['w']}" for w in p)
             for k, p in enumerate(phrases_of(words))]
    log("choosing takes (Claude, smart model)")
    res = ask_json(TAKES_RULES + "\n".join(lines), model="opus")
    res["keep"] = snap_sentence_ends(words, clamp_ranges(words, res["keep"]))
    issues = take_issues(words, res["keep"])
    if issues:
        log("take check found problems -> review:", "; ".join(issues))
        fix = (TAKES_RULES.split("TRANSCRIPT")[0] + "\nA first pass chose keep=" + json.dumps(res["keep"]) +
               "\nAn automatic check found these problems:\n- " + "\n- ".join(issues) +
               "\nFix them (and anything else wrong) and output the corrected JSON in the same format.\n\n"
               "TRANSCRIPT (each phrase: P<n> <start sec> | index:word ...):\n" + "\n".join(lines))
        res = ask_json(fix, model="opus")
        res["keep"] = snap_sentence_ends(words, clamp_ranges(words, res["keep"]))
        left = take_issues(words, res["keep"])
        if left:
            log("WARNING take check still flags:", "; ".join(left))
        res["review"] = {"issues": issues, "left": left}
    jsave(out, res)
    return res


def clamp_ranges(words, keep):
    n = len(words)
    out = []
    for a, b in keep:
        a, b = max(0, int(a)), min(n - 1, int(b))
        if a <= b and (not out or a > out[-1][1]):
            out.append([a, b])
    return out


def snap_sentence_ends(words, keep):
    """a range that stops one or two words short of its sentence end (same breath) is extended to it"""
    byi = {w["i"]: w for w in words}
    out = []
    for a, b in keep:
        if not re.search(r"[.?!,]$", byi[b]["w"]):
            for k in (1, 2):
                nx = byi.get(b + k)
                if nx is None or nx["s"] - byi[b + k - 1]["e"] > 0.6:
                    break
                if re.search(r"[.?!]$", nx["w"]):
                    b = b + k
                    break
        if out and a <= out[-1][1]:
            a = out[-1][1] + 1
        if a <= b:
            out.append([a, b])
    return out


def take_issues(words, keep):
    byi = {w["i"]: w for w in words}
    ids = [i for a, b in keep for i in range(a, b + 1)]
    toks = [re.sub(r"[^a-z0-9']", "", byi[i]["w"].lower()) for i in ids]
    text = " ".join(byi[i]["w"] for i in ids)
    issues = []
    for m in META.finditer(text):
        issues.append(f'non-script talk kept: "...{text[max(0, m.start() - 40):m.end() + 30]}..."')
    seen = {}
    for k in range(len(toks) - 5):
        g = " ".join(toks[k:k + 6])
        if g in seen and k - seen[g] >= 6:
            issues.append(f'repeated passage kept twice: "{g}" (words {ids[seen[g]]} and {ids[k]})')
            break
        seen.setdefault(g, k)
    for a, b in keep:
        nb = byi.get(b + 1)
        if nb and not re.search(r"[.?!,]$", byi[b]["w"]) and re.search(r"[.?!]$", nb["w"]) and \
                nb["s"] - byi[b]["e"] < 0.6:
            issues.append(f'range ends one word short of its sentence: "...{byi[b]["w"]}" drops "{nb["w"]}"')
    return issues


# ------------------------------------------------------------------ 3 spans
def fr(t):
    return round(t * FPS()) / FPS()


def step_spans(words, takes, dur, work):
    out = os.path.join(work, "spans.json")
    if os.path.exists(out):
        return jload(out)
    db, thr = A.voice_db(os.path.join(work, "a16.wav"))
    keep = set()
    for a, b in takes["keep"]:
        keep.update(range(a, b + 1))
    kept = [w for w in words if w["i"] in keep]
    if not kept:
        raise RuntimeError("the take picker kept no words")
    byi = {w["i"]: w for w in words}
    join_mid, join_end = tuple(ST["join_mid"]), tuple(ST["join_end"])
    groups = []
    for k, w in enumerate(kept):
        prev = kept[k - 1] if k else None
        if prev is not None and w["i"] == prev["i"] + 1 and w["s"] - prev["e"] <= ST["gap_keep"]:
            groups[-1].append(w)
        else:
            groups.append([w])
    spans = []
    for g in groups:
        first, last = g[0], g[-1]
        prv_raw, nxt_raw = byi.get(first["i"] - 1), byi.get(last["i"] + 1)
        # clean cut points from the loudness curve (never from whisper times alone): the clip may only start in the
        # silence before its first word and end in the silence after its last word -> no sliver of a cut word
        lo = max(first["s"] - 0.60, ((prv_raw["s"] + prv_raw["e"]) / 2) if prv_raw else 0.0)
        on, quiet_from = A.clean_onset(db, thr, first["s"], lo)
        hi = min(last["e"] + 0.60, ((nxt_raw["s"] + nxt_raw["e"]) / 2) if nxt_raw else dur)
        off, quiet_to = A.clean_offset(db, thr, last["e"], hi)
        tail = (join_end if re.search(r"[.?!]$", last["w"]) else join_mid)[0]
        spans.append({"on": on, "off": off, "qf": quiet_from, "qt": quiet_to, "tail": tail,
                      "words": [w["i"] for w in g], "last": last["w"]})
    for k, sp in enumerate(spans):
        prev_end_sentence = k and re.search(r"[.?!]$", spans[k - 1]["last"])
        head = (join_end if prev_end_sentence else join_mid)[1] if k else PAD_BEFORE
        # padding only inside the silence: never reach back into the previous sound or on into the next one
        sp["a"] = max(0.0, sp["on"] - head, min(sp["on"], sp["qf"] + 0.01))
        sp["b"] = min(sp["off"] + sp["tail"], max(sp["off"], sp["qt"] - 0.01))
    last_sp = spans[-1]
    last_sp["b"] = min(dur, last_sp["off"] + TAIL, max(last_sp["off"], last_sp["qt"] - 0.01)
                       if last_sp["qt"] < dur - 0.02 else dur)
    clean = []
    for sp in spans:
        a, b = fr(sp["a"]), fr(sp["b"])
        if clean and a < clean[-1]["b"]:
            a = clean[-1]["b"]
        if b - a >= 2 / FPS():
            clean.append({"a": a, "b": b, "words": sp["words"], "on": round(sp["on"], 3), "off": round(sp["off"], 3)})
        elif clean:
            clean[-1]["words"] += sp["words"]
    off = 0.0
    for sp in clean:
        sp["voice_off"] = sp["off"]
        sp["off"] = round(off, 4)
        off += sp["b"] - sp["a"]
    worst = 0.0
    for p, n in zip(clean, clean[1:]):
        gap = ((p["b"] - p["voice_off"]) + (n["on"] - n["a"])) / speed()
        worst = max(worst, gap)
        if gap > ST["max_join_gap"]:
            log(f"WARNING long join {gap:.2f}s at {n['off'] / speed():.2f}s")
    # join QA: the first/last 30 ms of every clip must be silence (else a neighbouring word leaks in)
    leaks = []
    for k, sp in enumerate(clean):
        h_db, t_db = A.edge_db(db, sp["a"], sp["b"])
        if (k and h_db > thr + 6) or (k < len(clean) - 1 and t_db > thr + 6):
            leaks.append(round(sp["off"] / speed(), 2))
    if leaks:
        log(f"note: {len(leaks)} join(s) where words run together (cut at the quietest point): {leaks[:12]}")
    res = {"spans": clean, "cut_dur": round(off, 3), "final_dur": round(off / speed(), 3), "unclean_joins": leaks}
    jsave(out, res)
    log(f"{len(clean)} pieces, {dur:.1f}s -> {off:.1f}s -> {off / speed():.1f}s at {speed()}x; "
        f"longest join silence {worst:.2f}s")
    return res


def final_time(spans, t):
    """raw-timeline time -> final (cut + speed-up) time"""
    for sp in spans:
        if t < sp["b"] + 1e-6:
            return (sp["off"] + min(max(t - sp["a"], 0.0), sp["b"] - sp["a"])) / speed()
    sp = spans[-1]
    return (sp["off"] + sp["b"] - sp["a"]) / speed()


def kept_words_final(words, sp):
    spans = sp["spans"]
    keep = [i for s in spans for i in s["words"]]
    byi = {w["i"]: w for w in words}
    return [{"i": i, "w": byi[i]["w"], "s": round(final_time(spans, byi[i]["s"]), 3),
             "e": round(final_time(spans, byi[i]["e"]), 3)} for i in keep]


# ------------------------------------------------------------------ 4 sync
def step_sync(work):
    out = os.path.join(work, "sync.json")
    if os.path.exists(out):
        return jload(out)
    o = {"audio_shift": float(ST.get("sync_start", 0.0))}   # measured + corrected on the real cut before render
    jsave(out, o)
    return o


# ------------------------------------------------------------------ 5 captions
CAPTION_RULES = """You are the caption editor for a vertical reel. Below are the final spoken words (index:word).
Group them into on-screen captions and fix the text:
- Each caption is a natural phrase of about MINW-MAXW words (one line preferred, two lines max, never more than ~60
  characters). Break at natural phrase/clause boundaries.
- Punctuation chain rule: read each caption with its neighbours. A caption that does not finish the sentence ends
  with a comma or nothing, and the next caption starts lowercase. A period (and a capital on the next caption) only
  where the sentence really ends. Do not add "?" to a statement that is only phrased like a question. A clause that
  continues the sentence ("whether ...", "or ...", "and it's ...") starts lowercase and the caption before it ends
  with a comma, not a period.
- Use only commas and periods (a "?" only for a real question). No colons, semicolons, dashes or exclamation marks.
- When the speaker introduces themselves, "I'm <name>," is its own caption and the "the founder of <company>." part
  the next.
- Do not rewrite their words. Only fix words the speech recognition misheard, and restore dropped words (judge by
  meaning). Numbers spoken as "Number one" become "#1" followed by a capital, no comma ("#1 Start small.").
  Contractions as spoken in natural speech.
- KEYS: mark the key words (shown in the accent colour): the important content words and phrases of each caption -
  nouns, key verbs, numbers, the words that carry the meaning. Not articles, lone pronouns or filler. Usually 30-50%
  of the words.KEYNOTE
Output ONLY JSON: [{"first": first_word_index, "last": last_word_index, "tokens": ["word", ...], "keys": [token
positions that are key words]}, ...] covering every index exactly once, in order. tokens are the corrected words of
that caption (usually one token per spoken word).

WORDS:
"""


def caption_prompt():
    lo, hi = ST["caption_words"]
    note = "" if ST["key_words"] else " For this creator's look, give keys: [] (only the spoken word is highlighted)."
    return CAPTION_RULES.replace("MINW", str(lo)).replace("MAXW", str(hi)).replace("KEYNOTE", note)


def step_captions(kw, work):
    out = os.path.join(work, "captions.json")
    if os.path.exists(out):
        return jload(out)
    log("writing captions (Claude)")
    groups = ask_json(caption_prompt() + " ".join(f"{w['i']}:{w['w']}" for w in kw), model="sonnet")
    if isinstance(groups, dict):
        groups = next((v for v in groups.values() if isinstance(v, list)), [])
    caps = captions_from_groups(kw, groups)
    jsave(out, caps)
    log(f"{len(caps)} captions")
    return caps


def captions_from_groups(kw, groups):
    pos = {w["i"]: k for k, w in enumerate(kw)}
    caps, covered = [], set()
    for g in groups:
        if g.get("first") not in pos or g.get("last") not in pos:
            continue
        a, b = pos[g["first"]], pos[g["last"]]
        if b < a or any(k in covered for k in range(a, b + 1)):
            continue
        covered.update(range(a, b + 1))
        ws = kw[a:b + 1]
        toks = [t for t in g.get("tokens") or [] if str(t).strip()] or [w["w"] for w in ws]
        keys = set(g.get("keys", []) if ST["key_words"] else [])
        if len(toks) == len(ws):
            tw = [(t, w["s"], w["e"]) for t, w in zip(toks, ws)]
        else:   # spread tokens over the spoken time by character length
            s0, e0 = ws[0]["s"], ws[-1]["e"]
            L = sum(len(t) + 1 for t in toks)
            acc, tw = 0, []
            for t in toks:
                ts = s0 + (e0 - s0) * acc / L
                acc += len(t) + 1
                tw.append((t, ts, s0 + (e0 - s0) * acc / L))
        caps.append({"start": ws[0]["s"], "end": ws[-1]["e"],
                     "words": [{"w": t, "s": s, "e": e, "key": k in keys} for k, (t, s, e) in enumerate(tw)]})
    # any words Claude skipped still get a plain caption (nothing spoken goes uncaptioned)
    for k, w in enumerate(kw):
        if k not in covered:
            caps.append({"start": w["s"], "end": w["e"], "words": [{"w": w["w"], "s": w["s"], "e": w["e"], "key": False}]})
    caps.sort(key=lambda c: c["start"])
    for k in range(len(caps) - 1):      # punctuation chain, enforced
        last, first = caps[k]["words"][-1], caps[k + 1]["words"][0]
        if first["w"][:1].islower() and last["w"].endswith("."):
            last["w"] = last["w"][:-1] + ","
        elif last["w"].endswith(",") and first["w"] in ("And", "Or", "But", "So", "Whether", "If", "That", "Which"):
            first["w"] = first["w"].lower()
    for k in range(len(caps)):
        nxt = caps[k + 1]["start"] if k + 1 < len(caps) else None
        end = caps[k]["end"] + 0.2
        if nxt is not None and (nxt - caps[k]["end"] < 0.35 or end > nxt):
            end = nxt
        caps[k]["end"] = round(end, 3)
    return caps


# ------------------------------------------------------------------ 6 visuals
EVENT_TEXT = {
    "broll": """"broll" - full-screen stock footage over the speaker (the voice keeps playing). The MOST used type
   (about 40-50% of events). It must MATCH the exact words being said, literal and concrete ("your savings" -> coins
   dropping into a jar; "red flag" -> a red flag; "budget" -> hands on a calculator with bills). Never random or
   generic. Covers ONE phrase: start = the first word of the phrase, dur = the phrase length (code then starts it
   LEAD s earlier and holds it EXTRA s longer), hard cut. Give 3 stock-video search queries, most specific first (2-4
   words, concrete filmable objects/actions, in English). Leave at least 1 s of the face between two B-rolls.
   Fields: subject (what the viewer sees), queries.""",
    "pip": """"pip" - a half-screen B-roll: a stock clip filling the WHOLE BOTTOM HALF of the frame while the face
   stays visible on top. Shows EXACTLY the thing named, as a clear close-up. Fields: subject, queries (3 searches,
   2-4 words, most specific first), "cross": true ONLY when the speaker calls it a myth/mistake/wrong (a red X is
   drawn over it). 1.8-3.5 s.""",
    "sticker": """"sticker" - an animated icon badge that pops in beside the head, optional 1-2 word label (CAPS). Pick
   the icon from this list ONLY: ICONS. Fields: icon, label, side ("left"/"right", alternate), color ("pink", "teal",
   "gold"; gold for warnings/money). 1.3-2.2 s. Use for a concept with a clear symbol (savings -> piggy-bank "SAVE",
   warning/mistake -> triangle-exclamation, growth -> arrow-trend-up, time -> hourglass-half, yes -> circle-check,
   wrong/myth -> circle-xmark).""",
    "text": """"text" - a kinetic headline card that pops in above the head: 1-4 words (CAPS) naming the key idea or
   number of that moment ("3 STEPS", "STEP 1", "$50 A MONTH", "START NOW"). style "pink" (default), "teal" or
   "white". 1.3-2.5 s. Use it on every numbered step heading and on key numbers/claims.""",
    "emoji": """"emoji" - a small cut-out emoji sticker beside the speaker (no badge, no text) that acts out the words
   ("hot weather" -> \U0001F975, "once a year" -> \U0001F4C5, "cheap" -> \U0001F4B8, "right" -> \u2705, "wrong" ->
   \u274C, "think" -> \U0001F914). Fields: emoji (1-2 SINGLE emoji characters; no skin tones, no combined/ZWJ emoji),
   side ("left"/"right", alternate). 1.2-2.4 s.""",
    "zoom": """"zoom" - a punch-in on the face (locked framing with one slow push, hard cut in and out) for an emphasis
   line. 1.2-3.0 s.""",
    "bw": """"bw" - black-and-white mood filter for a clearly negative moment (losses, a crash, a costly mistake).
   At most 2 per video, 1.2-2.5 s.""",
    "title": """"title" - EXACTLY TWO per video, plain in-app style text at the top of the frame (titles may overlap
   other events): (a) the HOOK title, start 0.0, dur 2.6-3.0: the opening question as a short casual sentence-case
   question with one emoji; (b) the CLOSING title over the final question / call to action (the last ~3-4 s, to the
   end). Fields: text, style ("white"; "red" now and then for the closing one).""",
}

VISUALS_RULES = """You are the video editor for a vertical (9:16) talking-head reel. Plan EVERY visual event so
something new happens on screen every GAP_LO-GAP_HI seconds (start to start, NEVER more than GAP_HI s apart, from the
first second to the end). Mix these event types, each aligned to the start of the words it illustrates:

TYPES

Rules:
- Never more than GAP_HI s without a visual change (an event starting, or a B-roll/zoom/bw ending back to the face),
  and never less than MINGAP s between two event starts. Titles (if used) do not count.
- Events never overlap (titles excepted); leave >= 0.3 s between one ending and the next starting.
- Do not use the same non-B-roll type twice in a row; alternate so it stays fresh and professional.
- Opening hook (first ~HOOK s): no full-screen B-roll; start the first event within the first 2 s.CTA
- Never cover a numbered heading caption (#1, #2 ...) with B-roll: give it a "text" card instead (if available).
- Relevance and quality first: every event must fit the words at that moment.
Output ONLY JSON, a list in time order: [{"type": "...", "start": seconds, "dur": seconds, "words": "<words it
covers>", ...type fields...}].

CAPTIONS (start-end seconds: text):
"""


def event_types():
    types = [t for t in ST["event_types"] if t in EVENT_TEXT]
    if LANDSCAPE:     # 16:9: B-roll, punch-ins and filters here; on-screen text comes from the dynamic-text layer
        types = [t for t in types if t in ("broll", "zoom", "bw")]
    if PROVIDER is None:
        types = [t for t in types if t not in ("broll", "pip")]
    return types or ["zoom"]


def visuals_prompt():
    types = event_types()
    body = "\n".join(f"{k + 1}. " + EVENT_TEXT[t] for k, t in enumerate(types))
    body = (body.replace("ICONS", ", ".join(STICKER_ICONS)).replace("LEAD", f"{ST['broll_lead']:.1f}")
            .replace("EXTRA", f"{ST['broll_extra']:.1f}"))
    cta = ("\n- Closing introduction + call to action (\"I'm <name> ...\" to the end): no full-screen B-roll."
           if ST["cta_no_broll"] else "")
    if ST["brand_name"]:
        cta += (f" The brand logo shows automatically while \"{ST['brand_name']}\" is on screen, so put nothing but "
                "a zoom on that line.")
    return (VISUALS_RULES.replace("TYPES", body).replace("GAP_LO", f"{max(1.5, ST['event_target_gap'] - 0.6):.1f}")
            .replace("GAP_HI", f"{ST['event_max_gap']:.1f}").replace("MINGAP", f"{ST['event_min_gap']:.1f}")
            .replace("HOOK", f"{ST['hook_no_broll']:.1f}").replace("CTA", cta))


def dur_limits():
    return {k: tuple(v) for k, v in ST["dur_limits"].items()}


def cta_start(final_dur, caps):
    if not ST["cta_no_broll"]:
        return final_dur
    cta = final_dur - 12.0 if final_dur > 30 else final_dur
    for c in caps:
        t = " ".join(w["w"] for w in c["words"]).lower()
        if t.startswith(("i'm ", "i am ", "my name is ")) and c["start"] > final_dur * 0.5:
            return min(cta, c["start"])
    return cta


def logo_spans(caps):
    name = (ST["brand_name"] or "").lower().strip()
    if not name:
        return []
    return [(c["start"], c["end"]) for c in caps
            if name in re.sub(r"[^a-z0-9 ]", "", " ".join(w["w"] for w in c["words"]).lower())]


def enforce_visuals(plan, final_dur, caps):
    """Hard rules in code (the planner is not trusted to count): valid types/durations, no B-roll in the hook or
    the CTA, nothing but a zoom on the logo line, face between B-rolls, events EVENT_MIN_GAP..EVENT_MAX_GAP apart;
    any gap still over the max gets a punch-in on the caption that starts there."""
    lim = dur_limits()
    allowed = set(event_types())
    max_gap, tgt_gap, min_gap = ST["event_max_gap"], ST["event_target_gap"], ST["event_min_gap"]
    cta = cta_start(final_dur, caps)
    logos = logo_spans(caps)
    titles = [e for e in plan if e.get("type") == "title"] if "title" in allowed else []
    cand = []
    for e in plan:
        t = e.get("type")
        if t not in lim or t not in allowed:
            continue
        try:
            e["start"] = round(float(e["start"]), 3)
            e["dur"] = float(e["dur"])
        except (KeyError, TypeError, ValueError):
            continue
        if t == "sticker" and e.get("icon") not in STICKER_ICONS:
            e["icon"] = "star"
        if t == "emoji" and not any(ord(ch) >= 0x2600 for ch in str(e.get("emoji", ""))):
            e["emoji"] = "\u2728"
        if t in ("broll", "pip") and not e.get("queries"):
            continue
        lo, hi = lim[t]
        if t == "broll" and not e.get("timed"):
            e["start"] = round(max(0.0, e["start"] - ST["broll_lead"]), 3)
            e["dur"] = e["dur"] + ST["broll_lead"] + ST["broll_extra"] - 0.4
            e["timed"] = True
        e["dur"] = round(max(lo, min(hi, e["dur"])), 3)
        s, en = e["start"], e["start"] + e["dur"]
        if t == "broll" and (s < ST["hook_no_broll"] or en > cta):
            continue
        if t != "zoom" and any(s < b and en > a for a, b in logos):
            continue
        if en > final_dur - 0.2 or s < ST["clean_open"]:
            continue
        cand.append(e)

    def fits(e, chosen):
        s, en = e["start"], e["start"] + e["dur"]
        for c in chosen:
            cs, ce = c["start"], c["start"] + c["dur"]
            if abs(s - cs) < min_gap or (s < ce + 0.3 and en + 0.3 > cs):
                return False
            if e["type"] == "broll" and c["type"] == "broll" and \
                    (s < ce + ST["broll_face_gap"] and en + ST["broll_face_gap"] > cs):
                return False
        return True

    def tier(e):
        if e["type"] in ("broll", "pip"):
            return 0 if e["type"] == "broll" else 1
        if e["type"] == "text" and re.search(r"STEP|#|\d", str(e.get("text", "")).upper()):
            return 1
        return 2 if e["type"] != "zoom" else 3
    chosen = []
    for e in sorted(cand, key=lambda x: (tier(x), x["start"])):
        if fits(e, chosen):
            chosen.append(e)
            continue
        if e["type"] in ("sticker", "text", "bw", "emoji", "pip"):
            lo = lim[e["type"]][0]
            tries = [dict(e, dur=round(e["dur"] - 0.1 * k, 3)) for k in range(1, 11)]
            tries += [dict(e, start=round(e["start"] + 0.1 * k, 3), dur=round(e["dur"] - 0.1 * k, 3))
                      for k in range(1, 11)]
            for e2 in tries:
                if e2["dur"] >= lo and fits(e2, chosen):
                    chosen.append(e2)
                    break
    chosen.sort(key=lambda x: x["start"])
    ded = []
    for e in chosen:
        if ded and e["type"] == ded[-1]["type"] == "zoom":
            continue
        ded.append(e)
    # fill the rhythm: any stretch over max_gap without a visual change gets a punch-in
    starts = sorted(c["start"] for c in caps)
    out, queue = [], list(ded)
    prev = {"type": "none", "start": -tgt_gap + 1.5, "dur": 0.0}
    guard = 0
    while guard < 2000:
        guard += 1
        nxt = queue[0] if queue else None
        p_s, p_e = prev["start"], prev["start"] + prev["dur"]
        last_change = p_e if prev["type"] in ("broll", "zoom", "bw", "pip") else p_s
        limit = last_change + max_gap
        if nxt is not None and nxt["start"] <= max(limit, p_e + 0.3):
            out.append(queue.pop(0))
            prev = nxt
            continue
        if nxt is None and limit > final_dur - 1.2:
            break
        lo_t = max(p_e + 0.3, p_s + min_gap, 0.3, ST["clean_open"])
        hi_t = (nxt["start"] - 0.3) if nxt is not None else final_dur - 0.2
        cands = [x for x in starts if lo_t <= x <= limit and hi_t - x >= 1.2]
        fs = min(cands, key=lambda x: abs(x - (last_change + tgt_gap))) if cands else lo_t
        if fs > limit or hi_t - fs < 1.2:
            if nxt is not None and nxt["start"] - limit <= 0.6 and limit >= p_e + 0.3:
                nxt["start"] = round(limit, 3)
            elif nxt is not None:
                log(f"rhythm gap {nxt['start'] - p_s:.1f}s at {p_s:.1f}s could not be filled")
                out.append(queue.pop(0))
                prev = nxt
            else:
                break
            continue
        f = {"type": "zoom", "start": round(fs, 3), "dur": round(min(2.0, hi_t - fs), 3), "words": "(rhythm filler)",
             "auto": True}
        out.append(f)
        prev = f
    if titles:
        out += hook_and_closing_titles(titles, final_dur, caps)
    return out


def hook_and_closing_titles(titles, final_dur, caps):
    """the two top texts: hook question for the first ~3 s, closing question to the end (made from the speaker's
    own first / last question when the planner left one out)"""
    def sentence_case(t):
        t = t.strip()
        return t[:1].upper() + t[1:].lower() if t.isupper() else t
    words = [w["w"] for c in caps for w in c["words"]]

    def first_question():
        out = []
        for w in words:
            out.append(w)
            if w.endswith(("?", ".")) and len(out) >= 3:
                break
        return sentence_case(" ".join(out).rstrip(".?") + "?") + " \U0001F914"

    def last_question():
        tail = [c for c in caps if c["start"] >= final_dur - 10]
        txt = " ".join(w["w"] for c in tail for w in c["words"])
        qs = [q.strip() for q in re.split(r"(?<=[?.])\s+", txt) if q.strip().endswith("?")]
        return (qs[-1][:1].upper() + qs[-1][1:]) if qs else None
    mid = final_dur / 2
    hook = next((t for t in titles if float(t.get("start", 0)) < mid and t.get("text")), None)
    close = next((t for t in reversed(titles) if float(t.get("start", 0)) >= mid and t.get("text")), None)
    out = [{"type": "title", "start": 0.0, "dur": round(min(3.0, max(2.4, float((hook or {}).get("dur", 2.8)))), 3),
            "text": (hook or {}).get("text") or first_question(), "style": "white", "words": "(hook title)"}]
    ctext = (close or {}).get("text") or last_question()
    if ctext:
        cs = float((close or {}).get("start", final_dur - 3.5))
        cs = min(max(cs, final_dur - 4.5), final_dur - 2.0)
        style = (close or {}).get("style") if (close or {}).get("style") in ("white", "red") else "white"
        out.append({"type": "title", "start": round(cs, 3), "dur": round(final_dur - 0.05 - cs, 3), "text": ctext,
                    "style": style, "words": "(closing title)"})
    return out


def phrase_lines_from_caps(caps):
    return [(c["start"], c["end"], " ".join(w["w"] for w in c["words"])) for c in caps]


def phrase_lines_from_words(kw):
    """caption-like phrases straight from the kept words, so the visual plan can be written WHILE the captions are"""
    out, cur = [], []
    for k, w in enumerate(kw):
        cur.append(w)
        nxt = kw[k + 1] if k + 1 < len(kw) else None
        if nxt is None or re.search(r"[.?!,]$", w["w"]) or nxt["s"] - w["e"] > 0.3 or len(cur) >= 9:
            out.append((cur[0]["s"], cur[-1]["e"], " ".join(x["w"] for x in cur)))
            cur = []
    return out


def plan_visuals_raw(lines, final_dur):
    """the visual plan, written as two halves in parallel for long videos; enforce_visuals() then applies every
    rhythm rule across the whole video, so the seam does not matter"""
    rules = visuals_prompt()
    need = max(0, int(final_dur / max(3, ST["broll_every_sec"]))) if "broll" in event_types() else 0
    parts = 2 if final_dur > 45 else 1
    log(f"planning visuals every {ST['event_target_gap']:.1f}-{ST['event_max_gap']:.1f} s (Claude, {parts} part(s))")
    bounds = [(final_dur * k / parts, final_dur * (k + 1) / parts) for k in range(parts)]

    def part(i):
        a, b = bounds[i]
        mine = [l for l in lines if a <= l[0] < b] if parts > 1 else lines
        txt = "\n".join(f"{s0:.2f}-{e0:.2f}: {t}" for s0, e0, t in mine)
        note = f"\n\nVideo length: {final_dur:.1f} s."
        if need:
            note += f" Plan at least {max(1, round(need * (b - a) / final_dur))} B-roll cutaways in this part."
        if parts > 1:
            ctx = "\n".join(f"{s0:.2f}-{e0:.2f}: {t}" for s0, e0, t in lines if not (a <= s0 < b))
            note += (f" You are planning PART {i + 1} of {parts}: only events that start between {a:.1f} and {b:.1f} s"
                     + ("" if i == 0 else " (the opening-hook rule does not apply to this part)")
                     + f".\n\nOTHER PART (context only, do not plan events there):\n{ctx}")
        try:
            evs = ask_json(rules + txt + note, model="sonnet")
        except Exception as ex:
            log("visual plan part failed:", ex)
            evs = []
        return [e for e in evs if isinstance(e, dict) and
                (e.get("type") == "title" or a - 0.5 <= float(e.get("start", -1)) < b)]
    with ThreadPoolExecutor(parts) as ex_:
        res = list(ex_.map(part, range(parts)))
    return [e for r in res for e in r]


def contact_sheet(cands, path):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(os.path.join(C.FONTS, "Rubik-Bold.ttf"), 26)
    tiles = []
    for c in cands:
        try:
            im = Image.open(c["thumb_local"]).convert("RGB")
        except Exception:
            im = Image.new("RGB", (270, 480), (60, 60, 60))
        im.thumbnail((270, 480))
        t = Image.new("RGB", (270, 520), (0, 0, 0))
        t.paste(im, ((270 - im.width) // 2, 0))
        ImageDraw.Draw(t).text((8, 486), f"#{c['n']}  {c.get('duration_sec') or '?'}s", font=f, fill=(255, 255, 0))
        tiles.append(t)
    cols = min(4, len(tiles))
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (270 * cols, 520 * rows), (0, 0, 0))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * 270, (k // cols) * 520))
    sheet.save(path, quality=88)


def pick_candidates(item, work, k):
    cands = []
    for q in item.get("queries", [])[:3]:
        for orient_ in (("horizontal", "any") if LANDSCAPE else ("vertical", "any")):
            try:
                res = PROVIDER.search(q, orientation=orient_, limit=12, log=log)
            except Exception as ex:
                log("search failed", q, str(ex)[:80])
                res = []
            for r in res:
                if r["id"] not in {c["id"] for c in cands} and r["id"] not in item.get("rejected", []):
                    r["query"] = q
                    r["orientation_req"] = orient_
                    cands.append(r)
            if len([c for c in cands if c["query"] == q]) >= 8:
                break
        if len(cands) >= 12:
            break
    cands = cands[:12]
    tdir = os.path.join(work, "thumbs")
    os.makedirs(tdir, exist_ok=True)

    def thumb(nc):
        n, c = nc
        c["n"] = n
        c["thumb_local"] = os.path.join(tdir, f"b{k:03d}_{n:02d}.jpg")
        try:
            PROVIDER.fetch_thumb(c, c["thumb_local"])
        except Exception:
            pass
    with ThreadPoolExecutor(6) as ex_:
        list(ex_.map(thumb, enumerate(cands, 1)))
    sheet = os.path.join(work, f"broll{k:03d}_sheet.jpg")
    if cands:
        contact_sheet(cands, sheet)
    return cands, sheet


def _pick_prompt(item, cands):
    listing = "\n".join(f"#{c['n']}: {c.get('title')} ({c.get('duration_sec')} s, {c.get('width')}x{c.get('height')}, "
                        f"{c.get('source')})" for c in cands)
    return (f"The image is a contact sheet of numbered stock video thumbnails. The reel says: \"{item.get('words')}\". "
            f"The viewer should see: {item.get('subject')}.\nCandidates:\n{listing}\n"
            + ("This clip fills the bottom half of the vertical frame (a wide 9:8 panel), so pick a clear close-up "
               "where the subject fills the frame (any orientation). " if item["type"] == "pip" else "")
            + "Pick the ONE clip that matches the words most literally and looks professional and clear on a phone "
              "(prefer vertical clips; avoid cartoons/flat animation, text-heavy clips, watermarks, or clips whose "
              "subject would be cropped away in 9:16). Reply ONLY JSON {\"n\": <number>, \"why\": \"...\"}; if none "
              "fits reply {\"n\": 0, \"why\": \"...\"}.")


def fetch_clip(item, cand):
    item["file"] = PROVIDER.download(cand, C.BROLL_CACHE)
    item["id"], item["title"], item["source"] = cand["id"], cand.get("title"), cand.get("source")
    item["page_url"], item["author"] = cand.get("page_url"), cand.get("author")


def step_visuals(caps, work, final_dur, raw_plan=None):
    out = os.path.join(work, "visuals.json")
    if os.path.exists(out):
        plan = jload(out)
    else:
        plan = raw_plan if raw_plan is not None else plan_visuals_raw(phrase_lines_from_caps(caps), final_dur)
        plan = enforce_visuals(plan, final_dur, caps)
        jsave(out, plan)
        n = {}
        for e in plan:
            n[e["type"]] = n.get(e["type"], 0) + 1
        log(f"{len(plan)} visual events: {n}")
    if PROVIDER is None:
        return plan
    lock = threading.Lock()

    def pick_one(kv):
        k, item = kv
        cands, sheet = pick_candidates(item, work, k)
        if not cands:
            item["tried"] = True
            return
        try:
            ch = ask_json(_pick_prompt(item, cands), model="sonnet", images=[sheet])
        except Exception as ex:
            log("pick failed", ex)
            ch = {"n": 1, "why": "fallback: first result"}
        pick = next((c for c in cands if c["n"] == ch.get("n")), None)
        item["pick_why"] = ch.get("why")
        if pick:
            try:
                fetch_clip(item, pick)
            except Exception as ex:
                log("download failed:", str(ex)[:120])
        item["tried"] = True
        with lock:
            jsave(out, plan)
    todo = [(k, it) for k, it in enumerate(plan) if it["type"] in ("broll", "pip") and not it.get("file")
            and not it.get("tried")]
    log(f"finding {len(todo)} B-roll clips (4 at a time, {PROVIDER.name})")
    with ThreadPoolExecutor(4) as ex_:
        list(ex_.map(pick_one, todo))
    jsave(out, plan)
    verify_broll(plan, work, out)
    return plan


def broll_frame_sheet(items, path):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(os.path.join(C.FONTS, "Rubik-Bold.ttf"), 20)
    tiles = []
    for n, e in enumerate(items, 1):
        bd = probe(e["file"])[0]
        t = e.get("clip_start", 0.3 if bd > e["dur"] + 0.8 else 0.0) + e["dur"] / 2
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", e["file"], "-frames:v", "1", "-vf",
                              "scale=270:480:force_original_aspect_ratio=increase,crop=270:480", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        im = Image.frombytes("RGB", (270, 480), raw) if len(raw) == 270 * 480 * 3 else Image.new("RGB", (270, 480))
        tile = Image.new("RGB", (270, 520))
        tile.paste(im)
        ImageDraw.Draw(tile).text((6, 490), f"#{n}", font=f, fill=(255, 255, 0))
        tiles.append(tile)
    cols = min(5, len(tiles))
    rows = math.ceil(len(tiles) / cols)
    sheet = Image.new("RGB", (270 * cols, 520 * rows))
    for k, t in enumerate(tiles):
        sheet.paste(t, ((k % cols) * 270, (k // cols) * 520))
    sheet.save(path, quality=85)


BROLL_STANDARD = """Use the creator's rulebook above as the standard when it has B-roll examples. Otherwise: simple,
literal or common visual metaphors are GOOD ("save every month" -> coins into a jar; "it takes time" -> an hourglass).
Generic stock people (someone typing on a laptop, writing in a notebook, sitting at a desk, holding a device) FAIL
unless the words are literally about that action."""


def check_broll(items, work, tag):
    """one smart-model look at a frame of each chosen clip next to its words -> {n: {why, queries}} for rejects"""
    sp = os.path.join(work, f"broll_check_{tag}.jpg")
    broll_frame_sheet(items, sp)
    listing = "\n".join(f"#{n}: words \"{e['words']}\" | clip: {e.get('title')}" for n, e in enumerate(items, 1))
    prompt = (f"The image shows one frame from each stock B-roll clip chosen for a vertical reel, numbered. Each "
              f"covers these spoken words:\n{listing}\n\n{BROLL_STANDARD}\nBe conservative: reject ONLY clear failures - "
              "off-topic (nothing to do with the words), foreign-language paperwork/text, dark/muddy or blurry, "
              "cartoonish clip-art, a tiny subject lost in an empty frame, watermarks, or generic stock people (see "
              "above). Otherwise keep it. For each reject give 2 better stock-video search queries (2-4 words, "
              "concrete and filmable). Reply ONLY JSON {\"reject\": {\"<n>\": {\"why\": \"...\", \"queries\": "
              "[\"...\", \"...\"]}}} ({} if all fit).")
    try:
        res = ask_json(prompt, model="opus", images=[sp]).get("reject", {}) or {}
        return {int(k): v for k, v in res.items()}
    except Exception as ex:
        log("B-roll check failed:", ex)
        return {}


def verify_broll(plan, work, out):
    """Automatic second look at every chosen clip (bounded: one check; each reject gets ONE fresh search + pick +
    re-check; a clip rejected twice becomes a punch-in)."""
    br = [e for e in plan if e["type"] in ("broll", "pip") and e.get("file") and not e.get("verified")]
    if not br:
        return
    rej = check_broll(br, work, "1")
    retry = []
    for n, e in enumerate(br, 1):
        if n not in rej:
            e["verified"] = True
            continue
        log(f"B-roll check rejected {e.get('title')} on \"{e['words']}\": {rej[n].get('why', '')}")
        e["rejected"] = e.get("rejected", []) + [e.get("id")]
        e["queries"] = (rej[n].get("queries") or [])[:2] + e.get("queries", [])[:1]
        for k_ in ("file", "id", "title", "clip_start"):
            e.pop(k_, None)
        retry.append(e)
    jsave(out, plan)

    def retry_one(e):
        k = plan.index(e)
        cands, sheet = pick_candidates(e, work, 100 + k)
        if cands:
            try:
                n_ = ask_json(_pick_prompt(e, cands), model="sonnet", images=[sheet]).get("n")
            except Exception:
                n_ = 0
            c = next((c for c in cands if c["n"] == n_), None)
            if c:
                try:
                    fetch_clip(e, c)
                except Exception as ex:
                    log("re-pick download failed:", str(ex)[-120:])
        if not e.get("file"):
            log(f"  no better clip for \"{e['words']}\": punch-in instead")
            e["verified"] = True
    with ThreadPoolExecutor(4) as ex_:
        list(ex_.map(retry_one, retry))
    jsave(out, plan)
    again = [e for e in retry if e.get("file")]
    if again:
        rej2 = check_broll(again, work, "2")
        for n, e in enumerate(again, 1):
            if n in rej2:
                log(f"  second pick also rejected on \"{e['words']}\": punch-in instead")
                e["file"] = None
                e["id"] = None
            e["verified"] = True
        jsave(out, plan)


POST_RULES = """Write social post copy for this vertical reel. From the captions below give: a short title (under 70
characters, no clickbait), a 2-3 sentence description ending with the call to action given in the video (if any),
5-8 relevant hashtags, and the KEYWORD this video targets: the search phrase a viewer would type, written as the
question or phrase the video answers, Title Case, at most 70 characters. Output ONLY JSON {"title": "...",
"description": "...", "hashtags": ["#...", ...], "keyword": "..."}.

CAPTIONS:
"""


def write_post(caps, work):
    p = os.path.join(work, "post.json")
    if os.path.exists(p):
        return
    try:
        txt = " ".join(w["w"] for c in caps for w in c["words"])
        jsave(p, ask_json(POST_RULES + txt, model="sonnet"))
    except Exception as ex:
        log("post copy failed:", ex)


# ------------------------------------------------------------------ 7 render
def encode_args(kbps=None, intermediate=False):
    enc = C.video_encoder()
    if enc == "libx264":
        if intermediate:
            return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "12"]
        return ["-c:v", "libx264", "-preset", "medium", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k",
                "-bufsize", f"{int(kbps * 2)}k", "-profile:v", "high"]
    if intermediate:
        return ["-c:v", enc, "-b:v", "80M" if ST["output_scale"] > 1 else "40M"]
    return ["-c:v", enc, "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.3)}k", "-profile:v", "high"]


def filter_delay(cut_wav, final_a):
    """seconds final_a lags a plain atempo copy of cut_wav (envelope cross-correlation, 1 ms steps)"""
    def load(p, af=None):
        cmd = ["ffmpeg", "-v", "error", "-i", p] + (["-af", af] if af else []) + ["-ac", "1", "-ar", "8000", "-f",
                                                                                 "f32le", "-"]
        return np.frombuffer(subprocess.run(cmd, capture_output=True).stdout, np.float32)
    a, b = load(cut_wav, f"atempo={speed()}"), load(final_a)
    h = 8

    def env(x):
        n = len(x) // h
        e = np.sqrt((x[:n * h].reshape(n, h) ** 2).mean(1))
        return e - e.mean()
    ea, eb = env(a), env(b)
    n = min(len(ea), len(eb))
    ea, eb = ea[:n], eb[:n]
    best, lag = None, 0
    for l in range(-20, 121):
        v = float(np.dot(ea[max(0, -l):n - max(0, l)], eb[max(0, l):n - max(0, -l)]))
        if best is None or v > best:
            best, lag = v, l
    return lag * h / 8000


def pick_theme(work):
    """ONE caption look per video (consistent inside the video). With unique_caption_looks (default) every video gets
    a freshly generated look - font, colours, highlight style, entry animation, case - whose signature has never been
    used for this style before; otherwise the built-in looks rotate (never one of the last 3).
    A re-render of the same video keeps its look. Returns (theme.json path, look name)."""
    tp = os.path.join(work, "theme.json")
    if os.path.exists(tp):
        th = jload(tp)["theme"]
        return tp, (th.get("name", "custom") if isinstance(th, dict) else th)
    from . import captions as CAP
    family = [t for t in ST["caption_themes"] if t in CAP.THEMES] or ["classic"]
    hp = os.path.join(C.WORKSPACE, f".caption_theme_history_{re.sub(r'[^a-z0-9]+', '-', ST.name.lower())}.json")
    hist = jload(hp) if os.path.exists(hp) else []
    if CAPTION_THEME:
        th, sig = CAPTION_THEME, CAPTION_THEME
    elif ST.get("unique_caption_looks", True):
        th = CAP.make_look(family, history=hist)
        sig = CAP.look_signature(th)
    else:
        options = [t for t in family if t not in hist[-3:]] or family
        th = sig = random.choice(options)
    os.makedirs(C.WORKSPACE, exist_ok=True)
    jsave(hp, (hist + [sig])[-200:])
    jsave(tp, {"theme": th})
    return tp, (th.get("name", "custom") if isinstance(th, dict) else th)


def measure_final_sync(work):
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        return None
    if not os.path.exists(C.FACE_MODEL):
        return None
    test = os.path.join(work, "synccheck.mp4")
    run(["ffmpeg", "-v", "error", "-y", "-i", os.path.join(work, "base_v.mp4"), "-i", os.path.join(work, "final_a.wav"),
         "-map", "0:v", "-map", "1:a", "-vf", f"setpts=PTS/{speed()},fps={FPS()},scale=360:-2", "-c:v", "libx264",
         "-preset", "ultrafast", "-crf", "23", "-c:a", "aac", "-shortest", test])
    try:
        d = probe(test)[0]
        r = subprocess.run([PY, "-m", "reel_editor.av_sync", test, "--windows", "6" if d > 24 else "3", "--dur",
                            f"{min(12.0, d):.1f}", "--json"], capture_output=True, text=True, check=True, cwd=C.ROOT)
        res = json.loads(r.stdout.strip().splitlines()[-1])
        good = [x["audio_late_sec"] for x in res if x.get("confidence", 0) >= 1.5 and abs(x["audio_late_sec"]) < 0.3]
        return statistics.median(good) if len(good) >= (3 if d > 24 else 2) else None
    except Exception as ex:
        log("sync check skipped:", str(ex)[:120])
        return None
    finally:
        if os.path.exists(test):
            os.remove(test)


def prepare_av(src, work, sp, sync):
    """the cut video + finished audio + verified lip sync (runs in a background thread while Claude plans)"""
    spans = sp["spans"]
    shift = sync["audio_shift"]
    fps = FPS()

    def frames(s, sh=None):
        f0 = round((s["a"] - (shift if sh is None else sh)) * fps)
        return f0, f0 + round((s["b"] - s["a"]) * fps)
    base_v = os.path.join(work, "base_v.mp4")

    def cut_video(sh):
        # select by FRAME NUMBER (time-based selection with rounded decimals silently drops frames -> lip drift);
        # a 60 fps / variable-rate phone recording is first resampled to the output fps
        vexpr = "+".join(f"between(n,{max(0, f0)},{f1 - 1})" for f0, f1 in (frames(x, sh) for x in spans))
        vsel = os.path.join(work, "vsel.txt")
        pre = f"fps={fps}," if abs(probe(src)[3] - fps) > 0.5 else ""
        with open(vsel, "w") as fh:
            fh.write(f"{pre}select='{vexpr}',setpts=N/{fps}/TB")
        log("cutting video")
        run(["ffmpeg", "-v", "error", "-y", "-i", src, "-an", C.script_flag("vf"), vsel, "-r", str(fps),
             *encode_args(intermediate=True), "-pix_fmt", "yuv420p", base_v])
    cut_video(shift)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.float32)
    nf = int(FADE * SR)
    ramp = np.sin(np.linspace(0, np.pi / 2, nf)).astype(np.float32)
    pieces = []
    for s in spans:
        f0, f1 = frames(s)
        if f0 < 0:
            f0, f1 = 0, f1 - f0
        n = int(round(f1 * SR / fps)) - int(round(f0 * SR / fps))
        i0 = int(round(f0 * SR / fps + shift * SR))
        seg = np.zeros(n, np.float32)
        lo, hi = max(0, i0), min(len(a), i0 + n)
        if hi > lo:
            seg[lo - i0:hi - i0] = a[lo:hi]
        if n > 2 * nf:
            seg[:nf] *= ramp
            seg[-nf:] *= ramp[::-1]
        pieces.append(seg)
    cut = np.concatenate(pieces)
    cut_wav = os.path.join(work, "cut.wav")
    with wave.open(cut_wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((np.clip(cut, -1, 1) * 32767).astype("<i2").tobytes())
    lufs = float(ST["loudness_lufs"])
    den = "highpass=f=70,"
    if ST["denoise"]:
        if os.path.exists(C.RNNOISE_MODEL):
            m = C.RNNOISE_MODEL.replace("\\", "/").replace(":", "\\:")
            den += f"arnndn=m='{m}':mix=1.0,"
        den += "afftdn=nr=12:nf=-45,"
    den += f"atempo={speed()}"
    log(f"audio: denoise + {speed()}x + loudness {lufs:.0f} LUFS")
    meas = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", cut_wav, "-af",
                           den + f",loudnorm=I={lufs}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                          capture_output=True, text=True).stderr
    m = json.loads(re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", meas, re.S)[-1])
    ln = (f"loudnorm=I={lufs}:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
          f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    final_a = os.path.join(work, "final_a.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", cut_wav, "-af", den + "," + ln + ",aresample=48000", "-ac", "2", final_a])
    fd_ = filter_delay(cut_wav, final_a)     # the denoisers add a small delay: measure it and trim it off
    if fd_ > 0.004:
        tmp_a = final_a + ".tmp.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", final_a, "-af",
             f"atrim=start={fd_:.4f},asetpts=PTS-STARTPTS,apad=pad_dur={fd_:.4f}", tmp_a])
        os.replace(tmp_a, final_a)
        log(f"audio filter delay {fd_ * 1000:.0f} ms removed")
    e = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", final_a, "-af", "ebur128=peak=true", "-f", "null",
                        "-"], capture_output=True, text=True).stderr
    li = float(re.findall(r"I:\s+(-?[0-9.]+) LUFS", e)[-1])
    pk = float(re.findall(r"Peak:\s+(-?[0-9.]+) dBFS", e)[-1])
    gain = min(lufs - li, -1.5 - pk)
    if gain > 0.2:
        tmp_a = final_a + ".tmp.wav"
        run(["ffmpeg", "-v", "error", "-y", "-i", final_a, "-af", f"volume={gain:.2f}dB", tmp_a])
        os.replace(tmp_a, final_a)
        log(f"loudness {li:.1f} -> {li + gain:.1f} LUFS")
    lag = measure_final_sync(work)
    if lag is not None and abs(lag - SYNC_BIAS) > 0.02:
        shift = round(shift + lag - SYNC_BIAS, 3)
        sync["audio_shift"] = shift
        sync["verified_fix"] = True
        jsave(os.path.join(work, "sync.json"), sync)
        log(f"lip sync {lag:+.3f}s -> shift {shift:+.3f}s, recutting video")
        cut_video(shift)
        lag = measure_final_sync(work)
    log("final lip sync: " + (f"audio {lag:+.3f}s behind the lips" if lag is not None else
                              "not measured (needs a visible face + mediapipe)"))
    return {"base_v": base_v, "final_a": final_a, "shift": shift, "lip_sync": lag}


def red_x(path):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (PIP_W, PIP_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = 60
    for a, b in (((m, m), (PIP_W - m, PIP_H - m)), ((PIP_W - m, m), (m, PIP_H - m))):
        d.line([a, b], fill=(235, 30, 45, 235), width=16)
    im.save(path)


DYNTEXT_RULES = """You are the motion-graphics editor for a landscape (16:9) video (talking head and/or screen
recording). Add dynamic on-screen text: a new item about every GAP s (start to start) wherever it fits, each one
RELEVANT to the exact words being said at that moment. Types (vary them, never the same type 3 times in a row):
- {"type":"pop","text":"ONE TAKE"}  1-3 word punch phrase, max 16 characters, for the emphasised idea
- {"type":"icon","icon":"<ICONS>","text":"Cuts the mess-ups"}  icon badge + label, max 26 characters
- {"type":"callout","text":"..."}  key-takeaway card, max 40 characters
- {"type":"stat","value":30,"prefix":"","suffix":"","label":"posts a day"}  count-up - ONLY numbers actually said
- {"type":"chips","items":["TikTok","YouTube"]}  2-6 short tags (max 14 characters each) for a list said out loud
- {"type":"steps","num":2,"text":"Open the settings"}  numbered step, for instructions
Every item also has "start" (seconds, when its words are said) and "dur" (1.8-3.2).
Rules:
- Only facts and ideas actually said; no invented claims, prices or numbers; no emojis.
- Write FRESH wording for THIS video, in the speaker's own terms. Do not fall back on stock filler phrases ("game
  changer", "let's go", "pro tip", "mind blown") unless the speaker says them; never repeat the same text twice.
- Items never overlap: next start >= previous start + previous dur + 0.3. Where nothing fits (silence, a demo
  playing), leave a gap rather than forcing filler.
Output ONLY a JSON list in time order.

SPOKEN LINES (start-end seconds: text):
"""


def step_dyntext(caps, final_dur, work):
    """landscape: plan the dynamic on-screen text (Claude); written once per video, kept on re-render"""
    out = os.path.join(work, "dyntext.json")
    if os.path.exists(out):
        return jload(out)
    from . import dyntext as DT
    lines = "\n".join(f"{c['start']:.2f}-{c['end']:.2f}: " + " ".join(w["w"] for w in c["words"]) for c in caps)
    gap = float(ST.get("landscape_text_gap", 4.0))
    prompt = (DYNTEXT_RULES.replace("GAP", f"{gap:.1f}").replace("<ICONS>", "|".join(DT.ICON_NAMES))
              + lines + f"\n\nVideo length: {final_dur:.1f} s.")
    log(f"planning dynamic on-screen text every ~{gap:.0f} s (Claude)")
    try:
        ev = ask_json(prompt, model="sonnet")
        ev = ev if isinstance(ev, list) else ev.get("events", [])
    except Exception as ex:
        log("dynamic text plan failed:", str(ex)[:120]); ev = []
    ev = DT.clean(ev, final_dur)
    jsave(out, ev)
    return ev


def step_render(src, work, sp, caps, plan, thumb, outp, av):
    W, H, S, fps = int(ST["width"]), int(ST["height"]), int(ST["output_scale"]), FPS()
    OW, OH = W * S, H * S
    final_dur = sp["cut_dur"] / speed()
    for e in plan:     # a B-roll that could not be found/downloaded becomes a punch-in, so the rhythm never breaks
        if e["type"] in ("broll", "pip") and not e.get("file"):
            log(f"no clip for B-roll at {e['start']:.1f}s -> punch-in instead")
            e["type"] = "zoom"
            e["dur"] = min(e["dur"], dur_limits()["zoom"][1])
    fx_ev = [e for e in plan if e["type"] in ("sticker", "text", "emoji")]
    fx_mov = os.path.join(work, "fx.mov")
    fxj = os.path.join(work, "fx_render.json")
    jsave(fxj, fx_ev)
    hide = [[b["start"], b["start"] + b["dur"]] for b in plan if b["type"] == "broll"] \
        if ST["captions_hide_under_broll"] else []
    caps_mov = os.path.join(work, "caps.mov")
    capj = os.path.join(work, "captions_render.json")
    jsave(capj, caps)
    theme, look = pick_theme(work)
    if LANDSCAPE:     # 16:9: no word-by-word subtitles; frequent dynamic on-screen text instead
        dyn = step_dyntext(caps, final_dur, work)
        dynj = os.path.join(work, "dyntext_render.json")
        jsave(dynj, dyn)
        jsave(fxj, [])
        log(f"rendering dynamic on-screen text ({look} look, {len(dyn)} items)")
        layers = [subprocess.Popen([PY, "-m", "reel_editor.dyntext", fxj, f"{final_dur:.3f}", fx_mov], cwd=C.ROOT),
                  subprocess.Popen([PY, "-m", "reel_editor.dyntext", dynj, f"{final_dur:.3f}", caps_mov, "--theme",
                                    theme], cwd=C.ROOT)]
    else:
        log(f"rendering captions ({look} look) + {len(fx_ev)} stickers/text cards (in parallel)")
        layers = [subprocess.Popen([PY, "-m", "reel_editor.fx", fxj, f"{final_dur:.3f}", fx_mov, "--theme", theme],
                                   cwd=C.ROOT),
                  subprocess.Popen([PY, "-m", "reel_editor.captions", capj, f"{final_dur:.3f}", caps_mov, "--hide",
                                    json.dumps(hide), "--theme", theme], cwd=C.ROOT)]
    titles = [e for e in plan if e["type"] == "title" and not LANDSCAPE]
    titles_mov = os.path.join(work, "titles.mov")
    if titles:
        tj = os.path.join(work, "titles_render.json")
        jsave(tj, titles)
        layers.append(subprocess.Popen([PY, "-m", "reel_editor.fx", tj, f"{final_dur:.3f}", titles_mov, "--theme",
                                        theme], cwd=C.ROOT))
    base_v, final_a = av["base_v"], av["final_a"]
    zooms = [e for e in plan if e["type"] == "zoom"]
    zoom_clips = []
    if zooms:       # smooth punch-ins: locked framing + one eased push, rendered as clips (camera.py)
        spec = {"base": base_v, "speed": speed(), "fps": fps, "ow": OW, "oh": OH, "zoom": float(ST["zoom"]),
                "push": float(ST["zoom_push"]), "model": C.FACE_MODEL,
                "events": [{"start": e["start"], "dur": e["dur"], "out": os.path.join(work, f"zoom{k:03d}.mp4")}
                           for k, e in enumerate(zooms)]}
        specp = os.path.join(work, "zoom_spec.json")
        jsave(specp, spec)
        log(f"rendering {len(zooms)} punch-ins (locked framing + eased push)")
        r = subprocess.run([PY, "-m", "reel_editor.camera", "zooms", specp], capture_output=True, text=True, cwd=C.ROOT)
        if r.returncode != 0:
            raise RuntimeError("punch-in render failed: " + r.stderr[-400:])
        info = json.loads(r.stdout.strip().splitlines()[-1])
        if info and not info[0]["tracked"]:
            log("  (no face tracking: punch-ins framed on the usual selfie position)")
        zoom_clips = list(zip(zooms, [x["out"] for x in info]))
        from . import motion_check as MC     # camera QA: each punch-in on its own (captions/B-roll excluded)
        cam = []
        for e, path in zoom_clips:
            r_ = MC.check(path, at=[0.0], ln=e["dur"])[0]
            cam.append({"start": e["start"], "jolts": r_["jolts"], "frames": r_["frames"]})
        bad = [c for c in cam if c["jolts"] > 0]
        av["camera"] = cam
        log("camera check: " + ("every punch-in smooth (0 jolts inside)" if not bad else
                                f"WARNING jolts inside {len(bad)} punch-in(s): {bad}"))
    for pr in layers:
        if pr.wait() != 0:
            raise RuntimeError("caption/sticker layer render failed")
    inputs = ["-i", base_v, "-i", caps_mov, "-i", final_a, "-i", fx_mov]
    LZ = ":flags=lanczos" if S > 1 else ""
    fc = [f"[0:v]setpts=PTS/{speed()},fps={fps},scale={OW}:{OH}:force_original_aspect_ratio=increase{LZ},"
          f"crop={OW}:{OH},setsar=1[v0]"]
    cur, idx = "[v0]", 4
    clean_open = float(ST["clean_open"])
    if clean_open:
        fc.append("[v0]split[v0m][v0c]")
        cur = "[v0m]"
    for k, (e, path) in enumerate(zoom_clips):
        inputs += ["-i", path]
        fc.append(f"[{idx}:v]setpts=PTS-STARTPTS+{e['start']:.3f}/TB,setsar=1[z{k}]")
        fc.append(f"{cur}[z{k}]overlay=eof_action=pass:enable='between(t,{e['start']:.3f},"
                  f"{e['start'] + e['dur']:.3f})'[vz{k}]")
        cur, idx = f"[vz{k}]", idx + 1
    bw = "+".join(f"between(t,{e['start']:.3f},{e['start'] + e['dur']:.3f})" for e in plan if e["type"] == "bw")
    if bw:
        fc.append(f"{cur}split[bm][bs]")
        fc.append("[bs]hue=s=0,eq=contrast=1.15:brightness=0.01[bb]")
        fc.append(f"[bm][bb]overlay=enable='{bw}'[vbw]")
        cur = "[vbw]"
    for k, b in enumerate(plan):
        if b["type"] not in ("broll", "pip") or not b.get("file"):
            continue
        bd = probe(b["file"])[0]
        cs = b.get("clip_start", 0.3 if bd > b["dur"] + 0.8 else 0.0)
        en = f"between(t,{b['start']:.3f},{b['start'] + b['dur']:.3f})"
        inputs += ["-i", b["file"]]
        if b["type"] == "broll":
            fc.append(f"[{idx}:v]trim=start={cs:.3f}:duration={b['dur']:.3f},setpts=PTS-STARTPTS,fps={fps},"
                      f"scale={OW}:{OH}:force_original_aspect_ratio=increase{LZ},crop={OW}:{OH},setsar=1,"
                      f"setpts=PTS+{b['start']:.3f}/TB[b{k}]")
            fc.append(f"{cur}[b{k}]overlay=eof_action=pass:enable='{en}'[v{k + 1}]")
            cur, idx = f"[v{k + 1}]", idx + 1
        else:
            fc.append(f"[{idx}:v]trim=start={cs:.3f}:duration={b['dur']:.3f},setpts=PTS-STARTPTS,fps={fps},"
                      f"scale={PIP_W * S}:{PIP_H * S}:force_original_aspect_ratio=increase{LZ},crop={PIP_W * S}:"
                      f"{PIP_H * S},setsar=1,setpts=PTS+{b['start']:.3f}/TB[p{k}]")
            fc.append(f"{cur}[p{k}]overlay=x=0:y={PIP_Y * S}:eof_action=pass:enable='{en}'[vp{k}]")
            cur, idx = f"[vp{k}]", idx + 1
            if b.get("cross"):
                xp = os.path.join(work, "cross.png")
                if not os.path.exists(xp):
                    red_x(xp)
                inputs += ["-loop", "1", "-i", xp]
                fc.append(f"[{idx}:v]scale={PIP_W * S}:{PIP_H * S}[xc{k}]")
                fc.append(f"{cur}[xc{k}]overlay=x=0:y={PIP_Y * S}:enable='{en}':shortest=0[vx{k}]")
                cur, idx = f"[vx{k}]", idx + 1

    def layer(i):
        if S == 1:
            return f"[{i}:v]"
        fc.append(f"[{i}:v]scale={OW}:{OH}:flags=lanczos[ly{i}]")
        return f"[ly{i}]"
    fc.append(f"{cur}{layer(3)}overlay=format=auto[vfx]")
    if titles:
        inputs += ["-i", titles_mov]
        fc.append(f"[vfx]{layer(idx)}overlay=format=auto[vtt]")
        idx += 1
        fc.append(f"[vtt]{layer(1)}overlay=format=auto[vc]")
    else:
        fc.append(f"[vfx]{layer(1)}overlay=format=auto[vc]")
    cur = "[vc]"
    logo = ST.resolve(ST["brand_logo"])
    ls = logo_spans(caps)
    if ls and logo and os.path.exists(logo):
        inputs += ["-loop", "1", "-i", logo]
        en = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in ls)
        lw_, lcx, lcy = (360, W - 250, 130) if LANDSCAPE else (LOGO_W, LOGO_CX, LOGO_CY)
        fc.append(f"[{idx}:v]scale={lw_ * S}:-1,format=rgba[lg]")
        fc.append(f"{cur}[lg]overlay=x={lcx * S}-w/2:y={lcy * S}-h/2:enable='{en}':shortest=0[vl]")
        cur, idx = "[vl]", idx + 1
    if clean_open:
        fc.append(f"{cur}[v0c]overlay=enable='lt(t,{clean_open})':eof_action=pass[vco]")
        cur = "[vco]"
    if thumb:
        inputs += ["-loop", "1", "-t", "0.2", "-i", thumb]
        fc.append(f"[{idx}:v]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},setsar=1,fps={fps}[th]")
        fc.append(f"{cur}[th]overlay=enable='lt(n,{int(ST['thumbnail_frames'])})':eof_action=pass[vt]")
        cur, idx = "[vt]", idx + 1
    fc.append(f"{cur}format=yuv420p[vout]")
    fcp = os.path.join(work, "compose.txt")
    with open(fcp, "w") as fh:
        fh.write(";\n".join(fc))
    try:     # export at the raw recording's own bitrate (never below 12 Mbps, capped at 60; 4K >= 45 Mbps)
        src_kbps = int(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                                "stream=bit_rate", "-of", "csv=p=0", src]).decode().strip()) // 1000
    except Exception:
        src_kbps = 0
    vbr = max(12000, min(60000, src_kbps)) if S == 1 else max(45000, min(100000, src_kbps))
    log(f"final render: {OW}x{OH} {fps} fps at {vbr / 1000:.0f} Mbps ({C.video_encoder()})")
    run(["ffmpeg", "-v", "error", "-y", *inputs, C.script_flag("filter_complex"), fcp, "-map", "[vout]", "-map", "2:a",
         "-t", f"{final_dur:.3f}", "-r", str(fps), *encode_args(vbr), "-pix_fmt", "yuv420p", "-tag:v", "avc1",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", outp])
    log("done:", outp)


def norm_words(t):
    t = t.lower().replace("#", " number ").replace("$", " ")
    for d, w in (("1", "one"), ("2", "two"), ("3", "three"), ("4", "four"), ("5", "five")):
        t = re.sub(rf"\b{d}\b", w, t)
    return re.sub(r"[^a-z0-9' ]", " ", t).split()


def qa_final(audio_path, caps, work, lufs):
    """Automatic final check: every caption word is actually heard (re-transcribed), no pause over 0.35 s,
    loudness on target. Returns a list of problems (empty = passed)."""
    from . import transcribe as TR
    issues = []
    wav = os.path.join(work, "qa.wav")
    run(["ffmpeg", "-v", "error", "-y", "-i", audio_path, "-ac", "1", "-ar", "16000", wav])
    ref = [(w, c) for c in caps for w in norm_words(" ".join(x["w"] for x in c["words"]))]
    try:
        hyp = norm_words(TR.transcribe_text(wav))
    except Exception as ex:
        issues.append(f"word check could not run: {str(ex)[:80]}")
        hyp = None
    if hyp is not None:
        import difflib
        sm = difflib.SequenceMatcher(None, [w for w, _ in ref], hyp, autojunk=False)
        for op, a, b, c, d in sm.get_opcodes():
            if op in ("delete", "replace") and b - a >= 2:
                seg_caps = {id(ref[k][1]): ref[k][1] for k in range(a, b)}.values()
                t0 = max(0.0, min(x["start"] for x in seg_caps) - 0.6)
                t1 = max(x["end"] for x in seg_caps) + 0.6
                seg = os.path.join(work, "qa_seg.wav")
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.2f}", "-t", f"{t1 - t0:.2f}", "-i", wav, seg])
                got = norm_words(TR.transcribe_text(seg))
                missing = [w for w, _ in ref[a:b] if w not in got]
                if len(missing) >= 2:
                    issues.append(f"words possibly missing near {t0:.0f}s: {' '.join(missing)}")
                os.remove(seg)
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", wav, "-af", "silencedetect=n=-35dB:d=0.35", "-f",
                          "null", "-"], capture_output=True, text=True).stderr
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                         wav]).decode())
    for st, ln in zip(re.findall(r"silence_start: ([0-9.]+)", err), re.findall(r"silence_duration: ([0-9.]+)", err)):
        if float(st) + float(ln) < dur - 0.6 and float(st) > 0.3:
            issues.append(f"pause of {float(ln):.2f}s at {float(st):.1f}s")
    e = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", audio_path, "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    li = float(re.findall(r"I:\s+(-?[0-9.]+) LUFS", e)[-1])
    if abs(li - lufs) > 0.6:
        issues.append(f"loudness {li:.1f} LUFS (target {lufs:.0f})")
    os.remove(wav)
    jsave(os.path.join(work, "qa.json"), {"issues": issues, "lufs": li})
    log("final check:", "PASSED" if not issues else "FLAGGED - " + "; ".join(issues))
    return issues


def write_credits(plan, outp):
    used = [e for e in plan if e["type"] in ("broll", "pip") and e.get("file") and e.get("source")]
    if not used:
        return None
    p = os.path.splitext(outp)[0] + " - credits.txt"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("Stock footage used in this video\n\n")
        for e in used:
            fh.write(f"- {e.get('title') or e.get('id')} by {e.get('author') or 'unknown'} "
                     f"({e['source']}) {e.get('page_url') or ''}\n")
    return p


STEPS = ["transcribe", "takes", "spans", "sync", "captions", "visuals", "render"]
STEP_FILES = {"transcribe": "words.json", "takes": "takes.json", "spans": "spans.json", "sync": "sync.json",
              "captions": "captions.json", "visuals": "visuals.json"}


def edit(src, out=None, thumb=None, work=None, style_file=None, notes="", until=None, redo=(), caption_theme=None,
         broll=None, keep_work=True):
    """Run the whole pipeline. Returns a dict with the output paths and checks."""
    global ST, PROVIDER, CAPTION_THEME, T0
    T0 = time.time()
    src = os.path.abspath(src)
    ST = Style(style_file or C.STYLE_FILE)
    CAPTION_THEME = caption_theme
    global LANDSCAPE
    fmt = str(ST.get("format", "auto")).lower()
    LANDSCAPE = fmt == "landscape" or (fmt == "auto" and source_is_landscape(src))
    if LANDSCAPE:
        ST.s["width"], ST.s["height"] = [int(x) for x in ST.get("landscape_size", [1920, 1080])]
    from .broll import get_provider
    PROVIDER = get_provider(broll, log=log)
    base = os.path.splitext(os.path.basename(src))[0]
    work = work or os.path.join(tempfile.gettempdir(), "reel-" + re.sub(r"\W+", "-", base))
    os.makedirs(work, exist_ok=True)
    open(os.path.join(work, ".reel-work"), "w").close()
    brain.USAGE_FILE = os.path.join(work, "usage.json")
    brain.NOTES = notes or ""
    redo_from = min([STEPS.index(s) for s in redo], default=None)
    if redo_from is not None:
        for s in STEPS[redo_from:]:
            f = STEP_FILES.get(s)
            if f and os.path.exists(os.path.join(work, f)):
                os.remove(os.path.join(work, f))
    stop = STEPS.index(until) if until else len(STEPS) - 1
    log(f"work dir: {work} | style: {ST.name} | brain: {brain.mode()} | B-roll: {PROVIDER.name if PROVIDER else 'none'}"
        f" | format: {'landscape 16:9 (dynamic text)' if LANDSCAPE else 'vertical 9:16 (full subtitles)'}")
    dur = probe(src)[0]
    words = step_transcribe(src, work)
    if stop < 1:
        return {"work": work}
    takes = step_takes(words, work)
    if stop < 2:
        return {"work": work}
    sp = step_spans(words, takes, dur, work)
    if stop < 3:
        return {"work": work}
    sync = step_sync(work)
    if stop < 4:
        return {"work": work}
    av_box = {}

    def _av():
        try:
            av_box["av"] = prepare_av(src, work, sp, sync)
        except Exception as ex:
            av_box["err"] = ex
    av_thread = threading.Thread(target=_av, daemon=True)
    if stop >= 6:
        av_thread.start()
    kw = kept_words_final(words, sp)
    raw_box, vis_thread = {}, None
    if stop >= 5 and not os.path.exists(os.path.join(work, "visuals.json")):
        vis_thread = threading.Thread(target=lambda: raw_box.update(
            plan=plan_visuals_raw(phrase_lines_from_words(kw), sp["final_dur"])), daemon=True)
        vis_thread.start()
    caps = step_captions(kw, work)
    if stop < 5:
        return {"work": work}
    if vis_thread:
        vis_thread.join()
    plan = step_visuals(caps, work, sp["final_dur"], raw_box.get("plan"))
    write_post(caps, work)
    if stop < 6:
        return {"work": work}
    outdir = os.path.join(C.WORKSPACE, "finished", base)
    os.makedirs(outdir, exist_ok=True)
    outp = os.path.abspath(out or os.path.join(outdir, base + "-edited.mp4"))
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    av_thread.join()
    if "err" in av_box:
        raise av_box["err"]
    av = av_box["av"]
    qa_box = {}
    qa_thread = threading.Thread(target=lambda: qa_box.update(
        issues=qa_final(av["final_a"], caps, work, float(ST["loudness_lufs"]))), daemon=True)
    qa_thread.start()      # the word/pause/loudness check runs on the finished audio during the render
    step_render(src, work, sp, caps, plan, thumb, outp, av)
    qa_thread.join()
    jsave(os.path.join(work, "visuals.json"), plan)
    stem = os.path.splitext(outp)[0]
    sheet = stem + " - review.jpg"
    from . import qa_sheet
    qa_sheet.make(outp, plan, sheet)
    credits = write_credits(plan, outp)
    up = os.path.join(work, "usage.json")
    tot = jload(up) if os.path.exists(up) else {k: 0 for k in brain.USAGE}
    jsave(stem + " - claude-usage.json", tot)
    if os.path.exists(os.path.join(work, "post.json")):
        shutil.copy(os.path.join(work, "post.json"), stem + " - post.json")
    log(f"Claude: {tot['calls']} calls, ${tot['cost_usd']:.2f} "
        + ("(list-price equivalent on your Claude plan)" if brain.mode() == "cli" else "(API billing)"))
    log(f"review sheet: {sheet}")
    res = {"out": outp, "sheet": sheet, "credits": credits, "qa": qa_box.get("issues", []), "usage": tot,
           "lip_sync": av.get("lip_sync"), "camera": av.get("camera", []), "work": work, "post": stem + " - post.json"}
    if not keep_work:
        clean(work)
    return res


def clean(work):
    """delete a work dir (it holds transcript-derived files)"""
    if work and os.path.isdir(work) and os.path.exists(os.path.join(work, ".reel-work")):
        shutil.rmtree(work)
        return True
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m reel_editor edit")
    ap.add_argument("input")
    ap.add_argument("--out")
    ap.add_argument("--thumb")
    ap.add_argument("--work")
    ap.add_argument("--style", help="path to a STYLE.md (default: STYLE_FILE in .env, else styles/default)")
    ap.add_argument("--notes", default="", help="change requests for a redo")
    ap.add_argument("--caption-theme")
    ap.add_argument("--broll", choices=["stock", "envato", "none"], help="override BROLL_SOURCE")
    ap.add_argument("--until", choices=STEPS)
    ap.add_argument("--redo", nargs="*", default=[], choices=STEPS)
    ap.add_argument("--clean", action="store_true", help="delete the work dir when done")
    ap.add_argument("--result-json", help="also write the full result (incl. Claude usage) to this file")
    a = ap.parse_args(argv)
    res = edit(a.input, a.out, a.thumb, a.work, a.style, a.notes, a.until, a.redo, a.caption_theme, a.broll,
               keep_work=not a.clean)
    if a.result_json:
        with open(a.result_json, "w") as fh:
            json.dump(res, fh, indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k != "usage"}, indent=1))


if __name__ == "__main__":
    main()
