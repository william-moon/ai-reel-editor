import random
from reel_editor import captions as C, dyntext as D, studio as S


def test_every_video_gets_a_new_look():
    hist = []
    for i in range(60):
        t = C.make_look(["classic", "sunrise", "ocean", "citrus", "ember", "violet"], hist, random.Random(i))
        sig = C.look_signature(t)
        assert sig not in hist
        hist.append(sig)


def test_lowkey_family_stays_lowkey():
    t = C.make_look(["lowkey_red"], [], random.Random(3))
    assert t.get("lowkey") and t["active"] == "box"


def test_load_theme_accepts_names_and_looks():
    assert C.load_theme("ocean")["active"] == "scale"
    look = C.make_look(["classic"], [], random.Random(1))
    assert C.load_theme({"theme": look}.get("theme"))["name"] == look["name"]


def test_dyntext_clean_drops_bad_and_overlapping_items():
    ev = [{"type": "pop", "start": 1, "dur": 2, "text": "One take"},
          {"type": "pop", "start": 2, "dur": 2, "text": "Overlap"},
          {"type": "stat", "start": 6, "dur": 2, "value": "many"},
          {"type": "emoji", "start": 9, "dur": 2, "text": "x"},
          {"type": "chips", "start": 12, "dur": 2, "items": ["A", "B"]}]
    out = D.clean(ev, 30)
    assert [e["type"] for e in out] == ["pop", "pop", "chips"]
    assert out[1]["start"] >= out[0]["start"] + out[0]["dur"]


def test_landscape_prompts_and_event_types():
    S.LANDSCAPE = True
    try:
        assert "landscape (16:9)" in S.orient("You are the video editor for a vertical (9:16) talking-head reel.")
        S.ST.s["event_types"] = ["broll", "sticker", "text", "zoom", "bw", "pip"]
        S.PROVIDER = object()
        assert set(S.event_types()) <= {"broll", "zoom", "bw"}
    finally:
        S.LANDSCAPE = False
        S.PROVIDER = None
