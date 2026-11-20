---
name: ai-reel-editor
description: Install, set up and run the AI reel editor - turns a raw selfie/talking-head recording into a finished vertical reel or 16:9 video (clean cuts of retakes and dead air, full subtitles on reels / dynamic on-screen text on landscape, a unique look per video, B-roll every 3-4 s, stickers, smooth punch-ins, loudness) with Claude as the brain, and can learn a creator's own edit style from their past videos. Use when the user says "install this repo", "set up the video editor", "edit this video/reel", "learn my editing style", or drops a raw video to edit.
---

# AI reel editor - install-and-run playbook (for the agent)

You (the agent) do ALL the work. The person only answers **two questions** and pastes keys / clicks one
"Allow" button. Never ask them to run commands. Never print secrets back to them or into logs.
Work from the repo folder (the folder containing this file). Commands below use `PY`:

- macOS / Linux: `PY=./.venv/bin/python`
- Windows (PowerShell): `$PY = ".\.venv\Scripts\python.exe"`

## 1. Install (no questions yet)

1. Run the installer. It installs ffmpeg and Python 3.10-3.12 if missing (Homebrew / apt / dnf / winget),
   creates `.venv`, installs the packages, downloads two small models and creates `.env`:
   - macOS / Linux: `./install.sh`
   - Windows: `powershell -ExecutionPolicy Bypass -File install.ps1`
   If a system package needs sudo and you cannot get it, tell the person the ONE command to run, then continue.
2. `PY -m reel_editor doctor` - fix anything marked FAIL (lines marked `--` are optional).
3. **Brain (Claude)** - decide, do not ask:
   - If `claude` (Claude Code) is on PATH and logged in, the editor uses it (`CLAUDE_MODE=auto`) and runs on the
     person's Claude plan. Check with `PY -m reel_editor doctor --brain` (one tiny call).
   - Otherwise (e.g. Hermes Agent, or a server without Claude Code) it needs an Anthropic API key: open
     https://console.anthropic.com/settings/keys for them (or send the link), have them paste the key, save it with
     `echo "<key>" | PY -m reel_editor config set ANTHROPIC_API_KEY -` and set `PY -m reel_editor config set CLAUDE_MODE api`.
     Tell them the cost: about $0.60-1.20 of Claude per finished reel; everything else is free.

## 2. Ask the two questions (one message, with your recommendation)

> **1) Where should B-roll (stock footage) come from?**
>   a) Pexels + Pixabay together - free, recommended (I'll set up both free keys with you, ~3 minutes; two
>      libraries means more and better clips to choose from)
>   b) Envato - only if you already pay for Envato (you log in once in the editor's own browser and it stays
>      logged in)
>   c) None - no stock footage, just captions, stickers, text cards and zooms
>
> **2) Where should videos go in and out?** I'll use **Google Drive** unless you'd rather not - OK?
>   a) Google Drive (default) - I create "Raw Videos" and "Finished Videos" folders in your Drive (Google shows
>      one "Allow" page). Drop videos from your phone or computer, get finished reels back in Drive.
>   b) A folder on this computer instead - nothing to click

Google Drive is the default (`STORAGE=gdrive`), but ALWAYS get a yes on it before setting it up: never connect
their Drive without that confirmation. If they say "you pick", use 1a and say you will use Google Drive, then
wait for their OK. If they decline Drive, use 2b.

## 3a. B-roll = Pexels + Pixabay (`BROLL_SOURCE=stock`, the default)

Always set up BOTH. They are searched together and the best clip wins, so two libraries give clearly better
picks than one. Both have a free official API, so no browser login is needed for them.

1. Open the key pages for them (on a desktop: `open URL` on macOS, `xdg-open URL` on Linux, `start URL` on
   Windows; on a VPS / Telegram bot: send the links):
   - Pexels: https://www.pexels.com/api/new/ - sign up / log in, fill the short form ("what are you building":
     "personal video editing tool"), the key is shown right away.
   - Pixabay: https://pixabay.com/accounts/register/ then https://pixabay.com/api/docs/ - when logged in, the key is
     shown in the "Parameters" section next to `key (required)`.
2. They paste each key to you. Save without echoing:
   `echo "<pexels key>" | PY -m reel_editor config set PEXELS_API_KEY -`
   `echo "<pixabay key>" | PY -m reel_editor config set PIXABAY_API_KEY -`
3. Test: `PY -m reel_editor test-keys` (one real search each). If one fails, ask them to re-copy that key.
4. If they only gave one key, you can start editing, but ask once more for the other and say why (more and better
   clips). Do not keep asking after that.
