"""Editor browser (Chrome Canary, permanent profile): no real browser is started here."""
import os
import platform

from reel_editor import browser as B
from reel_editor import config as C
from reel_editor import agent_docs as A


def test_default_port_does_not_take_over_9222():
    assert B.port() != 9222 or os.environ.get("BROWSER_CDP") or os.environ.get("ENVATO_CDP")


def test_profile_is_outside_the_repo():
    prof = os.path.realpath(B.profile_dir())
    assert not prof.startswith(os.path.realpath(C.ROOT))


def test_launch_args_keep_profile_and_debug_port():
    a = B._args(visible=False, url="https://example.com/")
    assert f"--user-data-dir={B.profile_dir()}" in a
    assert f"--remote-debugging-port={B.port()}" in a
    assert "--remote-debugging-address=127.0.0.1" in a
    assert "--headless=new" in a and a[-1] == "https://example.com/"
    assert "--headless=new" not in B._args(visible=True)


def test_canary_preferred(monkeypatch):
    monkeypatch.setattr(C, "BROWSER_BIN", "")
    if platform.system() == "Darwin":
        canary = "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"
        chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        monkeypatch.setattr(os.path, "exists", lambda p: p in (canary, chrome))
        assert B.find_browser() == canary and B.is_canary(canary)
    assert not B.is_canary("/usr/bin/chromium")


def test_keepalive_asks_for_login_without_saved_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_NO_KEYCHAIN", "1")
    monkeypatch.setattr(B, "running", lambda: {"Browser": "x"})
    monkeypatch.setattr(B, "logged_in", lambda site="envato": False)
    monkeypatch.delenv("ENVATO_EMAIL", raising=False)
    monkeypatch.delenv("ENVATO_PASSWORD", raising=False)
    logs = []
    assert B.keepalive("envato", log=logs.append) is False
    assert "browser login envato" in logs[-1]


def test_keepalive_signs_back_in_with_saved_login(monkeypatch, tmp_path):
    monkeypatch.setattr(B, "running", lambda: {"Browser": "x"})
    monkeypatch.setattr(B, "logged_in", lambda site="envato": False)
    from reel_editor import vault
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_NO_KEYCHAIN", "1")
    vault.put("ENVATO_EMAIL", "person@example.com")
    vault.put("ENVATO_PASSWORD", "PASSWORD-VALUE")
    calls = []
    monkeypatch.setattr(B, "_node", lambda *a, **k: calls.append(a) or '{"logged_in": true}')
    logs = []
    assert B.keepalive("envato", log=logs.append) is True
    assert calls == [("login", "envato")]
    assert all("PASSWORD-VALUE" not in l and "person@example.com" not in l for l in logs)


def test_envato_doc_explains_canary_and_never_leaks_login(monkeypatch):
    monkeypatch.setenv("BROLL_SOURCE", "envato")
    monkeypatch.setenv("ENVATO_PASSWORD", "PASSWORD-VALUE")
    monkeypatch.setenv("ENVATO_EMAIL", "person@example.com")
    doc = A.repo_doc(A.setup())
    assert "Chrome Canary" in doc and "browser login envato" in doc
    assert "PASSWORD-VALUE" not in doc and "person@example.com" not in doc


def test_stock_doc_recommends_both(monkeypatch):
    monkeypatch.setenv("BROLL_SOURCE", "stock")
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    doc = A.repo_doc(A.setup())
    assert "Keep BOTH keys" in doc and "Pixabay key missing" in doc


def test_envato_doc_has_plan_b_for_google_signups(monkeypatch):
    monkeypatch.setenv("BROLL_SOURCE", "envato")
    doc = A.repo_doc(A.setup())
    assert "plan B" in doc and "reset_password" in doc and "Disconnect Google" in doc


def test_vault_encrypts_and_refuses_tampering(monkeypatch, tmp_path):
    from reel_editor import vault
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_NO_KEYCHAIN", "1")
    monkeypatch.delenv("ENVATO_EMAIL", raising=False)
    monkeypatch.delenv("ENVATO_PASSWORD", raising=False)
    vault.put("ENVATO_EMAIL", "person@example.com")
    vault.put("ENVATO_PASSWORD", "PASSWORD-VALUE")
    assert vault.get("ENVATO_PASSWORD") == "PASSWORD-VALUE" and vault.has_login()
    raw = (tmp_path / "vault.bin").read_bytes()
    assert b"PASSWORD-VALUE" not in raw and b"person@example.com" not in raw
    assert oct((tmp_path / "vault.bin").stat().st_mode & 0o777) == "0o600"
    assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"
    bad = bytearray(raw); bad[-1] ^= 1
    (tmp_path / "vault.bin").write_bytes(bytes(bad))
    assert vault.get("ENVATO_PASSWORD") is None          # tampered file is refused, not half-read
    import pytest
    with pytest.raises(ValueError):
        vault.put("PEXELS_API_KEY", "x")


def test_plain_text_login_in_env_moves_into_vault(monkeypatch, tmp_path):
    from reel_editor import vault
    env = tmp_path / ".env"
    env.write_text("ENVATO_PASSWORD=PASSWORD-VALUE\nOTHER=1\n")
    monkeypatch.setattr(C, "ENV_FILE", str(env))
    monkeypatch.setattr(C, "set_env_value", lambda k, v: env.write_text(
        "\n".join(l if not l.startswith(k + "=") else f"{k}={v}" for l in env.read_text().splitlines()) + "\n"))
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path / "s"))
    monkeypatch.setenv("VAULT_NO_KEYCHAIN", "1")
    monkeypatch.setenv("ENVATO_PASSWORD", "PASSWORD-VALUE")
    assert vault.migrate_from_env() == ["ENVATO_PASSWORD"]
    assert "PASSWORD-VALUE" not in env.read_text() and "OTHER=1" in env.read_text()
    assert vault.get("ENVATO_PASSWORD") == "PASSWORD-VALUE"


def test_keepalive_pipes_login_on_stdin_not_env(monkeypatch, tmp_path):
    from reel_editor import vault
    monkeypatch.setenv("SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("VAULT_NO_KEYCHAIN", "1")
    vault.put("ENVATO_EMAIL", "person@example.com")
    vault.put("ENVATO_PASSWORD", "PASSWORD-VALUE")
    monkeypatch.setattr(B, "running", lambda: {"Browser": "x"})
    monkeypatch.setattr(B, "logged_in", lambda site="envato": False)
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        class R:
            returncode, stdout, stderr = 0, '{"logged_in": true}', ""
        return R()
    monkeypatch.setattr(B.subprocess, "run", fake_run)
    assert B.keepalive("envato", log=lambda *a: None) is True
    assert "PASSWORD-VALUE" in seen["input"]
    assert all("PASSWORD-VALUE" not in str(a) for a in seen["cmd"])
    assert "ENVATO_PASSWORD" not in seen["env"]
