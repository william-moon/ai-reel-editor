"""Settings: everything comes from the environment or the repo's local .env file (never committed).

Nothing here is user-specific: paths default to folders inside the repo or the user's home directory.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(ROOT, ".env")
ASSETS = os.path.join(ROOT, "assets")
FONTS = os.path.join(ASSETS, "fonts")


def load_env(path=ENV_FILE):
    """Minimal .env reader (KEY=VALUE, # comments, optional quotes). Real environment variables win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            if k and k not in os.environ:
                os.environ[k] = v


def set_env_value(key, value, path=ENV_FILE):
    """Write/replace KEY=VALUE in .env (creates it). Never prints the value."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        raise ValueError("bad key name")
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    out, done = [], False
    for line in lines:
        if re.match(rf"\s*{re.escape(key)}\s*=", line):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{key}={value}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    os.environ[key] = value


load_env()


def env(key, default=""):
    return os.environ.get(key, default)


def env_path(key, default):
    """a path setting; relative paths are relative to the repo folder"""
    p = os.path.expanduser(env(key, "") or default)
    return os.path.abspath(p if os.path.isabs(p) else os.path.join(ROOT, p))


# ---- folders
WORKSPACE = env_path("REEL_WORKSPACE", os.path.join(ROOT, "workspace"))
CACHE = os.path.join(WORKSPACE, "cache")
BROLL_CACHE = os.path.join(CACHE, "broll")
EMOJI_CACHE = os.path.join(CACHE, "emoji")
MODELS = env_path("REEL_MODELS", os.path.join(ROOT, "models"))
JOBS = os.path.join(WORKSPACE, "jobs")
FACE_MODEL = os.path.join(MODELS, "face_landmarker.task")
RNNOISE_MODEL = os.path.join(MODELS, "sh.rnnn")

# ---- brain (Claude)
CLAUDE_MODE = env("CLAUDE_MODE", "auto")             # auto | cli | api
MODEL_FAST = env("MODEL_FAST", "claude-sonnet-5-5")   # routine picks
MODEL_SMART = env("MODEL_SMART", "claude-opus-5-5")   # hard judgment


def claude_cli():
    """The Claude Code command as a list. CLAUDE_CLI may include a wrapper, e.g. "/path/wrapper.sh claude"."""
    raw = env("CLAUDE_CLI", "claude")
    return shlex.split(raw, posix=(os.name != "nt"))


# ---- B-roll
BROLL_SOURCE = env("BROLL_SOURCE", "stock")          # stock (Pexels + Pixabay) | envato | none
ENVATO_CDP = env("ENVATO_CDP", "http://127.0.0.1:9222")

# ---- storage
STORAGE = env("STORAGE", "local")                    # local | gdrive
LOCAL_ROOT = env_path("LOCAL_ROOT", os.path.join(os.path.expanduser("~"), "ReelEditor"))
GDRIVE_REMOTE = env("GDRIVE_REMOTE", "gdrive")
RAW_FOLDER = env("RAW_FOLDER", "Raw Videos")
FINISHED_FOLDER = env("FINISHED_FOLDER", "Finished Videos")

# ---- transcription
WHISPER_BACKEND = env("WHISPER_BACKEND", "auto")     # auto | faster | openai | groq
WHISPER_MODEL = env("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = env("WHISPER_DEVICE", "auto")
LANGUAGE = env("LANGUAGE", "en")

# ---- style
STYLE_FILE = env_path("STYLE_FILE", os.path.join(ROOT, "styles", "default", "STYLE.md"))


def ffmpeg_major():
    try:
        out = subprocess.check_output(["ffmpeg", "-version"], text=True).splitlines()[0]
        m = re.search(r"version n?(\d+)", out)
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


_FFV = None


def script_flag(kind):
    """ffmpeg 7+ loads an option value from a file with '-/opt file'; older builds use *_script options."""
    global _FFV
    if _FFV is None:
        _FFV = ffmpeg_major()
    if _FFV >= 7:
        return {"filter_complex": "-/filter_complex", "vf": "-/vf"}[kind]
    return {"filter_complex": "-filter_complex_script", "vf": "-filter_script:v"}[kind]


def video_encoder():
    """VIDEO_ENCODER=auto picks Apple's hardware encoder on macOS, libx264 elsewhere."""
    want = env("VIDEO_ENCODER", "auto")
    if want != "auto":
        return want
    if sys.platform == "darwin":
        try:
            enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
            if "h264_videotoolbox" in enc:
                return "h264_videotoolbox"
        except Exception:
            pass
    return "libx264"


def have(cmd):
    return shutil.which(cmd) is not None
