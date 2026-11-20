"""Where raw videos come in and finished videos go out.

STORAGE=local  - two folders on this computer (zero clicks):
                 LOCAL_ROOT/Raw Videos       drop raw recordings (+ an optional thumbnail image with the same name)
                 LOCAL_ROOT/Finished Videos  finished reels + review sheet + post copy
STORAGE=gdrive - the same two folders in Google Drive, through rclone (https://rclone.org). Setup needs ONE
                 "Allow" click on Google's consent page:  python -m reel_editor storage-setup gdrive
                 Headless server / VPS (no browser): on any computer WITH a browser run
                     rclone authorize "drive"
                 click Allow, copy the token it prints, then on the server:
                     python -m reel_editor storage-setup gdrive --token '<paste the token JSON>'

Processed raw files are moved to "Raw Videos/Done" (never deleted).
"""
import json
import os
import shutil
import subprocess
import time

from . import config as C

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")


class LocalStorage:
    name = "local"

    def __init__(self, root=None):
        self.root = root or C.LOCAL_ROOT
        self.raw = os.path.join(self.root, C.RAW_FOLDER)
        self.done = os.path.join(self.raw, "Done")
        self.finished = os.path.join(self.root, C.FINISHED_FOLDER)

    def setup(self, **_):
        for d in (self.raw, self.done, self.finished):
            os.makedirs(d, exist_ok=True)
        return {"raw": self.raw, "finished": self.finished}

    def list_new(self):
        if not os.path.isdir(self.raw):
            return []
        return sorted(f for f in os.listdir(self.raw)
                      if f.lower().endswith(VIDEO_EXT) and not f.startswith(".") and
                      os.path.isfile(os.path.join(self.raw, f)))

    def stable(self, name, wait=5):
        """a file still being copied keeps growing: only take it once its size stops changing"""
        p = os.path.join(self.raw, name)
        try:
            a = os.path.getsize(p)
            time.sleep(wait)
            return a > 0 and a == os.path.getsize(p)
        except OSError:
            return False

    def thumb_for(self, name):
        stem = os.path.splitext(name)[0]
        for f in os.listdir(self.raw):
            if os.path.splitext(f)[0] == stem and f.lower().endswith(IMAGE_EXT):
                return f
        return None

    def fetch(self, name, dest_dir):
        src = os.path.join(self.raw, name)
        dst = os.path.join(dest_dir, name)
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(src, dst)
        return dst

    def deliver(self, paths, subfolder=""):
        dest = os.path.join(self.finished, subfolder) if subfolder else self.finished
        os.makedirs(dest, exist_ok=True)
        out = []
        for p in paths:
            if p and os.path.exists(p):
                shutil.copy2(p, os.path.join(dest, os.path.basename(p)))
                out.append(os.path.join(dest, os.path.basename(p)))
        return out

    def archive(self, name):
        os.makedirs(self.done, exist_ok=True)
        for f in [name, self.thumb_for(name)]:
            if f and os.path.exists(os.path.join(self.raw, f)):
                shutil.move(os.path.join(self.raw, f), os.path.join(self.done, f))


def rclone_bin():
    return os.environ.get("RCLONE", "rclone")


def _rc(*args, capture=True, check=True, timeout=None):
    return subprocess.run([rclone_bin(), *args], capture_output=capture, text=True, check=check, timeout=timeout)


class DriveStorage:
    """Google Drive through rclone. The remote name is GDRIVE_REMOTE (default "gdrive")."""
    name = "gdrive"

    def __init__(self, remote=None):
        self.remote = (remote or C.GDRIVE_REMOTE).rstrip(":")
        self.raw = C.RAW_FOLDER
        self.finished = C.FINISHED_FOLDER

    def path(self, *parts):
        return f"{self.remote}:" + "/".join(p.strip("/") for p in parts if p)

    def configured(self):
        try:
            return self.remote in [r.rstrip(":") for r in _rc("listremotes").stdout.split()]
        except Exception:
            return False

    def setup(self, token=None, **_):
        """create the remote (opens Google's consent page once; or use a token from `rclone authorize "drive"`),
        then the two folders"""
        if not shutil.which(rclone_bin()):
            raise RuntimeError("rclone is not installed (see SKILL.md: brew install rclone / winget install Rclone.Rclone"
                               " / curl https://rclone.org/install.sh | sudo bash)")
        if not self.configured():
            args = ["config", "create", self.remote, "drive", "scope=drive"]
            if token:
                args += ["token", token, "config_is_local=false"]
            _rc(*args, capture=False)   # interactive: rclone prints/opens the Google consent URL
        for f in (self.raw, f"{self.raw}/Done", self.finished):
            _rc("mkdir", self.path(f))
        return {"raw": self.path(self.raw), "finished": self.path(self.finished)}

    def list_new(self):
        out = _rc("lsjson", "--files-only", "--max-depth", "1", self.path(self.raw)).stdout
        return sorted(f["Name"] for f in json.loads(out or "[]") if f["Name"].lower().endswith(VIDEO_EXT))

    def stable(self, name, wait=10):
        def size():
            out = _rc("lsjson", "--files-only", "--max-depth", "1", self.path(self.raw)).stdout
            return next((f["Size"] for f in json.loads(out or "[]") if f["Name"] == name), -1)
        a = size()
        time.sleep(wait)
        return a > 0 and a == size()

    def thumb_for(self, name):
        stem = os.path.splitext(name)[0]
        out = _rc("lsjson", "--files-only", "--max-depth", "1", self.path(self.raw)).stdout
        for f in json.loads(out or "[]"):
            if os.path.splitext(f["Name"])[0] == stem and f["Name"].lower().endswith(IMAGE_EXT):
                return f["Name"]
        return None

    def fetch(self, name, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        dst = os.path.join(dest_dir, name)
        _rc("copyto", self.path(self.raw, name), dst)
        return dst

    def deliver(self, paths, subfolder=""):
        out = []
        for p in paths:
            if p and os.path.exists(p):
                _rc("copyto", p, self.path(self.finished, subfolder, os.path.basename(p)))
                out.append(self.path(self.finished, subfolder, os.path.basename(p)))
        return out

    def archive(self, name):
        for f in [name, self.thumb_for(name)]:
            if f:
                _rc("moveto", self.path(self.raw, f), self.path(self.raw, "Done", f), check=False)


def get_storage(name=None):
    name = (name or C.STORAGE or "local").lower()
    if name in ("gdrive", "drive", "google", "googledrive"):
        return DriveStorage()
    return LocalStorage()
