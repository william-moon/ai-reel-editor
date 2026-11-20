"""Drop-and-go: watch the "Raw Videos" folder (local or Google Drive), edit each new video, deliver it to
"Finished Videos", move the raw file to "Raw Videos/Done".

  python -m reel_editor watch            # keep watching (Ctrl+C to stop)
  python -m reel_editor watch --once     # process what is there now, then exit (for cron / Task Scheduler)

One edit at a time (a lock file stops a second watcher). Job folders: workspace/jobs/<name>/ (final.mp4, review
sheet, post copy, Claude usage, log). The work dir with transcript-derived files is deleted after each edit.
"""
import json
import os
import re
import time
import traceback

from . import config as C
from . import studio
from .storage import get_storage


def _lock():
    os.makedirs(C.WORKSPACE, exist_ok=True)
    p = os.path.join(C.WORKSPACE, ".watch.lock")
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return p
    except FileExistsError:
        try:   # stale lock from a crashed run?
            pid = int(open(p).read().strip() or 0)
            os.kill(pid, 0)
            return None
        except (ValueError, OSError):
            os.remove(p)
            return _lock()


def job_dir(name):
    d = os.path.join(C.JOBS, re.sub(r"[^\w.-]+", "-", os.path.splitext(name)[0]))
    os.makedirs(d, exist_ok=True)
    return d


def process(store, name, notes="", log=print):
    jd = job_dir(name)
    state = {"name": name, "state": "editing", "started": time.time()}
    with open(os.path.join(jd, "job.json"), "w") as fh:
        json.dump(state, fh, indent=1)
    src = store.fetch(name, jd)
    th_name = store.thumb_for(name)
    thumb = store.fetch(th_name, jd) if th_name else None
    stem = os.path.splitext(name)[0]
    out = os.path.join(jd, f"{stem} - edited.mp4")
    try:
        res = studio.edit(src, out=out, thumb=thumb, work=os.path.join(jd, "work"), notes=notes, keep_work=False)
    except Exception as ex:
        state.update(state="failed", error=str(ex)[:500])
        with open(os.path.join(jd, "job.json"), "w") as fh:
            json.dump(state, fh, indent=1)
        log(f"FAILED {name}: {ex}")
        traceback.print_exc()
        return None
    delivered = store.deliver([res["out"], res["sheet"], res.get("post"), res.get("credits")])
    store.archive(name)
    for p in (src,):      # the raw copy in the job folder is no longer needed
        try:
            os.remove(p)
        except OSError:
            pass
    state.update(state="ready", finished=time.time(), qa=res["qa"], cost_usd=res["usage"].get("cost_usd"),
                 delivered=delivered)
    with open(os.path.join(jd, "job.json"), "w") as fh:
        json.dump(state, fh, indent=1)
    log(f"done {name} -> {delivered[0] if delivered else res['out']}")
    return res


def watch(once=False, interval=60, log=print):
    lock = _lock()
    if not lock:
        log("another watcher is already running")
        return
    store = get_storage()
    store.setup() if store.name == "local" else None
    log(f"watching {store.name} '{C.RAW_FOLDER}' every {interval}s")
    try:
        while True:
            for name in store.list_new():
                if store.stable(name):
                    log(f"new video: {name}")
                    process(store, name, log=log)
            if once:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        os.remove(lock)
