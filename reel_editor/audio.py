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
