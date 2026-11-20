"""Smooth virtual camera for punch-ins.

The rule (learned the hard way: auto-tracking that follows every head bob looks like a shaky handheld camera):
  1. LOCKED FRAMING - one framing per shot: the face position is the MEDIAN over the whole punch-in window,
     taken from face tracking sampled at 2 fps and linearly INTERPOLATED between samples (nearest-sample lookups
     make the framing step twice a second).
  2. ONE EASED PUSH - the only camera move inside a shot is a slow push (zoom -> zoom + push) with smooth
     ease-in/ease-out, rendered with sub-pixel resampling so it never steps.
  3. Hard cut in, hard cut out. Then run `python -m reel_editor.motion_check final.mp4` before delivery.

Runs as its own process (python -m reel_editor.camera zooms spec.json) so OpenCV/MediaPipe never share a process
with the speech recognizer. Without mediapipe the framing falls back to the usual selfie position.
"""
import json
import subprocess
import sys

import numpy as np


def smoothstep(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _frames(path, ss, n, OW, OH, fps, speed=1.0, scale=1):
    """yield n rgb frames (OH/scale x OW/scale) of `path` from ss, in the composed geometry"""
    w, h = OW // scale, OH // scale
    vf = (f"setpts=(PTS-STARTPTS)/{speed},fps={fps},scale={OW}:{OH}:force_original_aspect_ratio=increase,"
          f"crop={OW}:{OH}" + (f",scale={w}:{h}" if scale > 1 else ""))
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{ss:.3f}", "-i", path, "-an", "-vf", vf,
                          "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = w * h * 3
    try:
        for _ in range(n):
            b = p.stdout.read(size)
            if len(b) < size:
                break
            yield np.frombuffer(b, np.uint8).reshape(h, w, 3)
    finally:
        p.stdout.close()
        p.wait()


def track_faces(video, OW, OH, fps_sample=2.0, model=None):
    """face centre + height over the video's own time, in OWxOH composed-frame pixels -> dict or None"""
    try:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
    except ImportError:
        return None
    import os
    if not model or not os.path.exists(model):
        return None
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                         "csv=p=0", video]).decode())
    n = max(1, int(dur * fps_sample))
    sc = 4
    opts = vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=model),
                                        running_mode=vision.RunningMode.VIDEO, num_faces=1)
    ts, xs, ys, hs = [], [], [], []
    with vision.FaceLandmarker.create_from_options(opts) as lm:
        for i, fr in enumerate(_frames(video, 0.0, n, OW, OH, fps_sample, 1.0, sc)):
            t = i / fps_sample
            r = lm.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(fr)),
                                    int(t * 1000))
            if r.face_landmarks:
                L = r.face_landmarks[0]
                ts.append(t)
                xs.append(float(np.mean([p.x for p in L])) * OW)
                ys.append(float(np.mean([p.y for p in L])) * OH)
                hs.append(abs(L[152].y - L[10].y) * OH)
    if len(ts) < 2:
        return None
    return {"t": ts, "x": xs, "y": ys, "h": hs}


def steady(track, a, b, OW, OH):
    """locked framing for one shot: median of the interpolated face position over [a, b] (track time)"""
    if not track:
        return OW / 2, OH * 0.32
    t = np.array(track["t"])
    grid = np.arange(a, max(b, a + 0.01), 0.25)
    xs = np.interp(grid, t, track["x"])
    ys = np.interp(grid, t, track["y"])
    return float(np.median(xs)), float(np.median(ys))


def window(cx, cy, z, OW, OH):
    """top-left of the crop (w = OW/z) that puts the face ~40% down the frame, kept inside the picture"""
    w, h = OW / z, OH / z
    x0 = min(max(cx - w / 2, 0.0), OW - w)
    y0 = min(max(cy - h * 0.40, 0.0), OH - h)
    return x0, y0


def render_zoom(base, out, start, dur, speed, fps, OW, OH, center, zoom, push):
    """one punch-in clip in the FINAL timeline: locked framing + one eased push, sub-pixel resampled"""
    import cv2
    n = max(1, int(round(dur * fps)))
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OW}x{OH}",
                            "-r", str(fps), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
                            "-pix_fmt", "yuv420p", out], stdin=subprocess.PIPE)
    cx, cy = center
    k = 0
    for k, fr in enumerate(_frames(base, start * speed, n, OW, OH, fps, speed)):
        z = zoom + push * smoothstep(k / max(1, n - 1))
        x0, y0 = window(cx, cy, z, OW, OH)
        M = np.array([[z, 0, -x0 * z], [0, z, -y0 * z]], np.float32)
        img = cv2.warpAffine(fr, M, (OW, OH), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        enc.stdin.write(img.tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("zoom clip encode failed")
    return k + 1


def zooms(spec):
    """spec: {base, speed, fps, ow, oh, zoom, push, model, events: [{start, dur, out}]}"""
    OW, OH, sp = spec["ow"], spec["oh"], spec["speed"]
    track = track_faces(spec["base"], OW, OH, model=spec.get("model"))
    res = []
    for e in spec["events"]:
        c = steady(track, e["start"] * sp, (e["start"] + e["dur"]) * sp, OW, OH)
        render_zoom(spec["base"], e["out"], e["start"], e["dur"], sp, spec["fps"], OW, OH, c,
                    spec.get("zoom", 1.15), spec.get("push", 0.04))
        res.append({"out": e["out"], "center": [round(c[0]), round(c[1])], "tracked": bool(track)})
    return res


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "zooms":
        with open(sys.argv[2]) as fh:
            print(json.dumps(zooms(json.load(fh))))
    else:
        print("usage: python -m reel_editor.camera zooms spec.json")
        sys.exit(2)
