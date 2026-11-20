"""Pexels + Pixabay adapters with mocked HTTP (no keys, no network)."""
import io
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reel_editor.broll import stock  # noqa: E402

PEXELS = {"videos": [
    {"id": 101, "width": 1920, "height": 1080, "duration": 12, "url": "https://www.pexels.com/video/coins-in-jar-101/",
     "image": "https://images.pexels.com/101.jpg", "user": {"name": "A"},
     "video_files": [{"link": "https://v/101-sd.mp4", "file_type": "video/mp4", "width": 640, "height": 360},
                     {"link": "https://v/101-hd.mp4", "file_type": "video/mp4", "width": 1920, "height": 1080},
                     {"link": "https://v/101-4k.mp4", "file_type": "video/mp4", "width": 3840, "height": 2160}]},
    {"id": 102, "width": 1080, "height": 1920, "duration": 8, "url": "https://www.pexels.com/video/piggy-bank-102/",
     "image": "https://images.pexels.com/102.jpg", "user": {"name": "B"},
     "video_files": [{"link": "https://v/102-hd.mp4", "file_type": "video/mp4", "width": 1080, "height": 1920}]},
]}
PIXABAY = {"hits": [
    {"id": 201, "pageURL": "https://pixabay.com/videos/id-201/", "tags": "money, coins, savings", "duration": 15,
     "user": "C", "videos": {
         "large": {"url": "https://p/201-l.mp4", "width": 3840, "height": 2160, "thumbnail": "https://p/201-l.jpg"},
         "medium": {"url": "https://p/201-m.mp4", "width": 1920, "height": 1080, "thumbnail": "https://p/201-m.jpg"},
         "small": {"url": "https://p/201-s.mp4", "width": 1280, "height": 720, "thumbnail": "https://p/201-s.jpg"}}},
    {"id": 202, "pageURL": "https://pixabay.com/videos/id-202/", "tags": "jar, coin", "duration": 5, "user": "D",
     "picture_id": "999", "videos": {
         "medium": {"url": "https://p/202-m.mp4", "width": 1080, "height": 1920},
         "large": {"url": "", "width": 0, "height": 0}}},
]}

CALLS = []


def fake_get(url, headers=None, timeout=30):
    CALLS.append((url, headers or {}, threading.current_thread().name))
    if url.startswith(stock.PEXELS_URL):
        assert headers and headers.get("Authorization") == "pexels-test-key"
        return json.dumps(PEXELS).encode()
    if url.startswith(stock.PIXABAY_URL):
        assert "key=pixabay-test-key" in url
        return json.dumps(PIXABAY).encode()
    if url.endswith(".jpg"):
        return b"\xff\xd8\xff fake jpeg"
    raise AssertionError("unexpected url " + url)


def setup_function(_):
    CALLS.clear()
    stock.http_get = fake_get


def test_pexels_parse_and_pick_hd():
    res = stock.Pexels("pexels-test-key").search("coins jar", "portrait", 5)
    assert [r["id"] for r in res] == ["pexels-101", "pexels-102"]
    assert res[0]["download_url"] == "https://v/101-hd.mp4"          # HD rendition, not 4K or SD
    assert res[0]["title"] == "coins in jar"
    assert "orientation=portrait" in CALLS[0][0]


def test_pixabay_parse_thumbnail_fallback():
    res = stock.Pixabay("pixabay-test-key").search("coins jar", "portrait", 5)
    assert res[0]["download_url"] == "https://p/201-m.mp4"
    assert res[0]["thumbUrl"] == "https://p/201-m.jpg"
    assert res[1]["thumbUrl"] == "https://i.vimeocdn.com/video/999_640x360.jpg"   # older API: picture_id
    assert "safesearch=true" in CALLS[0][0]


def test_merged_parallel_vertical_first():
    p = stock.StockProvider("pexels-test-key", "pixabay-test-key")
    res = p.search("coins jar", orientation="vertical", limit=10)
    ids = [r["id"] for r in res]
    assert set(ids) == {"pexels-101", "pexels-102", "pixabay-201", "pixabay-202"}
    assert ids[0] in ("pexels-102", "pixabay-202") and ids[1] in ("pexels-102", "pixabay-202")   # vertical first
    assert {c[2] for c in CALLS} != {threading.current_thread().name}                         # ran in worker threads
    assert len(CALLS) == 2


def test_one_source_failing_still_returns_other():
    import urllib.error

    def half(url, headers=None, timeout=30):
        if url.startswith(stock.PEXELS_URL):
            raise urllib.error.HTTPError(url, 401, "bad key", {}, io.BytesIO(b""))
        return fake_get(url, headers, timeout)
    stock.http_get = half
    logs = []
    res = stock.StockProvider("pexels-test-key", "pixabay-test-key").search("x", log=logs.append)
    assert [r["source"] for r in res] == ["pixabay", "pixabay"]
    assert any("401" in l for l in logs)


def test_thumb_and_download_cached(tmp_path, monkeypatch):
    p = stock.StockProvider("pexels-test-key", None)
    cand = p.search("coins", limit=1)[0]
    t = p.fetch_thumb(cand, str(tmp_path / "t.jpg"))
    assert open(t, "rb").read().startswith(b"\xff\xd8")
    opened = []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        opened.append(req.full_url)
        return Resp(b"\x00" * 20000)
    monkeypatch.setattr(stock.urllib.request, "urlopen", fake_urlopen)
    f1 = p.download(cand, str(tmp_path / "cache"))
    f2 = p.download(cand, str(tmp_path / "cache"))
    assert f1 == f2 and os.path.getsize(f1) == 20000
    assert opened == ["https://v/102-hd.mp4"] or opened == ["https://v/101-hd.mp4"]      # downloaded once
    meta = json.load(open(f1 + ".json"))
    assert meta["source"] == "pexels" and meta["page_url"].startswith("https://www.pexels.com/")


def test_test_keys_reports(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "pexels-test-key")
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    res = stock.test_keys(log=lambda *a: None)
    assert res == {"pexels": True, "pixabay": None}
