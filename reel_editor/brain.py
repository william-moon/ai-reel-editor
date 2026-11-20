"""The brain: Claude.

Two ways to reach it (CLAUDE_MODE=auto tries them in this order):
  cli  - the `claude -p` command of Claude Code, when installed and logged in (runs on the user's Claude plan;
         `total_cost_usd` is the CLI's list-price equivalent).
  api  - the official `anthropic` Python SDK with ANTHROPIC_API_KEY (billed per token; required for agents
         such as Hermes that cannot use Claude plan limits).

Every call adds to a per-video usage file (calls, tokens, cost in USD) like the original editor did.
"""
import base64
import json
import os
import re
import subprocess
import tempfile
import threading

from . import config as C

# list prices per million tokens (input, output, cache read, cache write 5 min)
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0, 0.10, 2.50),
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.00),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}

USAGE = {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "cache_tokens": 0}
USAGE_FILE = None      # set by the pipeline: <work>/usage.json, a running total for one video across runs
NOTES = ""             # the creator's change requests for a redo, added to every prompt
_LOCK = threading.Lock()
_MODE = None


class BrainError(RuntimeError):
    pass


def resolve_model(name):
    if name in ("sonnet", "fast"):
        return C.MODEL_FAST
    if name in ("opus", "smart"):
        return C.MODEL_SMART
    return name


def cli_available():
    exe = C.claude_cli()
    return bool(exe) and (C.have(exe[0]) or os.path.exists(exe[0]))


def mode():
    """'cli' or 'api' (decided once per process)."""
    global _MODE
    if _MODE:
        return _MODE
    want = C.CLAUDE_MODE
    if want in ("cli", "api"):
        _MODE = want
    elif cli_available():
        _MODE = "cli"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        _MODE = "api"
    else:
        raise BrainError("No Claude access: install + log in to Claude Code (`claude`), or set ANTHROPIC_API_KEY in .env")
    return _MODE


def _record(model, inp, out, cache_r, cache_w, cost):
    with _LOCK:
        USAGE["calls"] += 1
        USAGE["cost_usd"] += cost
        USAGE["input_tokens"] += inp
        USAGE["output_tokens"] += out
        USAGE["cache_tokens"] += cache_r + cache_w
        if USAGE_FILE:
            tot = {k: 0 for k in USAGE}
            if os.path.exists(USAGE_FILE):
                with open(USAGE_FILE) as fh:
                    tot.update(json.load(fh))
            tot["calls"] += 1
            tot["cost_usd"] = round(tot["cost_usd"] + cost, 4)
            tot["input_tokens"] += inp
            tot["output_tokens"] += out
            tot["cache_tokens"] += cache_r + cache_w
            tot["mode"] = _MODE
            with open(USAGE_FILE, "w") as fh:
                json.dump(tot, fh, indent=1)


def ask(prompt, model="sonnet", images=(), timeout=900):
    """Send one prompt (+ optional image files) to Claude, return the text answer."""
    model = resolve_model(model)
    if NOTES:
        prompt = ("THE CREATOR'S CHANGE REQUESTS FOR THIS VIDEO (follow them where they apply to this step):\n"
                  + NOTES + "\n\n" + prompt)
    if mode() == "cli":
        return _ask_cli(prompt, model, images, timeout)
    return _ask_api(prompt, model, images, timeout)


def _ask_cli(prompt, model, images, timeout):
    cmd = C.claude_cli() + ["-p", "--model", model, "--output-format", "json"]
    if images:
        prompt = ("Use the Read tool to look at these image files first:\n"
                  + "\n".join(f"- {os.path.abspath(p)}" for p in images) + "\n\n" + prompt)
        cmd += ["--allowedTools", "Read"]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout,
                       cwd=tempfile.gettempdir(), encoding="utf-8")
    try:
        d = json.loads(r.stdout)
    except Exception:
        raise BrainError("claude CLI failed: " + (r.stderr or r.stdout)[-400:])
    if r.returncode != 0 or d.get("is_error"):
        raise BrainError("claude CLI error: " + str(d.get("result") or r.stderr)[-400:])
    u = d.get("usage") or {}
    _record(model, int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
            int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0),
            float(d.get("total_cost_usd") or 0))
    return d.get("result") or ""


_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        import anthropic
        _CLIENT = anthropic.Anthropic(timeout=600.0, max_retries=3)
    return _CLIENT


def _image_block(path):
    ext = os.path.splitext(path)[1].lower()
    media = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}.get(ext, "image/jpeg")
    with open(path, "rb") as fh:
        data = base64.standard_b64encode(fh.read()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def _ask_api(prompt, model, images, timeout):
    import anthropic
    content = [_image_block(p) for p in images] + [{"type": "text", "text": prompt}]
    kw = dict(model=model, max_tokens=16000, messages=[{"role": "user", "content": content}])
    client = _client()
    try:
        # server-side refusal fallback (beta): a declined request is re-run on a fallback model in the same call
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                           extra_body={"fallbacks": "default"}, **kw)
    except anthropic.BadRequestError as ex:
        if "fallback" not in str(ex).lower():
            raise BrainError(f"Claude API rejected the request: {ex}")
        resp = client.messages.create(**kw)           # account without the beta: plain request
    except anthropic.AuthenticationError:
        raise BrainError("ANTHROPIC_API_KEY was rejected (check the key in .env)")
    except anthropic.RateLimitError as ex:
        raise BrainError(f"Claude API rate limit: {ex}")
    except anthropic.APIStatusError as ex:
        raise BrainError(f"Claude API error {ex.status_code}: {ex}")
    except anthropic.APIConnectionError as ex:
        raise BrainError(f"Cannot reach the Claude API: {ex}")
    if resp.stop_reason == "refusal":
        raise BrainError("Claude declined this request")
    u = resp.usage
    inp, out = int(u.input_tokens or 0), int(u.output_tokens or 0)
    cr = int(getattr(u, "cache_read_input_tokens", 0) or 0)
    cw = int(getattr(u, "cache_creation_input_tokens", 0) or 0)
    pi, po, pcr, pcw = PRICES.get(resp.model, PRICES.get(model, (4.0, 20.0, 0.2, 5.0)))
    cost = (inp * pi + out * po + cr * pcr + cw * pcw) / 1e6
    _record(model, inp, out, cr, cw, cost)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def extract_json(txt):
    """First JSON object/array in a reply (tolerates ```json fences, prose before it and text after it)."""
    m = re.search(r"```(?:json)?\s*(.*?)```", txt, re.S)
    for s in ([m.group(1)] if m else []) + [txt]:
        dec = json.JSONDecoder()
        for i, ch in enumerate(s):
            if ch in "[{":
                try:
                    return dec.raw_decode(s[i:])[0]
                except json.JSONDecodeError:
                    continue
    raise BrainError("no JSON in Claude's answer: " + txt[:200])


def ask_json(prompt, model="sonnet", images=(), tries=2):
    """ask() + extract_json(), asking once more if the answer was not valid JSON"""
    last = None
    for k in range(tries):
        ans = ask(prompt if k == 0 else prompt + "\n\nIMPORTANT: reply with ONLY the JSON, nothing before or after it.",
                  model=model, images=images)
        try:
            return extract_json(ans)
        except BrainError as ex:
            last = ex
    raise last
