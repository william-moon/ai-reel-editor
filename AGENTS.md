# Instructions for AI agents

This repository is an automated video editor. The full install-and-run playbook is in **[SKILL.md](SKILL.md)** -
read it and follow it when the user asks you to install, set up or use this repo.

Short version:
1. Run `./install.sh` (macOS/Linux) or `install.ps1` (Windows), then `python -m reel_editor doctor`.
2. Ask the user only two questions: B-roll source (Pexels+Pixabay / Envato / none) and storage (Google Drive / local).
3. Help them get and test the keys, set up storage, run the sample test edit, report.

Code map: `reel_editor/studio.py` (pipeline), `brain.py` (Claude CLI/API), `transcribe.py`, `broll/` (Pexels,
Pixabay, Envato), `storage.py` (local / Google Drive via rclone), `camera.py` (smooth punch-ins),
`captions.py`, `fx.py`, `learn_style.py`, `jobs.py` (watch folder), `bot.py` (Telegram). Tests: `pytest tests`.
Never print or commit `.env` contents.
