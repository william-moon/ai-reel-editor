"""Generate a ~20 s talking-style test recording (a drawn face whose mouth moves with a synthetic voice), including
a false start + retake, so the whole pipeline can be tested without a real video.

  python -m reel_editor sample [out.mp4]

Voice: macOS `say`, Windows SAPI (PowerShell), Linux espeak-ng/espeak, or edge-tts if installed.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

LINES = [
    ("Here are three ways to save money on", 0.9),          # false start ...
    ("Wait, let me redo that.", 1.0),                        # ... retake cue
    ("Here are three simple ways to save money on groceries.", 0.7),
    ("Number one, make a list before you shop.", 0.6),
    ("Number two, never shop when you are hungry.", 0.6),
    ("Number three, buy the store brand.", 0.8),
    ("Follow for more tips like this.", 0.5),
]
SR = 22050


def tts(text, out_wav, tmp):
    """one line of speech -> mono wav at SR"""
    raw = os.path.join(tmp, "line.raw_audio")
    if sys.platform == "darwin" and shutil.which("say"):
        aiff = raw + ".aiff"
        subprocess.run(["say", "-r", "185", "-o", aiff, text], check=True)
        src = aiff
    elif os.name == "nt":
        wav = raw + ".wav"
        ps = ("Add-Type -AssemblyName System.Speech; $s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              f"$s.SetOutputToWaveFile('{wav}'); $s.Speak(\"{text}\"); $s.Dispose()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        src = wav
    elif shutil.which("espeak-ng") or shutil.which("espeak"):
        wav = raw + ".wav"
        subprocess.run([shutil.which("espeak-ng") or shutil.which("espeak"), "-s", "165", "-w", wav, text], check=True)
        src = wav
    else:
        try:
            import edge_tts  # noqa: F401
        except ImportError:
            raise SystemExit("No text-to-speech found: install espeak-ng (Linux) or `pip install edge-tts`")
        mp3 = raw + ".mp3"
        subprocess.run([sys.executable, "-m", "edge_tts", "--text", text, "--write-media", mp3], check=True)
        src = mp3
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-ac", "1", "-ar", str(SR), out_wav], check=True)


def read(p):
    with wave.open(p) as wf:
        return np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(np.float32) / 32768


def make(out="sample-raw.mp4", fps=30):
    from PIL import Image, ImageDraw
    tmp = tempfile.mkdtemp(prefix="reelsample")
    parts = [np.zeros(int(0.6 * SR), np.float32)]
    for k, (text, pause) in enumerate(LINES):
        w = os.path.join(tmp, f"l{k}.wav")
        tts(text, w, tmp)
        parts += [read(w), np.zeros(int(pause * SR), np.float32)]
    a = np.concatenate(parts)
    a = a / max(1e-6, np.abs(a).max()) * 0.6
    a += np.random.default_rng(1).normal(0, 0.002, len(a)).astype(np.float32)   # a little room noise
    wav = os.path.join(tmp, "voice.wav")
    with wave.open(wav, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    dur = len(a) / SR
    n = int(dur * fps)
    hop = SR // fps
    env = np.array([np.sqrt(np.mean(a[i * hop:(i + 1) * hop] ** 2)) for i in range(n)])
    env = env / max(1e-6, env.max())
    W, H = 540, 960
    bg = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(bg)
    for y in range(H):
        c = int(40 + 60 * y / H)
        d.line([(0, y), (W, y)], fill=(c, c + 20, c + 50))
    d.rectangle((60, 700, 480, 960), fill=(70, 90, 140))          # shoulders
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", str(fps), "-i", "-", "-i", wav, "-vf", "scale=1080:1920:flags=bicubic",
                            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                            "-c:a", "aac", "-b:a", "160k", "-shortest", out], stdin=subprocess.PIPE)
    for i in range(n):
        im = bg.copy()
        d = ImageDraw.Draw(im)
        cx, cy = 270, 330
        d.ellipse((cx - 120, cy - 150, cx + 120, cy + 150), fill=(236, 196, 160))     # head
        d.ellipse((cx - 60, cy - 40, cx - 30, cy - 10), fill=(40, 40, 40))           # eyes
        d.ellipse((cx + 30, cy - 40, cx + 60, cy - 10), fill=(40, 40, 40))
        m = 4 + 40 * float(env[i])                                                     # mouth opens with the voice
        d.ellipse((cx - 40, cy + 60 - m / 2, cx + 40, cy + 60 + m / 2), fill=(120, 30, 40))
        enc.stdin.write(im.tobytes())
    enc.stdin.close()
    enc.wait()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"sample written: {out} ({dur:.1f} s)")
    return out


if __name__ == "__main__":
    make(sys.argv[1] if len(sys.argv) > 1 else "sample-raw.mp4")