5. Rule for any stock source: use its API when it has one. Only a site with NO API goes through the editor's own
   browser (3b) - today that is Envato only.

## 3b. B-roll = Envato (optional, the person's own paid subscription)

Envato has no public download API, so the editor uses its OWN browser: Google Chrome Canary with a permanent
profile (`~/.ai-reel-editor/browser-profile`, outside the repo). It is separate from their everyday Chrome, keeps
the login across restarts and updates, and a background job keeps it running and refreshes the session daily, so
they should not have to log in again.

1. `PY -m reel_editor config set BROLL_SOURCE envato`, then `./install.sh --with-envato` (Windows: `install.ps1
   -WithEnvato`). This installs Node packages and Chrome Canary (macOS via Homebrew, Windows via winget; Linux has
   no Canary, so Chrome/Chromium is used with the same separate profile).
2. Start with a) - the Canary login. Use b) only if a) does not work for them (no screen on a server, the window
   will not open, or the login keeps dropping):
   > I'll open my own browser window on the Envato sign-in page. Log in once and it stays logged in.
   - If a) fails, offer b):
   > Plan B: give me your Envato email and password. I keep them only in this computer's private settings file
   > and use them to sign in by myself. (On Telegram, delete your message after I confirm it is saved.)
   - b) needs an Envato account with an EMAIL + PASSWORD login. First ask: "Did you sign up for Envato with
     Google, Apple or Facebook instead of typing an email and password?" If yes, help them add a password
     (2 minutes, their account stays the same):
     1. Open https://account.envato.com/reset_password and enter the email of their Google/Apple/Facebook
        account. Envato emails a link; they set a password (check spam if it does not arrive within a few
        minutes).
     2. If that does not give them a password: sign in at https://account.envato.com with Google as usual, open
        Account settings, click "Disconnect Google Account" (or Apple/Facebook), confirm, then follow the
        password-reset email Envato sends.
     3. If disconnecting fails, the usual cause is 2-step verification on the Envato account: they turn it off
        in Account settings first (the automatic sign-in cannot type 2-step codes anyway).
     Then save the email + password as below. Help: https://help.elements.envato.com/hc/en-us/articles/900003430786
   - a) Desktop: `PY -m reel_editor browser login envato` - a Canary window opens on the Envato sign-in page;
     they log in (tick "Remember me"). The command waits, confirms, and moves the browser to the background.
     Headless server: there is no window to show, so use b), or have them log in on a desktop install and copy
     the `browser-profile` folder over.
   - b) Save without echoing:
     `echo "<email>" | PY -m reel_editor config set ENVATO_EMAIL -`
     `echo "<password>" | PY -m reel_editor config set ENVATO_PASSWORD -`
     then `PY -m reel_editor browser keepalive` (signs in and confirms). If it reports `2fa`, help them turn
     off 2-step verification for Envato (step b.3); if `captcha`, have them log in once with a) on any desktop.
3. Make it permanent: `PY -m reel_editor browser install-service` (start at login + daily keep-alive at 04:20;
   on a Linux server also `loginctl enable-linger $USER` once).
