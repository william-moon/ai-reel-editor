"""Audio analysis: speech islands, loudness envelopes, and snapping word edges to the real voice."""
import re
import subprocess
import wave

import numpy as np

HOP = 0.01   # 10 ms frames


def read_wav16(path):
    with wave.open(path) as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(np.float32) / 32768


def frame_db(a, sr=16000, hop=HOP):
    h = int(sr * hop)
    n = len(a) // h
    if n == 0:
        return np.full(1, -120.0)
    return 20 * np.log10(np.sqrt(np.mean(a[:n * h].reshape(n, h) ** 2, axis=1)) + 1e-9)


def load_energy(wav):
    """voiced/unvoiced per 10 ms frame, threshold just above the room noise"""
    db = frame_db(read_wav16(wav))
    floor = float(np.percentile(db, 5))
    thr = min(max(floor + 12, -55.0), -38.0)
    return [bool(x > thr) for x in db], thr


class Energy:
    def __init__(self, voiced):
        self.v = voiced

    def idx(self, t):
        return max(0, min(len(self.v) - 1, int(t / HOP)))

    def snap_end(self, t, hi):
        i = self.idx(t)
        if self.v[i]:
            while i < len(self.v) - 1 and self.v[i + 1] and (i * HOP - t) < 0.2 and i * HOP < hi:
                i += 1
            return min((i + 1) * HOP, hi)
        j = i
        while j > 0 and not self.v[j] and (i - j) * HOP < 0.25:
            j -= 1
        return min((j + 1) * HOP, hi) if self.v[j] else t


def refine_words(words, en, duration):
    """Snap each word to the voiced region: end nudged to where the voice stops, start = next voiced onset after a
    real (>= 80 ms) silence, otherwise the previous word's end."""
    v = en.v
    prev_e = 0.0
    for i, w in enumerate(words):
        e_raw = w["e"]
        lo = prev_e
        nxt = words[i + 1]["e"] if i + 1 < len(words) else duration
        e = en.snap_end(e_raw, max(e_raw, min(nxt - 0.03, e_raw + 0.3)))
        e = max(e, lo + 0.03)
        j = en.idx(lo)
        lim = en.idx(e + 0.1)
        s = max(lo, w["s"])
        while j < lim and v[j]:
            j += 1
        k = j
        while k < lim and not v[k]:
            k += 1
        if k < len(v) and v[k] and (k - j) * HOP >= 0.08 and k * HOP < e + 0.1:
            s = max(s, k * HOP)
        s = min(max(s, lo), e - 0.03)
        w["s"], w["e"] = s, e
        prev_e = e


def fix_long_words(words, voiced, hop=HOP):
    """After a long pause a word's start can collapse onto the previous word's end (a 'word' lasting seconds),
    which would keep the dead air. Cap each word at a plausible spoken length, snap to the voice onset."""
    for w in words:
        n = len(re.sub(r"[^A-Za-z0-9]", "", w["text"])) or 1
        est = min(0.95, 0.14 + 0.075 * n)
        if w["e"] - w["s"] <= est + 0.25:
            continue
        lo = max(w["s"], w["e"] - est - 0.45)
        hi = w["e"] - est * 0.4
        s_new = w["e"] - est
        i = int(hi / hop)
        while i > int(lo / hop) and i < len(voiced):
            if voiced[i] and not voiced[i - 1]:
                s_new = i * hop
                break
            i -= 1
        w["s"] = max(w["s"], min(s_new, w["e"] - 0.08))


def speech_islands(wav, noise="-40dB", min_sil=0.45, pad=0.25):
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                         "csv=p=0", wav]).decode())
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", wav, "-af", f"silencedetect=n={noise}:d={min_sil}", "-f",
                          "null", "-"], capture_output=True, text=True).stderr
    st = [float(x) for x in re.findall(r"silence_start: ([0-9.]+)", err)]
    en = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", err)]
    isl, prev = [], 0.0
    for a, b in zip(st, en + [dur] * (len(st) - len(en))):
        if a - prev > 0.15:
            isl.append([max(0.0, prev - pad), min(dur, a + pad)])
        prev = b
    if dur - prev > 0.15:
        isl.append([max(0.0, prev - pad), dur])
    merged = []
    for a, b in isl:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return merged


def voice_db(wav):
    """10 ms loudness (dBFS) + a voice threshold relative to the speech level (raw files are mostly near-silent
    pauses, so a pure noise-floor rule sits too low and keeps breaths): ~32 dB under loud speech, >= floor + 15"""
    db = frame_db(read_wav16(wav))
    floor, loud = float(np.percentile(db, 10)), float(np.percentile(db, 97))
    thr = min(-35.0, max(floor + 15.0, loud - 32.0))
    return db, thr


def voice_onset(db, thr, lo, hi, run=4):
    i0, i1 = max(0, int(lo / HOP)), min(len(db), int(hi / HOP))
    k = 0
    for i in range(i0, i1):
        k = k + 1 if db[i] > thr else 0
        if k >= run:
            return (i - run + 1) * HOP
    return None


def voice_offset(db, thr, lo, hi, run=3):
    i0, i1 = max(0, int(lo / HOP)), min(len(db), int(hi / HOP))
    k = 0
    for i in range(i1 - 1, i0 - 1, -1):
        k = k + 1 if db[i] > thr else 0
        if k >= run:
            return (i + run) * HOP
    return None


# ---- clean cut points: a clip starts in the silence right before its first word and ends in the silence right
# after its last word, so no sliver of a neighbouring (cut) word is ever heard. Whisper word times are often
# 50-200 ms off, so the cut is placed from the loudness curve, not from the word times.
QUIET_RUN = 3          # >= 30 ms below the voice threshold counts as a gap between words


