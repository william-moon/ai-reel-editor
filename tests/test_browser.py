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


def test_keepalive_asks_for_login_without_saved_credentials(monkeypatch):
    monkeypatch.setattr(B, "running", lambda: {"Browser": "x"})
    monkeypatch.setattr(B, "logged_in", lambda site="envato": False)
    monkeypatch.delenv("ENVATO_EMAIL", raising=False)
    monkeypatch.delenv("ENVATO_PASSWORD", raising=False)
    logs = []
    assert B.keepalive("envato", log=logs.append) is False
    assert "browser login envato" in logs[-1]


def test_keepalive_signs_back_in_with_saved_login(monkeypatch):
    monkeypatch.setattr(B, "running", lambda: {"Browser": "x"})
    monkeypatch.setattr(B, "logged_in", lambda site="envato": False)
    monkeypatch.setenv("ENVATO_EMAIL", "person@example.com")
    monkeypatch.setenv("ENVATO_PASSWORD", "PASSWORD-VALUE")
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
