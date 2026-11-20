"""OPTIONAL Envato adapter.

Envato has no public download API, so this uses the editor's OWN browser (Google Chrome Canary with a permanent
profile, see reel_editor/browser.py) logged in to the person's paid Envato account, through tools/envato_video.mjs
(needs Node.js + `npm install` in tools/). The browser is started automatically; if Envato logged out, the
saved login (if any) is used once to sign back in.
Check Envato's licence terms yourself: downloads are licensed per project.
"""
import json
import os
import subprocess
import urllib.request

from .. import config as C

SCRIPT = os.path.join(C.ROOT, "tools", "envato_video.mjs")


def _run_once(*args, timeout=120):
    r = subprocess.run(["node", SCRIPT, *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "BROWSER_CDP": C.BROWSER_CDP})
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-400:])
    return r.stdout


def _run(*args, timeout=120):
    """Start the editor browser if needed; on a logged-out session try the saved login once, then retry."""
    from .. import browser
    if not browser.running():
        browser.start(visible=False)
    try:
        return _run_once(*args, timeout=timeout)
    except RuntimeError as ex:
        msg = str(ex)
        if "BROWSER_DOWN" in msg and browser.start(visible=False):
            return _run_once(*args, timeout=timeout)
        if "NOT_LOGGED_IN" in msg and browser.keepalive("envato", log=lambda *a: None):
            return _run_once(*args, timeout=timeout)
        raise


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
