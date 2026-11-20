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
    assert names == ["help", "status", "cancel", "model", "style", "privacy", "envato"]
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


def test_envato_login_flow_deletes_messages_and_encrypts(monkeypatch, tmp_path):
    from reel_editor import vault, browser
    bot, sent = make(monkeypatch, tmp_path)
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("VAULT_NO_KEYCHAIN", "1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "7")
    deleted = []
    monkeypatch.setattr(B, "api", lambda method, fields=None, **k: deleted.append(fields["message_id"])
                        if method == "deleteMessage" else {})
    monkeypatch.setattr(browser, "keepalive", lambda site, log=print: True)
    msg = lambda mid, text: {"chat": {"id": 5}, "from": {"id": 7}, "message_id": mid, "text": text}
    bot.on_message(msg(1, "/envato"))
    bot.on_message(msg(2, "person@example.com"))
    bot.on_message(msg(3, "PASSWORD-VALUE"))
    assert deleted == [2, 3]
    assert vault.get("ENVATO_EMAIL") == "person@example.com" and vault.get("ENVATO_PASSWORD") == "PASSWORD-VALUE"
    assert all("PASSWORD-VALUE" not in s and "person@example.com" not in s for s in sent)
    assert 5 not in bot.wait_cred
    bot.on_message(msg(4, "/envato forget"))
    assert vault.get("ENVATO_PASSWORD") is None
