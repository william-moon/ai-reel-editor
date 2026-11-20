"""OPTIONAL Envato adapter.

Uses YOUR OWN logged-in browser session (Chrome with remote debugging, ENVATO_CDP, default http://127.0.0.1:9222)
and your own paid Envato subscription, through tools/envato_video.mjs (needs Node.js + `npm install` in tools/).
Check Envato's licence terms yourself: downloads are licensed per project.
"""
import json
import os
import subprocess
import urllib.request

from .. import config as C

SCRIPT = os.path.join(C.ROOT, "tools", "envato_video.mjs")


def _run(*args, timeout=120):
    r = subprocess.run(["node", SCRIPT, *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "ENVATO_CDP": C.ENVATO_CDP})
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    return r.stdout


class EnvatoProvider:
    name = "envato"

    def search(self, query, orientation="vertical", limit=12, log=print):
        try:
            res = json.loads(_run("search", query, "--orientation", orientation, "--limit", str(limit), "--json"))
        except Exception as ex:
            log("envato search failed:", str(ex)[:120])
            return []
        for r in res:
            r["source"] = "envato"
        return res

    def fetch_thumb(self, cand, path):
        if not cand.get("thumbUrl"):
            return None
        req = urllib.request.Request(cand["thumbUrl"], headers={"User-Agent": "ai-reel-editor/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as fh:
            fh.write(r.read())
        return path

    def download(self, cand, dest_dir):
        os.makedirs(dest_dir, exist_ok=True)
        cached = [f for f in os.listdir(dest_dir) if cand["id"] in f and not f.endswith(".json")]
        if cached:
            return os.path.join(dest_dir, cached[0])
        last = None
        for _ in range(3):
            try:
                return _run("download", cand["id"], "--out", dest_dir, timeout=180).strip().splitlines()[-1]
            except Exception as ex:
                last = ex
        raise RuntimeError(f"envato download failed: {last}")
