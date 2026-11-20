"""Brain wiring with Claude mocked (no network, no cost)."""
import json
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reel_editor import brain  # noqa: E402


def test_extract_json_variants():
    assert brain.extract_json('Sure!\n```json\n{"n": 3}\n```') == {"n": 3}
    assert brain.extract_json('[1, 2] trailing') == [1, 2]
    assert brain.extract_json('[{"a": 1}]\n\nNote: index [3] was merged {see above}') == [{"a": 1}]
    assert brain.extract_json('Here [is] the plan: {"keep": [[1, 2]]}') == {"keep": [[1, 2]]}


def test_cli_mode_parses_and_logs_usage(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, input=None, **kw):
        seen["cmd"], seen["input"] = cmd, input
        out = {"result": '{"ok": true}', "is_error": False, "total_cost_usd": 0.0123,
               "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(out), stderr="")
    monkeypatch.setattr(brain.subprocess, "run", fake_run)
    monkeypatch.setattr(brain, "_MODE", "cli")
    monkeypatch.setattr(brain, "USAGE_FILE", str(tmp_path / "usage.json"))
    img = tmp_path / "sheet.jpg"
    img.write_bytes(b"x")
    ans = brain.ask("pick one", model="sonnet", images=[str(img)])
    assert brain.extract_json(ans) == {"ok": True}
    assert "--model" in seen["cmd"] and "claude-sonnet-5-5" in seen["cmd"] and "Read" in seen["cmd"]
    assert str(img) in seen["input"]
    u = json.load(open(tmp_path / "usage.json"))
    assert u["calls"] == 1 and abs(u["cost_usd"] - 0.0123) < 1e-9


def test_api_mode_builds_image_blocks_and_prices(tmp_path, monkeypatch):
    sent = {}

    class FakeMessages:
        def create(self, **kw):
            sent.update(kw)
            return types.SimpleNamespace(
                stop_reason="end_turn", model="claude-opus-5-5",
                content=[types.SimpleNamespace(type="text", text='{"n": 2}')],
                usage=types.SimpleNamespace(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0,
                                            cache_creation_input_tokens=0))
    fake = types.SimpleNamespace(beta=types.SimpleNamespace(messages=FakeMessages()), messages=FakeMessages())
    monkeypatch.setattr(brain, "_client", lambda: fake)
    monkeypatch.setattr(brain, "_MODE", "api")
    monkeypatch.setattr(brain, "USAGE_FILE", str(tmp_path / "usage.json"))
    img = tmp_path / "sheet.png"
    img.write_bytes(b"\x89PNG")
    ans = brain.ask("check", model="opus", images=[str(img)])
    assert brain.extract_json(ans) == {"n": 2}
    assert sent["model"] == "claude-opus-5-5"
    blocks = sent["messages"][0]["content"]
    assert blocks[0]["type"] == "image" and blocks[0]["source"]["media_type"] == "image/png"
    assert sent["extra_body"] == {"fallbacks": "default"}
    u = json.load(open(tmp_path / "usage.json"))
    assert abs(u["cost_usd"] - (1000 * 4 + 100 * 20) / 1e6) < 1e-9
