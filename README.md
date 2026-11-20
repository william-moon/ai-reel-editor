# AI Reel Editor

Turn a raw selfie / talking-head recording into a finished vertical reel (or a 16:9 landscape video), automatically:

- keeps only your last good take of every line (false starts, "wait, let me redo that", off-camera talk removed)
- removes dead air with clean, natural-sounding joins (every cut sits in the silence between words, found from the
  loudness curve, so no half-word ever leaks in), speeds up slightly, denoises, sets loudness to -14 LUFS, checks lip sync
- reels: word-by-word animated subtitles for the whole video with coloured key words, in a look generated fresh for
  every video (font, colours, highlight, entry animation) and never repeated
- landscape (16:9) videos: frequent dynamic on-screen text instead of subtitles - punch words, icon badges,
  takeaway cards, count-up numbers, tag lists and numbered steps, written from what is said in that video
- something new on screen every 3-4 seconds: B-roll that matches your exact words, icon stickers, text cards,
  smooth punch-in zooms, black-and-white for negative moments
- writes a title, description, hashtags and target keyword for posting
- **learns your own editing style** from your past finished reels and follows it from then on

Claude is the brain (it picks takes, writes captions, plans the visuals and looks at B-roll thumbnails to choose
clips). Everything else is free, open-source software running on your computer or server.

## Easiest install: ask your AI agent

Tell your agent (Claude Code, a Telegram bot running Claude Code on a VPS, or Hermes Agent):

> Install this repo: https://github.com/william-moon/ai-reel-editor and set it up for me.

It follows [`SKILL.md`](SKILL.md): installs everything, asks you two questions (where B-roll comes from, where
videos go), walks you through two free API keys, makes a test edit and reports back.

## What each piece does

| Software | What it does here | Cost |
|---|---|---|
| **Claude** (via Claude Code `claude -p`, or the Anthropic API) | Decides which takes to keep, writes captions, plans visuals, picks and double-checks B-roll by looking at thumbnails, writes post copy, learns your style | ~$0.60-1.20 per reel (on your Claude plan with Claude Code, or API billing) |
| **ffmpeg** | Cuts, encodes and mixes the video and audio | free |
| **faster-whisper** (Whisper speech recognition) | Turns your speech into words with exact timestamps | free (runs locally; optional Groq API for slow servers) |
| **Pexels + Pixabay APIs** | Free stock video for B-roll, searched in parallel | free keys |
| **OpenCV + MediaPipe** | Finds your face for steady punch-ins and measures lip sync | free (optional) |
| **Pillow** | Draws captions, stickers and text cards | free |
| **rclone** | Connects the Google Drive "Raw Videos" / "Finished Videos" folders | free (optional) |
| **Telegram bot** | Send a video from your phone, get the edit back, tap Approve / Change | free (optional) |
| **Envato adapter** | Envato has no download API, so it uses your own paid account in the editor's own Chrome Canary (log in once, the login is kept permanently) | your subscription (optional) |

## Manual install

Requirements: Python 3.10-3.12, ffmpeg. The installers get both for you.

```bash
./install.sh                     # macOS / Linux   (add --with-rclone for Google Drive)
powershell -ExecutionPolicy Bypass -File install.ps1    # Windows  (add -WithRclone)
.venv/bin/python -m reel_editor doctor
```

Then put your keys in `.env` (see [`.env.example`](.env.example)) or let `python -m reel_editor config set KEY -`
do it. Make a test edit:

```bash
.venv/bin/python -m reel_editor sample workspace/sample-raw.mp4
.venv/bin/python -m reel_editor edit workspace/sample-raw.mp4
```

## Using it

```bash
python -m reel_editor edit "my raw video.mp4" --thumb thumbnail.png     # one video
python -m reel_editor watch                                             # edit everything dropped in "Raw Videos"
python -m reel_editor learn-style ~/my-best-reels --name mystyle --activate   # learn your style
python -m reel_editor bot                                               # optional Telegram bot
python -m reel_editor motion-check final.mp4 --region face              # camera smoothness check
```

