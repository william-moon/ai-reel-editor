"""The editor's own browser: Google Chrome Canary with a permanent profile, for sites that have no API (Envato).

Why Canary: it is a separate app from the person's everyday Chrome, so the editor can keep it running in the
background (remote debugging on BROWSER_CDP) without taking over their normal browser. Linux has no Canary build,
so there it falls back to google-chrome / chromium with the same separate profile.

Why it stays logged in: the profile lives OUTSIDE the repo (BROWSER_PROFILE, default ~/.ai-reel-editor/browser-profile),
so cookies survive restarts, updates and re-installs. `browser install-service` keeps the browser running in the
background (launchd on macOS, systemd --user on Linux, a logon task on Windows) and runs a daily keep-alive that
visits the site so the session is refreshed before it can expire. If a site still logs out and the person saved
their login in the encrypted vault (reel_editor/vault.py), the keep-alive signs back in by itself.

  python -m reel_editor browser login envato       open a visible window on the sign-in page; log in once
  python -m reel_editor browser start [--visible]  start it in the background (headless) if not running
  python -m reel_editor browser stop
  python -m reel_editor browser status             running? logged in to Envato?
  python -m reel_editor browser keepalive          visit Envato, sign back in if needed (the daily job runs this)
  python -m reel_editor browser install-service    start at login + daily keep-alive (permanent)
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

from . import config as C

SITES = {
    "envato": {"login": "https://account.envato.com/sign_in?to=elements", "home": "https://app.envato.com/"},
}
LABEL = "com.ai-reel-editor.browser"


def cdp_url():
    return C.BROWSER_CDP


def port():
    return urllib.parse.urlparse(cdp_url()).port or 9222


def profile_dir():
    return os.path.expanduser(C.BROWSER_PROFILE)


def find_browser():
    """Chrome Canary first (all OSes that have it), then a normal Chrome/Chromium binary. BROWSER_BIN overrides."""
    if C.BROWSER_BIN:
        return C.BROWSER_BIN if os.path.exists(C.BROWSER_BIN) or shutil.which(C.BROWSER_BIN) else None
    sysname = platform.system()
    cands = []
    if sysname == "Darwin":
        cands = ["/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
                 os.path.expanduser("~/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    elif sysname == "Windows":
        la = os.environ.get("LOCALAPPDATA", "")
        pf = [os.environ.get("PROGRAMFILES", r"C:\Program Files"), os.environ.get("PROGRAMFILES(X86)", "")]
        cands = [os.path.join(la, r"Google\Chrome SxS\Application\chrome.exe")] + \
                [os.path.join(p, r"Google\Chrome\Application\chrome.exe") for p in pf if p] + \
                [os.path.join(la, r"Google\Chrome\Application\chrome.exe")]
    else:
        cands = [shutil.which(n) for n in ("google-chrome-canary", "google-chrome", "google-chrome-stable",
                                           "chromium", "chromium-browser")]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def is_canary(path):
    return bool(path) and ("Canary" in path or "Chrome SxS" in path)


def running():
    try:
        with urllib.request.urlopen(cdp_url().rstrip("/") + "/json/version", timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _args(visible, url=None):
    a = [f"--remote-debugging-port={port()}", "--remote-debugging-address=127.0.0.1",
         f"--user-data-dir={profile_dir()}", "--no-first-run", "--no-default-browser-check",
         "--disable-features=Translate", "--restore-last-session"]
    if not visible:
        a += ["--headless=new", "--window-size=1440,1000"]
    if url:
        a.append(url)
    return a


def start(visible=False, url=None, wait=20):
    if running():
        return True
    exe = find_browser()
    if not exe:
        raise RuntimeError("No Chrome Canary / Chrome found. Install Chrome Canary: https://www.google.com/chrome/canary/")
    os.makedirs(profile_dir(), exist_ok=True)
    kw = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "stdin": subprocess.DEVNULL}
    if platform.system() == "Windows":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    subprocess.Popen([exe, *_args(visible, url)], **kw)
    for _ in range(wait * 2):
        if running():
            return True
        time.sleep(0.5)
    return False


def stop():
    """Ask the browser to close through its own debugging connection (never kills by name)."""
    if not running():
        return True
    try:
        _node("close", timeout=30)
    except Exception:
        pass
    for _ in range(20):
        if not running():
            return True
        time.sleep(0.5)
    return False


NODE_SCRIPT = os.path.join(C.ROOT, "tools", "browser_session.mjs")


def _node(*args, timeout=120, stdin=None):
    env = {k: v for k, v in os.environ.items() if k not in ("ENVATO_EMAIL", "ENVATO_PASSWORD")}
    r = subprocess.run(["node", NODE_SCRIPT, *args], capture_output=True, text=True, timeout=timeout,
                       input=stdin or "", env={**env, "BROWSER_CDP": cdp_url()})
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout)[-400:].strip())
    return r.stdout.strip()


def logged_in(site="envato"):
    """True / False, or None when it could not be checked (browser down, node missing)."""
    if not running():
        return None
    try:
        return json.loads(_node("check", site)).get("logged_in")
    except Exception:
        return None


def login(site="envato", wait_min=10, log=print):
    """Open a VISIBLE window on the sign-in page and wait until the person has logged in. Then switch the browser
    back to the background. The login is kept in the permanent profile."""
    if site not in SITES:
        raise ValueError(f"unknown site {site}")
    if running():
        stop()
    if not start(visible=True, url=SITES[site]["login"]):
        raise RuntimeError("browser did not start")
    log(f"A browser window is open on the {site} sign-in page. Log in there once (tick 'Remember me' if offered).")
    t0 = time.time()
    while time.time() - t0 < wait_min * 60:
        time.sleep(5)
        if logged_in(site):
            log(f"logged in to {site}; the login is saved in the editor's browser profile")
            stop()
            start(visible=False)
            return True
    log("not logged in yet (timed out); run the command again when ready")
    return False


def keepalive(site="envato", log=print):
    """Daily job: make sure the browser runs, refresh the session, sign back in with saved credentials if needed."""
    if not running() and not start(visible=False):
        log("browser could not start")
        return False
    state = logged_in(site)
    if state:
        log(f"{site}: logged in")
        return True
    if state is None:
        log(f"{site}: could not check (is node + `npm install` in tools/ done?)")
        return False
    from . import vault
    if vault.has_login(site):
        creds = json.dumps({"email": vault.get("ENVATO_EMAIL"), "password": vault.get("ENVATO_PASSWORD")})
        try:
            out = json.loads(_node("login", site, timeout=150, stdin=creds))
        except Exception as ex:
            log(f"{site}: automatic sign-in failed: {str(ex)[:160]}")
            return False
        if out.get("logged_in"):
            log(f"{site}: signed back in with the saved login")
            return True
        log(f"{site}: automatic sign-in did not finish ({out.get('reason', 'unknown')}); run: browser login {site}")
        return False
    log(f"{site}: logged out. Run: python -m reel_editor browser login {site}  (plan B if that cannot work: save the "
        "Envato login in the encrypted vault: /envato in the Telegram bot, or `vault set ENVATO_EMAIL` + "
        "`vault set ENVATO_PASSWORD`; Google/Apple sign-up accounts add a password at "
        "https://account.envato.com/reset_password first)")
    return False


# ---------- permanent background service ----------

def _py():
    return sys.executable


def install_service(log=print):
    sysname = platform.system()
    cwd = C.ROOT
    if sysname == "Darwin":
        la = os.path.expanduser("~/Library/LaunchAgents")
        os.makedirs(la, exist_ok=True)
        logs = os.path.join(C.WORKSPACE, "logs")
        os.makedirs(logs, exist_ok=True)
        for name, args, extra in (
                ("", ["browser", "start"], "<key>RunAtLoad</key><true/>\n  <key>StartInterval</key><integer>900</integer>"),
                (".keepalive", ["browser", "keepalive"],
                 "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>4</integer>"
                 "<key>Minute</key><integer>20</integer></dict>")):
            label = LABEL + name
            argx = "".join(f"<string>{a}</string>" for a in [_py(), "-m", "reel_editor", *args])
            plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key><array>{argx}</array>
  <key>WorkingDirectory</key><string>{cwd}</string>
  {extra}
  <key>StandardOutPath</key><string>{os.path.join(logs, 'browser.log')}</string>
  <key>StandardErrorPath</key><string>{os.path.join(logs, 'browser.log')}</string>
</dict></plist>
"""
            path = os.path.join(la, label + ".plist")
            with open(path, "w") as fh:
                fh.write(plist)
            uid = str(os.getuid())
            subprocess.run(["launchctl", "bootout", f"gui/{uid}", path], capture_output=True)
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", path], capture_output=True)
            log(f"installed {path}")
        return True
    if sysname == "Linux":
        d = os.path.expanduser("~/.config/systemd/user")
        os.makedirs(d, exist_ok=True)
        units = {
            "ai-reel-editor-browser.service": f"""[Unit]
Description=ai-reel-editor browser (permanent profile)
[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={cwd}
ExecStart={_py()} -m reel_editor browser start
[Install]
WantedBy=default.target
""",
            "ai-reel-editor-keepalive.service": f"""[Unit]
Description=ai-reel-editor browser keep-alive
[Service]
Type=oneshot
WorkingDirectory={cwd}
ExecStart={_py()} -m reel_editor browser keepalive
""",
            "ai-reel-editor-keepalive.timer": """[Unit]
Description=daily ai-reel-editor browser keep-alive
[Timer]
OnCalendar=*-*-* 04:20:00
Persistent=true
[Install]
WantedBy=timers.target
""",
        }
        for n, body in units.items():
            with open(os.path.join(d, n), "w") as fh:
                fh.write(body)
        for cmd in (["daemon-reload"], ["enable", "--now", "ai-reel-editor-browser.service"],
                    ["enable", "--now", "ai-reel-editor-keepalive.timer"]):
            subprocess.run(["systemctl", "--user", *cmd], capture_output=True)
        log(f"installed systemd user units in {d} (on a server also run once: loginctl enable-linger $USER)")
        return True
    if sysname == "Windows":
        base = f'"{_py()}" -m reel_editor browser'
        for name, cmd, sched in (("ai-reel-editor browser", base + " start", ["/SC", "ONLOGON"]),
                                 ("ai-reel-editor keepalive", base + " keepalive", ["/SC", "DAILY", "/ST", "04:20"])):
            subprocess.run(["schtasks", "/Create", "/F", "/TN", name, "/TR", cmd, *sched], capture_output=True)
        log("installed two scheduled tasks (start at logon, daily keep-alive)")
        return True
    log(f"unsupported OS {sysname}")
    return False


def status():
    exe = find_browser()
    info = running()
    out = {
        "browser": exe or "not found",
        "canary": is_canary(exe),
        "profile": profile_dir(),
        "running": bool(info),
        "envato_logged_in": logged_in("envato") if info else None,
        "saved_login": __import__("reel_editor.vault", fromlist=["x"]).has_login("envato"),
    }
    return out


def main(args):
    sub = args[0] if args else "status"
    site = args[1] if len(args) > 1 and not args[1].startswith("--") else "envato"
    if sub == "start":
        ok = start(visible="--visible" in args)
        print("running" if ok else "could not start")
        return 0 if ok else 1
    if sub == "stop":
        return 0 if stop() else 1
    if sub == "login":
        return 0 if login(site) else 1
    if sub == "keepalive":
        return 0 if keepalive(site) else 1
    if sub == "install-service":
        return 0 if install_service() else 1
    if sub == "status":
        print(json.dumps(status(), indent=1))
        return 0
    print(__doc__)
    return 2
