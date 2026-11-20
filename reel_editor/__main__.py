"""Command line: python -m reel_editor <command> ...

  doctor [--brain]                  check everything is installed/configured (--brain: one tiny Claude call)
  fetch-assets                      download the face-tracking + denoise models (free, ~4 MB)
  config set KEY VALUE|-            save a setting in .env ("-" reads the value from stdin; values never printed)
  config show                       list which settings are set (values hidden)
  test-keys                         real test search on Pexels / Pixabay with the keys in .env
  vault set|delete|status [NAME]    encrypted store for the Envato login (AES-256, key in the OS keychain)
  browser login|start|stop|status|keepalive|install-service [envato]
                                    the editor's own Chrome Canary (permanent login) for sites with no API
  storage-setup [local|gdrive] [--token JSON]   create "Raw Videos" + "Finished Videos"
  sample [out.mp4]                  make a ~20 s test recording
  edit <video> [options]            edit one video (see: edit --help)
  learn-style <folder> --name N [--activate]   learn a style from finished reels -> styles/N/STYLE.md
  watch [--once]                    edit everything dropped into "Raw Videos"
  bot                               run the optional Telegram bot
  agent-docs [--print]              write CLAUDE.md / AGENTS.md tuned to this setup (repo + telegram-agent/)
  motion-check <video>              camera smoothness QA
  clean <work dir>                  delete a work dir (holds transcript-derived files)
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request

from . import config as C

SECRET_KEYS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "EMAIL")
ASSETS = {
    C.FACE_MODEL: "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/"
                  "face_landmarker.task",
    C.RNNOISE_MODEL: "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/somnolent-hogwash-2018-09-01/"
                     "sh.rnnn",
}


def fetch_assets():
    os.makedirs(C.MODELS, exist_ok=True)
    ok = True
    for path, url in ASSETS.items():
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            print(f"ok      {os.path.basename(path)} (already there)")
            continue
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ai-reel-editor/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(path + ".part", "wb") as fh:
                fh.write(r.read())
            os.replace(path + ".part", path)
            print(f"ok      {os.path.basename(path)} downloaded")
        except Exception as ex:
            ok = False
            print(f"FAILED  {os.path.basename(path)}: {ex}")
    return ok


def _mod(name):
    try:
        m = __import__(name)
        return getattr(m, "__version__", "ok")
    except Exception:
        return None


def doctor(brain_test=False):
    rows = []

    def row(ok, what, detail=""):
        rows.append({"ok": ok, "what": what, "detail": detail})
        print(f"{'ok  ' if ok is True else ('--  ' if ok is None else 'FAIL')}  {what}" + (f"  ({detail})" if detail else ""))
    v = sys.version_info
    row(v >= (3, 10), "python >= 3.10", platform.python_version())
    for exe in ("ffmpeg", "ffprobe"):
        row(C.have(exe), exe, "" if C.have(exe) else "install ffmpeg")
    row(C.ffmpeg_major() >= 5, "ffmpeg version", str(C.ffmpeg_major()))
    for mod in ("numpy", "PIL", "cv2", "faster_whisper", "anthropic"):
        ver = _mod(mod)
        row(ver is not None, f"python package {mod}", str(ver or "pip install -r requirements.txt"))
    mp = _mod("mediapipe")
    row(True if mp else None, "mediapipe (optional face tracking / lip sync)", str(mp or "not installed: centre framing"))
    for p in ASSETS:
        row(True if os.path.exists(p) else None, f"model {os.path.basename(p)}",
            "" if os.path.exists(p) else "python -m reel_editor fetch-assets")
    from . import brain
    cli = brain.cli_available()
    row(True if cli else None, "Claude Code CLI", " ".join(C.claude_cli()[:1]) if cli else "not found")
    row(True if os.environ.get("ANTHROPIC_API_KEY") else None, "ANTHROPIC_API_KEY", "set" if os.environ.get(
        "ANTHROPIC_API_KEY") else "not set")
    try:
        m = brain.mode()
        row(True, "brain", f"{m} ({C.MODEL_FAST} / {C.MODEL_SMART})")
    except Exception as ex:
        row(False, "brain", str(ex))
    if brain_test:
        try:
            ans = brain.ask("Reply with exactly the word READY.", model="sonnet")
            row("READY" in ans.upper(), "brain live test", ans.strip()[:40])
        except Exception as ex:
            row(False, "brain live test", str(ex)[:200])
    src = C.BROLL_SOURCE
    if src == "stock":
        k = [n for n in ("PEXELS_API_KEY", "PIXABAY_API_KEY") if os.environ.get(n)]
        row(True if k else None, "B-roll: Pexels + Pixabay", ", ".join(k) + " set" if k else "no keys yet (python -m reel_editor "
                                                                                 "test-keys after adding them)")
    elif src == "envato":
        from . import browser
        exe = browser.find_browser()
        row(C.have("node") and os.path.isdir(os.path.join(C.ROOT, "tools", "node_modules")), "B-roll: Envato adapter",
            "ok" if C.have("node") else "needs node + npm install in tools/ (./install.sh --with-envato)")
        row(bool(exe), "editor browser", ("Chrome Canary" if browser.is_canary(exe) else exe) if exe else
            "install Chrome Canary: https://www.google.com/chrome/canary/")
        if exe:
            li = browser.logged_in("envato")
            row(True if li else None, "Envato login (editor browser)",
                "logged in" if li else "python -m reel_editor browser login envato")
        from . import vault
        vault.migrate_from_env()
        row(True if vault.has_login() else None, "Envato plan-B login (encrypted vault)",
            f"saved, {vault.mode()}" if vault.has_login() else "not saved (only needed if the Canary login cannot work)")
    else:
        row(None, "B-roll", "none (stickers, text cards and punch-ins only)")
    st = C.STORAGE
    if st == "gdrive":
        from .storage import DriveStorage
        row(C.have("rclone"), "rclone", "" if C.have("rclone") else "install rclone")
        row(DriveStorage().configured(), f"Google Drive remote '{C.GDRIVE_REMOTE}'", "python -m reel_editor storage-setup gdrive")
    else:
        row(True, "storage: local folders", C.LOCAL_ROOT)
    row(os.path.exists(C.STYLE_FILE), "style", os.path.relpath(C.STYLE_FILE, C.ROOT))
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        row(bool(os.environ.get("TELEGRAM_ALLOWED_USER_IDS")), "Telegram bot", "allowed user ids set" if os.environ.get(
            "TELEGRAM_ALLOWED_USER_IDS") else "set TELEGRAM_ALLOWED_USER_IDS")
    bad = [r for r in rows if r["ok"] is False]
    print("\nREADY" if not bad else f"\n{len(bad)} problem(s) to fix")
    return 0 if not bad else 1


def config_cmd(args):
    if args[:1] == ["set"] and len(args) >= 3:
        key, val = args[1], args[2]
        if val == "-":
            val = sys.stdin.readline().strip()
        from . import vault
        if key in vault.ALLOWED:          # logins never go in .env
            vault.put(key, val)
            print(f"saved {key} in the encrypted vault ({vault.mode()})")
            return 0
        C.set_env_value(key, val)
        print(f"saved {key} in .env")
        return 0
    if args[:1] == ["show"]:
        keys = []
        if os.path.exists(C.ENV_FILE):
            with open(C.ENV_FILE) as fh:
                keys = [l.split("=", 1)[0].strip() for l in fh if "=" in l and not l.strip().startswith("#")]
        for k in keys:
            v = os.environ.get(k, "")
            hidden = any(s in k for s in SECRET_KEYS)
            print(f"{k}={'(set)' if hidden and v else ('(empty)' if not v else v)}")
        return 0
    print("usage: config set KEY VALUE|-   |   config show")
    return 2


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "doctor":
        return doctor("--brain" in rest)
    if cmd == "fetch-assets":
        return 0 if fetch_assets() else 1
    if cmd == "config":
        return config_cmd(rest)
    if cmd == "test-keys":
        from .broll.stock import test_keys
        res = test_keys()
        if all(v is None for v in res.values()):
            print("no PEXELS_API_KEY / PIXABAY_API_KEY in .env")
            return 1
        return 0 if all(v is not False for v in res.values()) else 1
    if cmd == "vault":
        from .vault import main as vm
        return vm(rest)
    if cmd == "browser":
        from .browser import main as br
        return br(rest)
    if cmd == "storage-setup":
        from .storage import get_storage
        name = rest[0] if rest and not rest[0].startswith("--") else C.STORAGE
        token = rest[rest.index("--token") + 1] if "--token" in rest else None
        res = get_storage(name).setup(token=token)
        C.set_env_value("STORAGE", "gdrive" if name in ("gdrive", "drive") else "local")
        print(json.dumps(res, indent=1))
        return 0
    if cmd == "sample":
        from .sample import make
        make(rest[0] if rest else os.path.join(C.WORKSPACE, "sample-raw.mp4"))
        return 0
    if cmd == "edit":
        from .studio import main as edit_main
        edit_main(rest)
        return 0
    if cmd == "learn-style":
        from .learn_style import main as ls_main
        ls_main(rest)
        return 0
    if cmd == "watch":
        from .jobs import watch
        watch(once="--once" in rest)
        return 0
    if cmd == "bot":
        from .bot import main as bot_main
        bot_main()
        return 0
    if cmd == "agent-docs":
        from .agent_docs import main as ad
        return ad(rest)
    if cmd == "motion-check":
        from .motion_check import main as mc
        return mc(rest)
    if cmd == "clean":
        from .studio import clean
        print("deleted" if rest and clean(rest[0]) else "not a work dir")
        return 0
    print(f"unknown command {cmd}\n{__doc__}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