Each finished video comes with a `- review.jpg` (one frame per visual event, to check the B-roll),
`- post.json` (title, description, hashtags, keyword), `- claude-usage.json` (Claude cost for this video) and,
when stock footage was used, `- credits.txt`.

Every step saves its decision as JSON in the work dir (`words.json`, `takes.json`, `spans.json`, `captions.json`,
`visuals.json`). Edit one by hand and re-run to override it, or `--redo <step>` to have it redone.
`--notes "use less B-roll, no black and white"` passes change requests to every Claude decision.

## Your style = one file

`styles/default/STYLE.md` is the starting rulebook. `learn-style` studies 3-10 of your finished reels (cut rhythm,
speaking pace, loudness, and contact sheets of frames that Claude looks at: caption look, B-roll share, stickers,
emoji, text cards, zooms, titles) and writes `styles/<name>/STYLE.md` in the same format: plain-English rules that
Claude reads before every decision, plus a JSON block with the numbers the code enforces (how often something new
appears, which effects are allowed, zoom amount, caption looks ...). Change it by hand any time.

## Camera rule (why punch-ins look smooth)

Auto-tracking that follows every head movement looks like a shaky handheld camera. Each punch-in instead uses
**locked framing** (the median face position over the whole shot, from tracking interpolated between samples) and
**one slow eased push**, rendered with sub-pixel resampling, hard cut in and out. The pipeline measures every
punch-in for jolts, and `motion-check` gives the same measurement for any video (a smooth talking-head edit lands
around 11-22 jolts per 20 s; over ~25 means the camera is stepping).

## Running it unattended

- **macOS (launchd)**: a `~/Library/LaunchAgents/com.example.reel-editor.plist` with `ProgramArguments`
  `[<repo>/.venv/bin/python, -m, reel_editor, watch, --once]`, `WorkingDirectory` `<repo>`, `StartInterval` 300.
  Load with `launchctl load ~/Library/LaunchAgents/com.example.reel-editor.plist`.
- **Linux (cron)**: `*/5 * * * * cd /path/to/ai-reel-editor && .venv/bin/python -m reel_editor watch --once >> workspace/watch.log 2>&1`
- **Windows (Task Scheduler)**: a task every 5 minutes running `.venv\Scripts\python.exe -m reel_editor watch --once`
  with "Start in" set to the repo folder.

Only one edit runs at a time (a lock file stops overlaps).

## Platforms

Developed and tested on macOS (Apple Silicon). The code avoids macOS-only parts (no Apple emoji font, no hard-coded
paths, hardware encoder only when available, libx264 elsewhere) and has installers for Linux and Windows, but see
"Status" below for what has actually been run where.

## Status / known limits

- Tested end to end on macOS with a generated sample (no stock B-roll). Pexels/Pixabay adapters are unit-tested
  with mocked responses; a real keyed search runs during setup (`test-keys`).
- Linux and Windows installers and the Telegram bot, Google Drive OAuth and Envato adapter have not been run on
  real accounts/machines yet by the authors: please report issues.
- Editor browser (`browser` command): starting Canary with its permanent profile, status and the background
  service are unit-tested; the automatic Envato sign-in with a saved login has NOT been run against Envato's
  live sign-in page (it cannot pass 2-step codes or captchas; then log in once with `browser login envato`).
- Envato login vault (`vault`): AES-256-GCM, master key in the OS keychain (tested live on
  macOS Keychain; Windows Credential Manager and Linux Secret Service not yet run). Headless Linux without a
  keychain falls back to a mode-600 key file: that keeps the login out of the repo, logs and chat, but someone
  already logged in as that user on the server could read it.
- Captions and post copy assume English by default (`LANGUAGE` in `.env` changes the speech recognizer; the Claude
  prompts are in English).
- Telegram's cloud Bot API limits bots to 20 MB downloads: use the Raw Videos folder (or a self-hosted Bot API
  server) for full-quality raw files.

## Licences

Code: MIT (`LICENSE`). Fonts, icons, emoji, models and stock footage have their own licences:
see [`THIRD-PARTY-LICENSES.md`](THIRD-PARTY-LICENSES.md). If your videos use emoji stickers, credit OpenMoji
(CC BY-SA 4.0).
