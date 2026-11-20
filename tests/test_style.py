import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reel_editor.style import Style, DEFAULTS  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_default_style_parses():
    s = Style(os.path.join(ROOT, "styles", "default", "STYLE.md"))
    assert s.name == "default"
    assert s["speed"] == 1.05 and "broll" in s["event_types"]
    assert "```json" not in s.text and "Visual rhythm" in s.text
    assert s.rulebook().startswith("THE CREATOR'S STYLE RULEBOOK")


def test_override_and_dur_limits_merge(tmp_path):
    p = tmp_path / "STYLE.md"
    p.write_text('---\nname: t\ndescription: d\n---\nRules here.\n```json settings\n'
                 '{"event_max_gap": 3.0, "dur_limits": {"zoom": [1.0, 2.0]}, "brand_logo": "logo.png"}\n```\n')
    s = Style(str(p))
    assert s["event_max_gap"] == 3.0
    assert s["dur_limits"]["zoom"] == [1.0, 2.0] and s["dur_limits"]["broll"] == DEFAULTS["dur_limits"]["broll"]
    assert s.resolve(s["brand_logo"]) == str(tmp_path / "logo.png")


def test_missing_file_gives_defaults():
    s = Style("/nonexistent/STYLE.md")
    assert s["zoom"] == DEFAULTS["zoom"] and s.rulebook() == ""
