import subprocess
import sys

from reel_editor import bot as B


class FakeStore:
    pass


def make(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(B, "say", lambda chat, text, kb=None: sent.append(text) or {"message_id": 1})
    monkeypatch.setattr(B, "get_storage", lambda: FakeStore())
    monkeypatch.setattr(B.C, "set_env_value", lambda k, v: None)
    return B.Bot(), sent


def test_commands_menu_matches_handlers():
    names = [c for c, _ in B.COMMANDS]
    assert names == ["help", "status", "cancel", "model", "style", "privacy"]
    assert all(len(d) <= 256 and " " not in c for c, d in B.COMMANDS)


def test_cancel_kills_running_edit_and_clears_queue(monkeypatch, tmp_path):
    bot, sent = make(monkeypatch, tmp_path)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    bot.jobs = {"a": {"src": "a.mp4"}, "b": {"src": "b.mp4"}}
    bot.current = {"jid": "a", "proc": proc, "started": 0}
    bot.q.put("b")
    bot.cmd_cancel(1, "")
    assert proc.wait(timeout=5) != 0
    assert bot.q.empty() and {"a", "b"} <= bot.cancelled
    assert "Stopped" in sent[-1] and "1 queued" in sent[-1]


def test_model_rejects_bad_id(monkeypatch, tmp_path):
    bot, sent = make(monkeypatch, tmp_path)
    monkeypatch.delenv("MODEL_SMART", raising=False)
    bot.cmd_model(1, "opus 5.5")
    assert "Nothing changed" in sent[-1]
    bot.cmd_model(1, "claude-opus-5-5")
    assert "claude-opus-5-5" in sent[-1]
    bot.cmd_model(1, "reset")
    assert "default" in sent[-1]


def test_status_and_help(monkeypatch, tmp_path):
    bot, sent = make(monkeypatch, tmp_path)
    bot.cmd_status(1, "")
    assert "Not editing" in sent[-1]
    bot.cmd_help(1, "")
    assert "/cancel" in sent[-1] and "/account" not in sent[-1]
