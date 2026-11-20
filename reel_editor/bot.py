"""Optional Telegram bot: send a raw video from your phone, get the finished reel back, tap Approve or Change.

Setup (the install playbook does this):
  1. In Telegram, talk to @BotFather -> /newbot -> copy the token -> TELEGRAM_BOT_TOKEN in .env
  2. Send /start to your new bot: it replies with your numeric user id -> TELEGRAM_ALLOWED_USER_IDS in .env
     (comma-separated; nobody else can use the bot)
  3. python -m reel_editor bot

Flow: send the video AS A FILE (paperclip -> File) so Telegram does not compress it, optionally a thumbnail image
first. The bot edits it and sends it back with [Approve] [Change something]. Approve -> the files go to your
"Finished Videos" folder. Change -> your next message is the list of changes and the video is re-edited.

Commands (also shown in Telegram's "/" menu):
  /help     what the bot does + the commands
  /status   what it is editing right now, for how long, and what is queued
  /cancel   stop the edit running now AND clear the queue
  /model    show the Claude models the editor uses (/model <id> switches the main one, /model reset undoes it)
  /style    show the active edit style (/style <name> switches to styles/<name>/STYLE.md)
  /privacy  where your videos, keys and transcripts live
Each edit has a hard time cap (EDIT_TIMEOUT_MIN in .env, default 45 minutes) so a stuck edit cannot run forever.

Size limit: Telegram's cloud Bot API lets bots download files up to 20 MB and send up to 50 MB. For bigger raw
videos either drop them in the "Raw Videos" folder instead (the watcher picks them up), or run Telegram's own
local Bot API server (https://github.com/tdlib/telegram-bot-api, up to 2 GB) and set TELEGRAM_API_BASE to it.
"""
import json
import os
import queue
import signal
import sys
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import config as C
from .storage import get_storage

API = C.env("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")
LOCAL_API = not API.startswith("https://api.telegram.org")
IN_LIMIT = (2000 if LOCAL_API else 20) * 1024 * 1024
OUT_LIMIT = (2000 if LOCAL_API else 50) * 1024 * 1024


def token():
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not t:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    return t


def allowed_ids():
    return {int(x) for x in re.findall(r"-?\d+", os.environ.get("TELEGRAM_ALLOWED_USER_IDS", ""))}


def api(method, fields=None, files=None, timeout=120):
    url = f"{API}/bot{token()}/{method}"
    if files:
        b = uuid.uuid4().hex
        body = b""
        for k, v in (fields or {}).items():
            body += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, path in files.items():
            with open(path, "rb") as fh:
                body += (f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{os.path.basename(path)}\""
                         f"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() + fh.read() + b"\r\n"
        body += f"--{b}--\r\n".encode()
        req = urllib.request.Request(url, body, {"Content-Type": f"multipart/form-data; boundary={b}"})
    else:
        req = urllib.request.Request(url, json.dumps(fields or {}).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r).get("result")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"telegram {method}: HTTP {e.code} {e.read()[:200]!r}")


def say(chat, text, kb=None):
    f = {"chat_id": chat, "text": text}
    if kb:
        f["reply_markup"] = {"inline_keyboard": kb}
    return api("sendMessage", f)


def download(file_id, dest):
    info = api("getFile", {"file_id": file_id})
    fp = info["file_path"]
    if LOCAL_API and os.path.isabs(fp) and os.path.exists(fp):   # local server in --local mode: already on disk
        os.replace(fp, dest)
        return dest
    url = f"{API}/file/bot{token()}/{fp}"
    with urllib.request.urlopen(url, timeout=600) as r, open(dest, "wb") as fh:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            fh.write(b)
    return dest


def preview(final, out, max_mb=45):
    dur = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                                         "csv=p=0", final]).decode())
    kbps = max(800, min(3500, int(max_mb * 8192 / max(dur, 1)) - 128))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", final, "-vf", "scale=720:-2", "-c:v", "libx264", "-preset",
                    "veryfast", "-b:v", f"{kbps}k", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out],
                   check=True)
    return out


