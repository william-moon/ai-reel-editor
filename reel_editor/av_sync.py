#!/usr/bin/env python3
"""Measure audio/video (lip-sync) offset of a talking-head clip.

Mouth opening per frame (MediaPipe face landmarks) is cross-correlated with the speech
energy envelope. Output: the lag in seconds; positive = AUDIO IS LATE (audio must be moved
EARLIER by that amount), negative = audio is early.

  python -m reel_editor.av_sync <video> [--start S] [--dur D] [--windows N] [--json]
Needs the optional mediapipe package + models/face_landmarker.task (python -m reel_editor fetch-assets).
Measures N windows spread across the clip so drift (offset changing over time) shows up.
"""
import argparse, json, subprocess, sys, os
import numpy as np, cv2
import mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions

from .config import FACE_MODEL as MODEL
FPS = 30.0

def probe_dur(p):
    return float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                          "-of", "csv=p=0", p]).decode().strip())

def mouth_signal(path, start, dur):
    cmd = ["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", path, "-an",
           "-vf", f"fps={FPS},scale=360:-2", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    # get frame size
    w = 360
    h = int(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                     "stream=width,height", "-of", "csv=p=0", path]).decode().split(",")[1])
    W0 = int(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width", "-of", "csv=p=0", path]).decode().strip())
    h = int(round(h * 360 / W0 / 2)) * 2
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (w * h * 3)
    frames = np.frombuffer(raw[: n * w * h * 3], np.uint8).reshape(n, h, w, 3)
    opts = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=MODEL),
                                        running_mode=vision.RunningMode.VIDEO, num_faces=1)
    out = np.full(n, np.nan)
    with vision.FaceLandmarker.create_from_options(opts) as lm:
        for i in range(n):
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frames[i]))
            r = lm.detect_for_video(img, int(i * 1000 / FPS))
            if r.face_landmarks:
                L = r.face_landmarks[0]
                # inner lips 13 (upper) / 14 (lower); normalise by face height 10..152
                face = abs(L[152].y - L[10].y) or 1e-6
                out[i] = abs(L[14].y - L[13].y) / face
    m = np.isnan(out)
    if m.all():
        return None
    out[m] = np.interp(np.flatnonzero(m), np.flatnonzero(~m), out[~m])
    return out

def audio_env(path, start, dur):
    sr = 16000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(dur), "-i", path, "-vn",
                          "-ac", "1", "-ar", str(sr), "-af", "highpass=f=150,lowpass=f=4000",
                          "-f", "s16le", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32)
    hop = int(sr / FPS)
    n = len(a) // hop
    rms = np.sqrt((a[: n * hop].reshape(n, hop) ** 2).mean(1) + 1e-9)
    return np.log(rms + 1.0)

def lag_of(v, a, maxlag=int(0.6 * FPS)):
    n = min(len(v), len(a))
    v = v[:n]; a = a[:n]
    def z(x):
        x = x - np.convolve(x, np.ones(15) / 15, mode="same")  # remove slow trend
        return (x - x.mean()) / (x.std() + 1e-9)
    v, a = z(v), z(a)
    best = None
    scores = {}
    for k in range(-maxlag, maxlag + 1):
        # positive k: audio[t+k] matches video[t]  -> audio is late by k frames
        if k >= 0:
            c = np.dot(v[: n - k], a[k:]) / (n - k)
        else:
            c = np.dot(v[-k:], a[: n + k]) / (n + k)
        scores[k] = c
        if best is None or c > scores[best]:
            best = k
    # sub-frame refine (parabola)
    if -maxlag < best < maxlag:
        y0, y1, y2 = scores[best - 1], scores[best], scores[best + 1]
        d = (y0 - y2) / (2 * (y0 - 2 * y1 + y2) + 1e-12)
    else:
        d = 0.0
    vals = np.array(list(scores.values()))
    conf = (scores[best] - np.median(vals)) / (vals.std() + 1e-9)
    return (best + d) / FPS, float(scores[best]), float(conf)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--start", type=float, default=None)
    ap.add_argument("--dur", type=float, default=40.0)
    ap.add_argument("--windows", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    total = probe_dur(a.video)
    starts = [a.start] if a.start is not None else \
        [max(0.0, (total - a.dur) * i / max(1, a.windows - 1)) for i in range(a.windows)]
    res = []
    for s in starts:
        v = mouth_signal(a.video, s, a.dur)
        if v is None:
            res.append({"start": round(s, 1), "error": "no face"}); continue
        lag, corr, conf = lag_of(v, audio_env(a.video, s, a.dur))
        res.append({"start": round(s, 1), "audio_late_sec": round(lag, 3), "corr": round(corr, 3),
                    "confidence": round(conf, 2)})
    if a.json:
        print(json.dumps(res))
    else:
        for r in res:
            print(r)

if __name__ == "__main__":
    main()