SOFT_DB = 10.0         # soft consonants (s, f, h, th, p) and word tails sit up to this far under the voice threshold
SOFT_MAX = 0.15        # ... and last at most this long beyond the voiced part
TOL = 0.10             # how far whisper's word edge may be from the real one
SOFT_GUARD = 0.06      # when the "soft" sound never stops (room noise / breath, not a consonant): keep this much


def _dip(db, a, b):
    """quietest frame in [a, b) (frame indices), smoothed over 30 ms so a single dip does not win"""
    a, b = max(0, a), min(len(db), b)
    if b - a < 1:
        return a
    seg = np.asarray(db[a:b], dtype=float)
    if len(seg) >= 3:
        seg = np.convolve(seg, np.ones(3) / 3, mode="same")
    return a + int(np.argmin(seg))


def _deep_dip(db, a, b, depth=6.0):
    """the deepest frame in [a, b) that is a real gap between two sounds (>= depth dB under the loudest frame within
    50 ms on BOTH sides), or None"""
    best = None
    for m in range(max(1, a), min(len(db) - 1, b)):
        left, right = max(db[max(0, m - 5):m]), max(db[m + 1:m + 6])
        if db[m] <= min(left, right) - depth and (best is None or db[m] < db[best]):
            best = m
    return best


def clean_onset(db, thr, s, lo):
    """(onset, quiet_from) for the word whisper starts at s.
    onset = where its sound REALLY begins, soft first letters (s, f, h, th: quieter than the voice threshold)
    included, so the first letter is never clipped; quiet_from = where the silence before it begins (any cut in
    [quiet_from, onset] is clean). lo = earliest allowed time (about the end of the previous transcript word).
    No silence before the word (it runs on from the previous one): both = the quietest point between the two."""
    n = len(db)
    soft = thr - SOFT_DB
    i = min(n - 1, int((s + 0.03) / HOP))
    i_lo = max(0, int(lo / HOP))
    if db[i] <= thr:     # the word really starts later than whisper says: find its first voiced frame
        k = i
        while k < min(n - 1, i + 25) and db[k] <= thr:
            k += 1
        if db[k] > thr:
            i = k
    lim = max(i_lo, int((s - TOL) / HOP))
    while i > lim and db[i - 1] > thr:          # 1. back through the voiced start of the word
        i -= 1
    if i == lim and i > 0 and db[i - 1] > thr:  # still voiced: it runs on from the previous word
        m = _deep_dip(db, max(i_lo, int((s - 0.12) / HOP)), int((s + 0.10) / HOP))
        if m is None:
            m = _dip(db, max(i_lo, int((s - 0.12) / HOP)), int((s + 0.03) / HOP) + 1)
        return m * HOP, m * HOP
    k = i                                        # 2. back through a soft first letter (between soft and thr)
    while k > max(i_lo, i - int(SOFT_MAX / HOP)) and soft < db[k - 1] <= thr:
        k -= 1
    if k > 0 and db[k - 1] <= soft:              # 3a. real silence before it: clean
        j = k
        while j > i_lo and db[j - 1] <= thr - 3:
            j -= 1
        return k * HOP, j * HOP
    # 3b. no silence (another sound right before, or noise): cut at the quietest point between that and the word
    m = _dip(db, k, i + 1)
    return m * HOP, m * HOP


def clean_offset(db, thr, e, hi):
    """(offset, quiet_to) for the word whisper ends at e: mirror image of clean_onset. offset includes a soft tail
    (s, t, breathy end) so the last letter is never clipped, and it never runs on into the next word: when the
    sound keeps going, the cut is the quietest point between the two words."""
    n = len(db)
    soft = thr - SOFT_DB
    i = max(0, int((e - 0.03) / HOP))
    i_hi = min(n, int(hi / HOP))
    if db[i] <= thr:     # the word really ended earlier than whisper says: back up to its last voiced frame
        k = i
        while k > max(0, i - 25) and db[k] <= thr:
            k -= 1
        if db[k] > thr:
            i = k
    lim = min(i_hi - 1, int((e + TOL) / HOP))
    while i < lim and db[i + 1] > thr:
        i += 1
    if i == lim and i + 1 < n and db[i + 1] > thr:
        m = _deep_dip(db, int((e - 0.10) / HOP), min(i_hi, int((e + 0.12) / HOP)) + 1)
        if m is None:
            m = _dip(db, int((e - 0.06) / HOP), min(i_hi, int((e + 0.12) / HOP)) + 1)
        return m * HOP, m * HOP
    if i > int((e + 0.05) / HOP):                # ran on well past whisper's end: maybe that was the next word
        m = _deep_dip(db, int((e - 0.08) / HOP), i + 1)
        if m is not None:
            return m * HOP, m * HOP
    k = i
    while k < min(i_hi - 1, i + int(SOFT_MAX / HOP)) and soft < db[k + 1] <= thr:
        k += 1
    if k + 1 < n and db[k + 1] <= soft:
        j = k + 1
        while j < i_hi and db[j] <= thr - 3:
            j += 1
        return (k + 1) * HOP, j * HOP
    m = _dip(db, i, k + 2)
    return m * HOP, m * HOP


def edge_db(db, a, b, ms=30):
    """loudest 10 ms frame in the first and last `ms` of the clip [a, b] (join QA)"""
    k = max(1, int(ms / 1000 / HOP))
    ia, ib = int(a / HOP), int(b / HOP)
    head = db[ia:ia + k]; tail = db[max(ia, ib - k):ib]
    return (float(head.max()) if len(head) else -120.0, float(tail.max()) if len(tail) else -120.0)
