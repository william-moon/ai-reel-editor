"""A creator's style = one STYLE.md file.

  ---
  name: my-style
  description: one line
  ---
  # free-text rulebook (Claude reads ALL of it before every editing decision)
  ...
  ```json settings
  { "speed": 1.05, "event_types": ["broll", "sticker", "text", "zoom", "bw"], ... }
  ```

The JSON block holds the numbers the code enforces (rhythm, durations, zoom, captions ...); anything missing falls
back to DEFAULTS below. `learn-style` writes this file from a creator's own finished edits.
"""
import json
import os
import re

DEFAULTS = {
    # pacing / output
    "speed": 1.05,                 # playback speed of the talking head
    "fps": 30,
    "width": 1080, "height": 1920,
    "format": "auto",              # auto = follow the raw video (landscape in -> 16:9 out), or "vertical" / "landscape"
    "landscape_size": [1920, 1080],
    "landscape_text_gap": 4.0,     # landscape: dynamic on-screen text about this often (where it fits)
    "output_scale": 1,             # 2 = export 2160x3840 (4K) with the same layout
    "loudness_lufs": -14.0,
    "denoise": True,
    "gap_keep": 0.24,              # pauses up to this (raw seconds) stay as they are
    "join_mid": [0.05, 0.05],      # (tail, head) silence kept at a cut inside a sentence
    "join_end": [0.09, 0.08],      # ... after . ? !
    "max_join_gap": 0.30,
    # visual rhythm
    "event_types": ["broll", "sticker", "text", "zoom", "bw"],   # also available: pip, emoji, title
    "event_max_gap": 4.0,          # something new on screen at least this often (start to start)
    "event_target_gap": 3.4,
    "event_min_gap": 1.8,
    "broll_every_sec": 8,          # roughly one B-roll cutaway per this many seconds
    "broll_lead": 0.4,             # B-roll cuts in this much before its words
    "broll_extra": 1.0,            # ... and holds this much longer than the phrase
    "broll_face_gap": 1.0,         # face on screen at least this long between two B-rolls
    "hook_no_broll": 7.5,          # opening seconds stay on the face (zoom/text/sticker are fine)
    "cta_no_broll": True,          # closing "I'm <name> ..." + call to action stays on the face
    "dur_limits": {"broll": [2.6, 4.2], "pip": [1.8, 3.6], "sticker": [1.0, 2.4], "text": [1.0, 2.6],
                   "emoji": [1.0, 2.4], "zoom": [1.0, 3.0], "bw": [1.0, 2.6]},
    "zoom": 1.15,                  # punch-in amount
    "zoom_push": 0.04,             # one slow eased push during a punch-in (0 = static)
    "clean_open": 0.0,             # seconds at the very start with no overlays at all
    # captions
    "caption_themes": ["classic", "sunrise", "ocean", "citrus", "ember", "violet"],
    "captions_hide_under_broll": False,   # reels: subtitles run the WHOLE video, over B-roll too
    "unique_caption_looks": True,  # every video gets its own caption look (font/colours/highlight/entry), never repeated
    "caption_words": [3, 9],       # words per caption
    "key_words": True,             # colour the key words
    # brand
    "brand_name": "",              # when the captions say this name ...
    "brand_logo": "",              # ... show this PNG (path relative to the STYLE.md folder) top-right
    "thumbnail_frames": 2,         # a supplied thumbnail is shown on the first N frames
}


class Style:
    def __init__(self, path=None):
        self.path = path
        self.name = "default"
        self.description = ""
        self.text = ""
        self.s = json.loads(json.dumps(DEFAULTS))
        if path and os.path.exists(path):
            self._load(path)

    def _load(self, path):
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    if k.strip() == "name":
                        self.name = v.strip()
                    elif k.strip() == "description":
                        self.description = v.strip()
            raw = raw[m.end():]
        js = re.search(r"```json[^\n]*\n(.*?)```", raw, re.S)
        if js:
            try:
                user = json.loads(js.group(1))
            except json.JSONDecodeError as ex:
                raise ValueError(f"{path}: the ```json settings block is not valid JSON ({ex})")
            for k, v in user.items():
                if k == "dur_limits" and isinstance(v, dict):
                    self.s["dur_limits"].update(v)
                else:
                    self.s[k] = v
            raw = raw[:js.start()] + raw[js.end():]
        self.text = raw.strip()

    def __getitem__(self, k):
        return self.s[k]

    def get(self, k, default=None):
        return self.s.get(k, default)

    def resolve(self, rel):
        """a path written in the style file, relative to that file's folder"""
        if not rel:
            return ""
        if os.path.isabs(rel):
            return rel
        return os.path.join(os.path.dirname(self.path or "."), rel)

    def rulebook(self):
        """the free-text part, given to Claude with every decision"""
        if not self.text:
            return ""
        return ("THE CREATOR'S STYLE RULEBOOK (their own learned rules: follow them; where they disagree with the "
                "generic rules above, the rulebook wins):\n" + self.text + "\n\n")