4. Check: `PY -m reel_editor browser status` must show `"envato_logged_in": true`.
5. Tell them plainly: it uses THEIR subscription, downloads are licensed per project (check Envato's terms), and
   the editor never presses Envato's AI-generation buttons.

## 3c. B-roll = none

`PY -m reel_editor config set BROLL_SOURCE none`

## 4a. Storage = Google Drive (default, after they confirm)

1. Install rclone: `./install.sh --with-rclone` (Windows: `install.ps1 -WithRclone`).
2. Desktop with a browser: `PY -m reel_editor storage-setup gdrive` - rclone opens Google's consent page; the
   person clicks **Allow** once. The command then creates "Raw Videos" (with "Done" inside) and "Finished Videos".
3. Headless server / VPS / Telegram-only: on any computer that has a browser (theirs) they need rclone and run
   `rclone authorize "drive"`, click Allow, and paste you the token it prints (a JSON line). Then run
   `PY -m reel_editor storage-setup gdrive --token '<token JSON>'`. Treat the token as a secret.

## 4b. Storage = local folder

`PY -m reel_editor storage-setup local` - creates `~/ReelEditor/Raw Videos` and `~/ReelEditor/Finished Videos`
(change with `config set LOCAL_ROOT <path>` first if they want another place). Tell them the exact paths.

## 5. Test edit on a generated sample (always do this)

1. `PY -m reel_editor sample workspace/sample-raw.mp4` (~20 s talking face with a false start + retake).
2. `PY -m reel_editor edit workspace/sample-raw.mp4 --out workspace/sample-edited.mp4`
   (first run downloads the speech model, ~1.5 GB; on a slow VPS set `WHISPER_MODEL=small` or add a
   `GROQ_API_KEY`).
3. Check: the JSON printed at the end has `"qa": []` (final check passed); `ffprobe workspace/sample-edited.mp4`
   plays (1080x1920, h264 + aac); `PY -m reel_editor motion-check workspace/sample-edited.mp4 --region face`
   says smooth; look at `workspace/sample-edited - review.jpg` yourself (one frame per visual event) and confirm
   the false start ("...save money on" + "wait, let me redo that") is gone.
4. Report to the person in plain words: what was installed, which B-roll + storage they chose, the test result
   (length before/after, Claude cost from `- claude-usage.json`), and how to use it (section 7). Send them the test
   video if you can.

## 5b. Write their agent files (always do this)

`PY -m reel_editor agent-docs` writes CLAUDE.md + AGENTS.md tuned to what they chose (B-roll source, storage,
Claude plan vs API key, Telegram on/off, OS): rules, limits and fixes for THEIR stack only. It also writes
`telegram-agent/CLAUDE.md` + `AGENTS.md`: the personality and commands (/help /status /cancel /model /compact
/style /privacy) for an AI agent that chats with them on Telegram. Run it again whenever a setting changes.
Only the GENERATED block is replaced; never put names, keys, tokens or user ids in these files.

## 6. Learn their style (offer this once setup works)

Ask for 3-10 of their own finished, posted reels (the more typical the better). Put them in a folder and run:
`PY -m reel_editor learn-style "<folder>" --name <their-name-or-brand-slug> --activate`
It measures rhythm/pacing/loudness, makes contact sheets, and Claude writes `styles/<name>/STYLE.md`: their
rulebook (plain English + the numbers the code enforces). Every future edit follows it. Show them the rulebook and
edit it by hand when they ask for changes ("more B-roll", "no emoji", "captions lower") - that file IS the style.
Never put personal details into a STYLE.md.

## 7. Daily use

- One video: `PY -m reel_editor edit "<raw video>" [--thumb thumbnail.png] [--notes "changes"]`
  Output: `workspace/finished/<name>/` (final mp4, `- review.jpg`, `- post.json` title/description/hashtags,
  `- claude-usage.json`, `- credits.txt` for stock clips).
- Drop-and-go: `PY -m reel_editor watch` edits everything that lands in "Raw Videos" and delivers to
  "Finished Videos". To keep it running: macOS launchd / Linux systemd or cron (`*/5 * * * * cd <repo> &&
  .venv/bin/python -m reel_editor watch --once`) / Windows Task Scheduler (`.venv\Scripts\python.exe -m
  reel_editor watch --once` every 5 minutes). README has examples.
- Redo with changes: `PY -m reel_editor edit "<raw>" --work <same work dir> --redo captions --notes "..."`
  (steps: transcribe, takes, spans, sync, captions, visuals, render; each step's JSON in the work dir can be
  hand-edited, e.g. swap a B-roll clip in `visuals.json`).
- Telegram (optional): token from @BotFather -> `config set TELEGRAM_BOT_TOKEN -`; they send /start to the bot,
  it replies with their user id -> `config set TELEGRAM_ALLOWED_USER_IDS <id>`; run `PY -m reel_editor bot`, then
  `PY -m reel_editor agent-docs`. Typing "/" in the bot shows its commands: /help, /status (what it is editing and
  for how long), /cancel (stops the running edit and clears the queue), /model, /style, /privacy. Each edit has a
  hard cap (`EDIT_TIMEOUT_MIN`, default 45).
- Telegram through an AI agent instead (Claude Code Telegram channel, Hermes Agent gateway, or another bridge):
  run the agent with `telegram-agent/` as its working folder so it loads `telegram-agent/CLAUDE.md` / `AGENTS.md`.
  Bots can only download 20 MB from Telegram: big raw files go in the "Raw Videos" folder instead.

## Rules for you

- Before calling any edit done: `qa` empty (or explain each item), look at the review sheet's B-roll, and run
  `motion-check --region face` on anything with punch-ins. The pipeline already checks each punch-in for jolts.
- The work dir holds transcript-derived files: delete it when the video is final (`PY -m reel_editor clean <work>`;
  `watch` does this automatically).
- Never commit or print `.env`, keys, tokens, or rclone config. Never post videos anywhere: the person downloads
  and posts them.
- Costs: Claude only (~$0.60-1.20 per reel with B-roll; less without). Pexels, Pixabay, ffmpeg, Whisper, rclone are
  free.
