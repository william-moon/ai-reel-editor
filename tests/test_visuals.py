"""The rhythm rules enforced in code (no Claude involved)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reel_editor import studio  # noqa: E402
from reel_editor.style import Style  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def caps(n=20, step=2.0):
    return [{"start": k * step, "end": k * step + step - 0.1,
             "words": [{"w": f"word{k}", "s": k * step, "e": k * step + 0.5, "key": False}]} for k in range(n)]


def setup_function(_):
    studio.ST = Style(os.path.join(ROOT, "styles", "default", "STYLE.md"))
    studio.PROVIDER = object()      # pretend a B-roll source exists


def changes(plan):
    t = []
    for e in plan:
        t.append(e["start"])
        if e["type"] in ("broll", "zoom", "bw", "pip"):
            t.append(e["start"] + e["dur"])
    return sorted(t)


def test_empty_plan_gets_filled_every_4s():
    c = caps()
    plan = studio.enforce_visuals([], 40.0, c)
    assert plan and all(e["type"] == "zoom" for e in plan)
    ch = [0.0] + changes(plan)
    assert max(b - a for a, b in zip(ch, ch[1:])) <= 4.0 + 0.61


def test_broll_rules():
    c = caps()
    plan = [{"type": "broll", "start": 2.0, "dur": 2.0, "queries": ["x"], "words": "hook"},          # in the hook
            {"type": "broll", "start": 10.0, "dur": 2.0, "queries": ["coins"], "words": "a"},
            {"type": "broll", "start": 13.0, "dur": 2.0, "queries": ["jar"], "words": "too close"},
            {"type": "sticker", "start": 20.0, "dur": 1.5, "icon": "not-an-icon", "words": "s"},
            {"type": "broll", "start": 20.5, "dur": 2.0, "words": "no queries"},
            {"type": "weird", "start": 25.0, "dur": 2.0}]
    out = studio.enforce_visuals(plan, 40.0, c)
    br = [e for e in out if e["type"] == "broll"]
    assert len(br) == 1 and br[0]["start"] == round(10.0 - 0.4, 3)          # lead applied, hook/too-close dropped
    assert 2.6 <= br[0]["dur"] <= 4.2
    st = [e for e in out if e["type"] == "sticker"]
    assert st and st[0]["icon"] == "star"
    assert not [e for e in out if e["type"] == "weird"]
    planned = sorted(e["start"] for e in out if not e.get("auto"))
    assert min(b - a for a, b in zip(planned, planned[1:])) >= 1.8 - 1e-6     # planned events: min gap
    srt = sorted(out, key=lambda e: e["start"])
    for a, b in zip(srt, srt[1:]):                                              # nothing overlaps (+0.3 s air)
        assert a["start"] + a["dur"] + 0.3 <= b["start"] + 1e-6
    assert all(e["dur"] >= 1.2 for e in out if e.get("auto"))


def test_no_broll_when_no_provider():
    studio.PROVIDER = None
    assert "broll" not in studio.event_types() and "pip" not in studio.event_types()
    assert "broll" not in studio.visuals_prompt().split("Rules:")[0]


def test_captions_from_groups_chain_and_coverage():
    kw = [{"i": i, "w": w, "s": i * 0.4, "e": i * 0.4 + 0.3} for i, w in
          enumerate(["Good", "planning", "works.", "whether", "you", "start", "small"])]
    groups = [{"first": 0, "last": 2, "tokens": ["Good", "planning", "works."], "keys": [1]},
              {"first": 3, "last": 5, "tokens": ["whether", "you", "start"], "keys": []}]
    c = studio.captions_from_groups(kw, groups)
    assert c[0]["words"][-1]["w"] == "works,"            # period before a lowercase continuation -> comma
    assert c[0]["words"][1]["key"] is True
    assert c[-1]["words"][0]["w"] == "small"            # a skipped word still gets a caption
