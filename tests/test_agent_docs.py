import itertools
import os

from reel_editor import agent_docs as A


def _env(monkeypatch, broll, storage, brain, telegram):
    monkeypatch.setenv("BROLL_SOURCE", broll)
    monkeypatch.setenv("STORAGE", storage)
    monkeypatch.setenv("CLAUDE_MODE", brain)
    if telegram:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRET-TOKEN-VALUE")
    else:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("PEXELS_API_KEY", "PEXELS-SECRET")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "987654321")


def test_every_combination_is_tuned_and_has_no_secrets(monkeypatch):
    for broll, storage, brain, tg in itertools.product(["stock", "envato", "none"], ["gdrive", "local"],
                                                       ["auto", "api"], [True, False]):
        _env(monkeypatch, broll, storage, brain, tg)
        s = A.setup()
        repo, tele = A.repo_doc(s), A.telegram_doc(s)
        for doc in (repo, tele):
            for secret in ("SECRET-TOKEN-VALUE", "PEXELS-SECRET", "987654321"):
                assert secret not in doc
        assert ("Pexels + Pixabay (free" in repo) == (broll == "stock")
        assert ("B-roll: Envato" in repo) == (broll == "envato")
        assert ("Google Drive" in repo.split("## Videos in and out")[1][:40]) == (storage == "gdrive")
        for cmd in ("/help", "/status", "/cancel", "/model", "/compact", "/style", "/privacy"):
            assert cmd in tele
        assert "/account" not in tele


def test_merge_keeps_text_outside_block(tmp_path):
    p = tmp_path / "CLAUDE.md"
    p.write_text("# mine\nkeep me\n")
    A.merge(str(p), "first", "")
    A.merge(str(p), "second", "")
    t = p.read_text()
    assert "keep me" in t and "second" in t and "first" not in t
    assert t.count(A.START) == 1


def test_write_all_writes_four_files(tmp_path, monkeypatch):
    monkeypatch.setattr(A.C, "ROOT", str(tmp_path))
    paths = A.write_all()
    assert len(paths) == 4 and all(os.path.exists(p) for p in paths)
    assert os.path.exists(tmp_path / "telegram-agent" / "CLAUDE.md")


def test_telegram_agent_lists_envato_command(monkeypatch):
    _env(monkeypatch, "envato", "local", "auto", True)
    tele = A.telegram_doc(A.setup())
    assert "/envato" in tele and "vault set" in tele
