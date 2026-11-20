---
name: default
description: Fast-paced vertical talking-head reel. Something new on screen every 3-4 seconds, literal B-roll, bold captions with coloured key words.
---
# Default edit style

This is the starting rulebook. Run `python -m reel_editor learn-style <folder of your finished reels>` to replace it
with one learned from YOUR own edits (it is written in exactly this format).

## Who the videos are for
Short vertical reels (TikTok, Reels, Shorts) where one person talks to the camera and explains, teaches or sells.

## Cutting
- Keep only the last clean take of every line. A take was good when the speaker moves on to the next line after it.
- Remove false starts, retake cues ("wait", "let me redo that", "again"), talk to someone off camera and fillers
  (um, uh) when they can be cut cleanly.
- Joins must flow: a breath-length beat inside a sentence, a touch more at a sentence end. No dead air.
- Slightly faster than real life (1.05x) so it feels energetic without sounding sped up.

## Captions
- One short phrase at a time (3-9 words), one line preferred, two at most.
- Punctuation chain: a caption that does not finish the sentence ends with a comma or nothing, and the next
  starts lowercase. Only commas and periods ("?" only for a real question).
- Key words (the nouns, numbers and verbs that carry the meaning) in the accent colour; the word being spoken
  is highlighted.
- Numbers spoken as "number one" become "#1".

## Visual rhythm
- Something new on screen every 3-4 seconds (never more than 4 s): B-roll most often, plus icon stickers,
  kinetic text cards for every numbered step and key number, punch-in zooms for emphasis, and black-and-white
  for clearly negative moments.
- The opening hook stays on the speaker's face (zoom, text or a sticker are fine) so viewers connect with the
  person first. The closing introduction + call to action also stays on the face.
- Never the same non-B-roll effect twice in a row.

## B-roll
- B-roll must show the exact words, literally and concretely. Simple, common visual metaphors are good.
  Examples of GOOD pairings:
  - "save a little every month" -> coins dropping into a jar
  - "the market crashed" -> a red stock chart falling
  - "talk to your team" -> people around a meeting table
  - "it takes time" -> an hourglass running
  - "check the numbers" -> a hand using a calculator
- REJECT: generic stock people (someone typing on a laptop, sitting at a desk, holding a phone) unless the words
  are literally about that action; foreign-language text; dark, blurry or cartoon clips; a tiny subject lost in
  an empty frame; anything with a watermark.
- Each clip cuts in a little before its words and holds about a second after the phrase (about 3 s on screen),
  with at least 1 s of the speaker's face between two B-rolls.

## Camera
- Locked framing: a punch-in frames the face once and holds it (no wobbling auto-tracking), with at most one
  slow, eased push. Run the motion check before delivery.

## Sound
- Voice denoised, loudness -14 LUFS, lip sync checked on the final cut.

```json settings
{
  "speed": 1.05,
  "event_types": ["broll", "sticker", "text", "zoom", "bw"],
  "event_max_gap": 4.0,
  "event_target_gap": 3.4,
  "event_min_gap": 1.8,
  "broll_every_sec": 8,
  "hook_no_broll": 7.5,
  "zoom": 1.15,
  "zoom_push": 0.04,
  "caption_themes": ["classic", "sunrise", "ocean", "citrus", "ember", "violet"],
  "captions_hide_under_broll": true,
  "loudness_lufs": -14
}
```
