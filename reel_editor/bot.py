"""Optional Telegram bot: send a raw video from your phone, get the finished reel back, tap Approve or Change.

Setup (the install playbook does this):
  1. In Telegram, talk to @BotFather -> /newbot -> copy the token -> TELEGRAM_BOT_TOKEN in .env
  2. Send /start to your new bot: it replies with your numeric user id -> TELEGRAM_ALLOWED_USER_IDS in .env
     (comma-separated; nobody else can use the bot)
  3. python -m reel_editor bot

Flow: send the video AS A FILE (paperclip -> File) so Telegram does not compress it, optionally a thumbnail image
first. The bot edits it and sends it back with [Approve] [Change something]. Approve -> the files go to your
"Finished Videos" folder. Change -> your next message is the list of changes and the video is re-edited.

Size limit: Telegram's cloud Bot API lets bots download files up to 20 MB and send up to 50 MB. For bigger raw
videos either drop them in the "Raw Videos" folder instead (the watcher picks them up), or run Telegram's own
local Bot API server (https://github.com/tdlib/telegram-bot-api, up to 2 GB) and set TELEGRAM_API_BASE to it.
"""
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import config as C
from . import studio
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


class Bot:
    def __init__(self):
        self.q = queue.Queue()
        self.jobs = {}          # job id -> {chat, src, thumb, result, notes}
        self.pending_thumb = {}
        self.wait_notes = {}    # chat -> job id
        self.store = get_storage()

    def run(self):
        me = api("getMe")
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
        text = m.get("text") or ""
        if text.startswith("/start"):
            say(chat, "Send me a raw video AS A FILE (paperclip -> File) and I'll edit it. Send a thumbnail image "
                      "first if you want one on the first frames.")
            return
        if text.startswith("/status"):
            say(chat, f"{self.q.qsize()} video(s) waiting, {len(self.jobs)} job(s) this session.")
            return
        if chat in self.wait_notes and text:
            jid = self.wait_notes.pop(chat)
            self.jobs[jid]["notes"] = (self.jobs[jid].get("notes", "") + "\n" + text).strip()
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
            self.q.put(jid)
            say(chat, f"Queued ({self.q.qsize()} ahead incl. this one). I'll send it back when it's done.")

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
            j = self.jobs[jid]
            try:
                stem = os.path.splitext(os.path.basename(j["src"]))[0]
                res = studio.edit(j["src"], out=os.path.join(j["dir"], f"{stem} - edited.mp4"), thumb=j.get("thumb"),
                                  work=os.path.join(j["dir"], "work"), notes=j.get("notes", ""),
                                  redo=["takes"] if j.get("notes") else ())
                j["result"] = res
                self.send_result(jid, res)
            except Exception as ex:
                say(j["chat"], f"The edit failed: {str(ex)[:300]}")

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
        cap += f"\nClaude cost: ${res['usage'].get('cost_usd', 0):.2f}"
        send = out
        if os.path.getsize(out) > OUT_LIMIT:
            send = preview(out, os.path.join(j["dir"], "preview.mp4"))
            cap += "\n(preview copy; Approve saves the full-quality file)"
        kb = [[{"text": "Approve", "callback_data": f"ok:{jid}"},
               {"text": "Change something", "callback_data": f"change:{jid}"}]]
        api("sendDocument", {"chat_id": j["chat"], "caption": cap, "reply_markup": json.dumps({"inline_keyboard": kb})},
            files={"document": send}, timeout=900)


def main():
    Bot().run()


if __name__ == "__main__":
    main()