EDIT_TIMEOUT = int(C.env("EDIT_TIMEOUT_MIN", "45") or 45) * 60
MODEL_ID = re.compile(r"^claude-[a-z0-9][a-z0-9.-]{2,60}$")
COMMANDS = [
    ("help", "What I do and my commands"),
    ("status", "What I'm editing now, how long, what's queued"),
    ("cancel", "Stop the edit running now and clear the queue"),
    ("model", "Show or switch the Claude model the editor uses"),
    ("style", "Show or switch the edit style"),
    ("privacy", "Where your videos and keys live"),
]
HELP = ("I'm your reel editor. Send me a raw video AS A FILE (paperclip -> File) and I send back the finished "
        "reel with [Approve] [Change something]. Send a thumbnail image first if you want one.\n\n"
        "Commands:\n"
        "/status - what I'm editing now, how long, and what's queued\n"
        "/cancel - stop the edit running now and clear the queue\n"
        "/model - show the Claude models I use (/model <id> to switch, /model reset to undo)\n"
        "/style - show the active style (/style <name> to switch)\n"
        "/privacy - where your videos and keys live")


def fmt(sec):
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def styles():
    root = os.path.join(C.ROOT, "styles")
    return sorted(d for d in os.listdir(root) if os.path.exists(os.path.join(root, d, "STYLE.md")))


def active_style():
    return os.path.basename(os.path.dirname(os.environ.get("STYLE_FILE") or C.STYLE_FILE))


def set_setting(key, value):
    C.set_env_value(key, value)
    if value:
        os.environ[key] = value
    else:
        os.environ.pop(key, None)


