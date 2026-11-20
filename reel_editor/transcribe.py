"""Speech to words with timestamps.

Backends (WHISPER_BACKEND):
  faster  - faster-whisper (CTranslate2), runs on CPU everywhere incl. Windows; default
  openai  - openai-whisper (PyTorch), if you already have it
  groq    - Groq's hosted Whisper (needs GROQ_API_KEY); seconds instead of minutes on a slow VPS.
            Only the speech audio is uploaded.
  auto    - groq when GROQ_API_KEY is set, else faster, else openai.

Each stretch of speech is transcribed ON ITS OWN: whole-file transcription merges repeated takes
("click the link..." said twice becomes one), which is exactly what the take picker needs to see.
"""
import json
import os
import re
import time
import uuid
import wave

import numpy as np

from . import config as C

PROMPT = "Um, so, uh, let me start. Okay, again."
_MODEL = {}


def backend():
    b = C.WHISPER_BACKEND
    if b != "auto":
        return b
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    try:
        import faster_whisper  # noqa: F401
        return "faster"
    except ImportError:
        pass
    try:
        import whisper  # noqa: F401
        return "openai"
    except ImportError:
        raise RuntimeError("No speech recognizer installed: pip install faster-whisper (or set GROQ_API_KEY)")


def _read_wav(path):
    with wave.open(path) as wf:
        a = np.frombuffer(wf.readframes(wf.getnframes()), "<i2").astype(np.float32) / 32768
    return a


def _faster():
    if "faster" not in _MODEL:
        from faster_whisper import WhisperModel
        dev = C.WHISPER_DEVICE
        if dev == "auto":
            dev = "cuda" if _has_cuda() else "cpu"
        ct = "float16" if dev == "cuda" else "int8"
        _MODEL["faster"] = WhisperModel(C.WHISPER_MODEL, device=dev, compute_type=ct)
    return _MODEL["faster"]


def _has_cuda():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _openai():
    if "openai" not in _MODEL:
        import whisper
        name = {"large-v3-turbo": "turbo"}.get(C.WHISPER_MODEL, C.WHISPER_MODEL)
        _MODEL["openai"] = whisper.load_model(name)
    return _MODEL["openai"]


def _words_local(path):
    """-> [{"text", "s", "e"}] relative to the start of this file"""
    audio = _read_wav(path)
    out = []
    if backend() == "openai":
        r = _openai().transcribe(audio, language=C.LANGUAGE or None, word_timestamps=True, initial_prompt=PROMPT,
                                 condition_on_previous_text=False, fp16=False)
        for seg in r.get("segments", []):
            for w in seg.get("words", []):
                out.append({"text": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"])})
    else:
        segs, _ = _faster().transcribe(audio, language=C.LANGUAGE or None, word_timestamps=True,
                                       initial_prompt=PROMPT, condition_on_previous_text=False, vad_filter=False,
                                       beam_size=5)
        for seg in segs:
            for w in seg.words or []:
                out.append({"text": w.word.strip(), "s": float(w.start), "e": float(w.end)})
    return [w for w in out if re.search(r"[A-Za-z0-9]", w["text"])]


def _groq_one(path, text_only=False):
    import urllib.error
    import urllib.request
    key = os.environ["GROQ_API_KEY"]
    b = uuid.uuid4().hex
    fields = {"model": "whisper-large-v3-turbo", "response_format": "verbose_json", "temperature": "0",
              "prompt": PROMPT}
    if C.LANGUAGE:
        fields["language"] = C.LANGUAGE
    body = b"".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
                    for k, v in fields.items())
    if not text_only:
        body += f"--{b}\r\nContent-Disposition: form-data; name=\"timestamp_granularities[]\"\r\n\r\nword\r\n".encode()
    with open(path, "rb") as fh:
        body += (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                 f"Content-Type: audio/wav\r\n\r\n").encode() + fh.read() + f"\r\n--{b}--\r\n".encode()
    for attempt in range(6):
        req = urllib.request.Request("https://api.groq.com/openai/v1/audio/transcriptions", body,
                                     {"Authorization": f"Bearer {key}",
                                      "Content-Type": f"multipart/form-data; boundary={b}",
                                      "User-Agent": "ai-reel-editor/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(float(e.headers.get("retry-after") or 2 * (attempt + 1)))
                continue
            raise RuntimeError(f"groq {e.code}")
    raise RuntimeError("groq: too many retries")


def _groq_words(r):
    toks = (r.get("text") or "").split()
    key = lambda x: re.sub(r"[^a-z0-9]", "", x.lower())
    ti, out = 0, []
    for w in r.get("words") or []:
        t = w["word"].strip()
        if not re.search(r"[A-Za-z0-9]", t):
            continue
        for j in range(ti, min(ti + 4, len(toks))):   # take the punctuated form from the segment text
            if key(toks[j]) == key(t):
                t, ti = toks[j], j + 1
                break
        out.append({"text": t, "s": float(w["start"]), "e": float(w["end"])})
    return out


def transcribe_islands(islands, files, log=print):
    """islands [(a, b)] in seconds + one wav per island -> absolute word list"""
    be = backend()
    log(f"transcribing {len(files)} speech stretches ({be}, model {C.WHISPER_MODEL if be != 'groq' else 'groq turbo'})")
    if be == "groq":
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(8) as ex:
            res = [_groq_words(r) for r in ex.map(_groq_one, files)]
    else:
        res = [_words_local(f) for f in files]
    words = []
    for (a, b), ws in zip(islands, res):
        for w in ws:
            w = {"text": w["text"], "s": w["s"] + a, "e": min(w["e"] + a, b)}
            if words and w["s"] < words[-1]["e"]:
                w["s"] = words[-1]["e"]
            w["e"] = max(w["e"], w["s"] + 0.02)
            words.append(w)
    return words


def transcribe_text(path):
    """plain text of a whole (16 kHz mono wav) file, for the final word check"""
    if backend() == "groq":
        return _groq_one(path, text_only=True).get("text") or ""
    return " ".join(w["text"] for w in _words_local(path))