class Bot:
    def __init__(self):
        self.q = queue.Queue()
        self.jobs = {}          # job id -> {chat, src, thumb, result, notes}
        self.pending_thumb = {}
        self.wait_notes = {}    # chat -> job id
        self.store = get_storage()
        self.current = None     # {jid, proc, started}
        self.cancelled = set()

    def run(self):
        me = api("getMe")
        try:
            api("setMyCommands", {"commands": [{"command": c, "description": d} for c, d in COMMANDS]})
        except Exception as ex:
            print("could not set the / menu:", str(ex)[:120])
        print(f"bot @{me.get('username')} running ({'local' if LOCAL_API else 'cloud'} Bot API)")
        threading.Thread(target=self.worker, daemon=True).start()
        offset = 0
        while True:
            try:
                ups = api("getUpdates", {"offset": offset, "timeout": 50}, timeout=70) or []
            except Exception as ex:
                print("poll error:", str(ex)[:120])
                time.sleep(5)
                continue
            for u in ups:
                offset = u["update_id"] + 1
                try:
                    if "callback_query" in u:
                        self.on_button(u["callback_query"])
                    elif "message" in u:
                        self.on_message(u["message"])
                except Exception as ex:
                    print("handler error:", ex)

    def on_message(self, m):
        chat, user = m["chat"]["id"], (m.get("from") or {}).get("id")
        if user not in allowed_ids():
            if (m.get("text") or "").startswith("/start"):
                say(chat, f"Your Telegram user id is {user}. Add it to TELEGRAM_ALLOWED_USER_IDS in the editor's .env "
                          "and restart the bot to use it.")
            return
        text = (m.get("text") or "").strip()
        if text.startswith("/"):
            cmd, _, arg = text.partition(" ")
            cmd = cmd.split("@")[0].lower()
            handler = {"/start": self.cmd_help, "/help": self.cmd_help, "/status": self.cmd_status,
                       "/cancel": self.cmd_cancel, "/model": self.cmd_model, "/style": self.cmd_style,
                       "/privacy": self.cmd_privacy}.get(cmd)
            if handler:
                handler(chat, arg.strip())
                return
        if chat in self.wait_notes and text:
            jid = self.wait_notes.pop(chat)
            self.jobs[jid]["notes"] = (self.jobs[jid].get("notes", "") + "\n" + text).strip()
            self.cancelled.discard(jid)
            self.q.put(jid)
            say(chat, "Got it, re-editing with your changes.")
            return
        doc = m.get("document") or m.get("video")
        photo = m.get("photo")
        if photo or (doc and (doc.get("mime_type") or "").startswith("image/")):
            fid = photo[-1]["file_id"] if photo else doc["file_id"]
            p = os.path.join(C.JOBS, f"thumb-{chat}-{int(time.time())}.jpg")
            os.makedirs(C.JOBS, exist_ok=True)
            self.pending_thumb[chat] = download(fid, p)
            say(chat, "Thumbnail saved for your next video.")
            return
        if doc:
            if (doc.get("file_size") or 0) > IN_LIMIT:
                say(chat, f"That file is {doc['file_size'] / 1e6:.0f} MB; Telegram only lets bots download "
                          f"{IN_LIMIT // (1024 * 1024)} MB. Put it in your '{C.RAW_FOLDER}' folder instead and it will "
                          "be edited automatically.")
                return
            if m.get("video"):
                say(chat, "Note: that video was sent the normal way, so Telegram compressed it. Send it as a File "
                          "for full quality.")
            jid = uuid.uuid4().hex[:8]
            jd = os.path.join(C.JOBS, f"tg-{jid}")
            os.makedirs(jd, exist_ok=True)
            name = re.sub(r"[^\w.-]+", "-", doc.get("file_name") or f"video-{jid}.mp4")
            src = download(doc["file_id"], os.path.join(jd, name))
            self.jobs[jid] = {"chat": chat, "src": src, "thumb": self.pending_thumb.pop(chat, None), "dir": jd}
            self.cancelled.discard(jid)
            self.q.put(jid)
            say(chat, f"Queued ({self.q.qsize()} ahead incl. this one). I'll send it back when it's done.")

    def cmd_help(self, chat, arg):
        say(chat, HELP)

    def cmd_status(self, chat, arg):
        cur = self.current
        lines = []
        if cur:
            name = os.path.basename(self.jobs[cur["jid"]]["src"])
            lines.append(f"Editing {name} for {fmt(time.time() - cur['started'])} "
                         f"(cap {EDIT_TIMEOUT // 60} min).")
        else:
            lines.append("Not editing anything right now.")
        lines.append(f"{self.q.qsize()} video(s) waiting.")
        say(chat, " ".join(lines))

    def cmd_cancel(self, chat, arg):
        dropped = 0
        while True:
            try:
                self.cancelled.add(self.q.get_nowait())
                dropped += 1
            except queue.Empty:
                break
        self.wait_notes.pop(chat, None)
        cur = self.current
        if cur:
            self.cancelled.add(cur["jid"])
            kill(cur["proc"])
        say(chat, ("Stopped the edit I was working on" if cur else "Nothing was running") +
            f" and cleared {dropped} queued video(s).")

    def cmd_model(self, chat, arg):
        if arg.lower() == "reset":
            set_setting("MODEL_SMART", "")
            say(chat, "Back to the default model.")
        elif arg:
            if not MODEL_ID.match(arg):
                say(chat, "That does not look like a Claude model id (example: claude-opus-5-5). Nothing changed.")
                return
            set_setting("MODEL_SMART", arg)
            say(chat, f"Main model is now {arg} from the next edit. If edits start failing, send /model reset.")
        smart = os.environ.get("MODEL_SMART") or C.MODEL_SMART
        fast = os.environ.get("MODEL_FAST") or C.MODEL_FAST
        if not arg:
            say(chat, f"Main (judgment) model: {smart}\nFast (routine) model: {fast}\n"
                      f"Brain: {C.env('CLAUDE_MODE', 'auto')} (auto = Claude Code on your plan, api = API key)")

    def cmd_style(self, chat, arg):
        have = styles()
        if arg:
            if arg not in have:
                say(chat, f"No style called {arg}. Styles: {', '.join(have)}")
                return
            set_setting("STYLE_FILE", os.path.join("styles", arg, "STYLE.md"))
        say(chat, f"Active style: {active_style()}. Styles: {', '.join(have)}. "
                  "To learn a new one, send your past reels to your AI agent and ask it to learn your style.")

    def cmd_privacy(self, chat, arg):
        where = "your Google Drive" if C.STORAGE == "gdrive" else "folders on the computer running me"
        say(chat, "Your videos are edited on the computer or server running me and delivered to " + where + ". "
                  "Keys stay in the editor's .env file and are never sent in chat. Claude sees the transcript and "
                  "a few still frames to make editing choices. Work files with the transcript are deleted after "
                  "you approve. Only the Telegram user ids in TELEGRAM_ALLOWED_USER_IDS can use me.")

    def on_button(self, cq):
        api("answerCallbackQuery", {"callback_query_id": cq["id"]})
        if (cq.get("from") or {}).get("id") not in allowed_ids():
            return
        action, _, jid = (cq.get("data") or "").partition(":")
        j = self.jobs.get(jid)
        if not j:
            return
        if action == "ok":
            res = j["result"]
            done = self.store.deliver([res["out"], res["sheet"], res.get("post"), res.get("credits")])
            say(j["chat"], "Approved. Saved to your Finished Videos folder" + (f": {done[0]}" if done else "."))
        elif action == "change":
            self.wait_notes[j["chat"]] = jid
            say(j["chat"], "What should I change? Reply with the changes in one message.")

    def worker(self):
        while True:
            jid = self.q.get()
            if jid in self.cancelled:
                continue
            j = self.jobs[jid]
            try:
                res = self.run_edit(jid, j)
                if res:
                    j["result"] = res
                    self.send_result(jid, res)
            except Exception as ex:
                say(j["chat"], f"The edit failed: {str(ex)[:300]}")
            finally:
                self.current = None

    def run_edit(self, jid, j):
        stem = os.path.splitext(os.path.basename(j["src"]))[0]
        result_file = os.path.join(j["dir"], "result.json")
        cmd = [sys.executable, "-m", "reel_editor", "edit", j["src"],
               "--out", os.path.join(j["dir"], f"{stem} - edited.mp4"), "--work", os.path.join(j["dir"], "work"),
               "--result-json", result_file]
        if j.get("thumb"):
            cmd += ["--thumb", j["thumb"]]
        if j.get("notes"):
            cmd += ["--notes", j["notes"], "--redo", "takes"]
        log = open(os.path.join(j["dir"], "edit.log"), "w")
        proc = subprocess.Popen(cmd, cwd=C.ROOT, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=(os.name != "nt"))
        started = time.time()
        self.current = {"jid": jid, "proc": proc, "started": started}
        msg = say(j["chat"], "Editing... 0:00")
        mid = (msg or {}).get("message_id")
        last = 0
        while proc.poll() is None:
            time.sleep(2)
            el = time.time() - started
            if el > EDIT_TIMEOUT:
                kill(proc)
                say(j["chat"], f"The edit hit the {EDIT_TIMEOUT // 60}-minute limit and was stopped. "
                               "Try a shorter video, or raise EDIT_TIMEOUT_MIN.")
                return None
            if mid and el - last >= 15:
                last = el
                try:
                    api("editMessageText", {"chat_id": j["chat"], "message_id": mid, "text": f"Editing... {fmt(el)}"})
                except Exception:
                    pass
        log.close()
        if jid in self.cancelled:
            return None
        if mid:
            try:
                api("editMessageText", {"chat_id": j["chat"], "message_id": mid,
                                        "text": f"Done in {fmt(time.time() - started)}"})
            except Exception:
                pass
        if proc.returncode != 0 or not os.path.exists(result_file):
            tail = open(os.path.join(j["dir"], "edit.log"), errors="replace").read()[-300:]
            raise RuntimeError(tail or f"exit code {proc.returncode}")
        return json.load(open(result_file))

    def send_result(self, jid, res):
        j = self.jobs[jid]
        out = res["out"]
        post = {}
        if res.get("post") and os.path.exists(res["post"]):
            post = json.load(open(res["post"]))
        cap = (post.get("title", "") + "\n\n" + post.get("description", "") + "\n" +
               " ".join(post.get("hashtags", [])))[:1000]
        qa = res.get("qa") or []
        cap += "\n\nFinal check: " + ("passed" if not qa else "; ".join(qa))[:200]
        cap += f"\nClaude cost: ${(res.get('usage') or {}).get('cost_usd', 0):.2f}"
        send = out
        if os.path.getsize(out) > OUT_LIMIT:
            send = preview(out, os.path.join(j["dir"], "preview.mp4"))
            cap += "\n(preview copy; Approve saves the full-quality file)"
        kb = [[{"text": "Approve", "callback_data": f"ok:{jid}"},
               {"text": "Change something", "callback_data": f"change:{jid}"}]]
        api("sendDocument", {"chat_id": j["chat"], "caption": cap, "reply_markup": json.dumps({"inline_keyboard": kb})},
            files={"document": send}, timeout=900)


def kill(proc):
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        proc.kill()


def main():
    Bot().run()


if __name__ == "__main__":
    main()
