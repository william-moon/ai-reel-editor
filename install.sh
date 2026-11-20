#!/usr/bin/env bash
# Installer for macOS and Linux (incl. VPS). Safe to re-run. Non-interactive (bots can run it).
#   ./install.sh                 core install
#   ./install.sh --with-rclone   + rclone (Google Drive storage)
#   ./install.sh --with-envato   + Node packages for the optional Envato adapter
#   ./install.sh --no-optional   skip mediapipe (face tracking / lip-sync check)
set -euo pipefail
cd "$(dirname "$0")"

WITH_RCLONE=0; WITH_ENVATO=0; OPTIONAL=1
for a in "$@"; do
  case "$a" in
    --with-rclone) WITH_RCLONE=1 ;;
    --with-envato) WITH_ENVATO=1 ;;
    --no-optional) OPTIONAL=0 ;;
  esac
done

say() { printf '\n==> %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
SUDO=""; if [ "$(id -u)" != "0" ] && have sudo; then SUDO="sudo"; fi

pkg_install() {   # pkg_install <brew name> <apt name> <dnf name>
  if have brew; then brew install "$1"
  elif have apt-get; then $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "$2"
  elif have dnf; then $SUDO dnf install -y -q "$3"
  else echo "Please install $1 yourself, then re-run."; exit 1; fi
}

say "ffmpeg"
if ! have ffmpeg || ! have ffprobe; then pkg_install ffmpeg ffmpeg ffmpeg; fi
ffmpeg -version | head -1

say "Python 3.10-3.12 (mediapipe has wheels for these)"
PY=""
for c in python3.12 python3.11 python3.10 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  if have brew; then brew install python@3.12; PY=python3.12
  elif have apt-get; then $SUDO apt-get update -qq && $SUDO apt-get install -y -qq python3 python3-venv python3-pip; PY=python3
  elif have dnf; then $SUDO dnf install -y -q python3.12 || $SUDO dnf install -y -q python3; PY=$(command -v python3.12 || command -v python3)
  else echo "Install Python 3.10-3.12, then re-run."; exit 1; fi
fi
if have apt-get && ! "$PY" -c 'import venv, ensurepip' 2>/dev/null; then
  $SUDO apt-get install -y -qq python3-venv || true
fi
"$PY" --version

say "virtual environment (.venv) + packages"
[ -d .venv ] || "$PY" -m venv .venv
./.venv/bin/python -m pip install -q --upgrade pip
./.venv/bin/python -m pip install -q -r requirements.txt
if [ "$OPTIONAL" = 1 ]; then
  ./.venv/bin/python -m pip install -q -r requirements-optional.txt || echo "(optional packages skipped: editor works without face tracking)"
fi

say "models (face tracking + voice denoise)"
./.venv/bin/python -m reel_editor fetch-assets || echo "(model download failed: edits still work, re-run later)"

if [ "$WITH_RCLONE" = 1 ] && ! have rclone; then
  say "rclone (Google Drive)"
  if have brew; then brew install rclone; else curl -fsSL https://rclone.org/install.sh | $SUDO bash; fi
fi
if [ "$WITH_ENVATO" = 1 ]; then
  say "Envato adapter (Node.js)"
  have node || pkg_install node nodejs nodejs
  (cd tools && npm install --silent)
  # the editor's own browser (Chrome Canary, permanent login profile). Linux has no Canary: Chrome/Chromium is used.
  if [ "$(uname)" = "Darwin" ] && [ ! -d "/Applications/Google Chrome Canary.app" ] && have brew; then
    brew install --cask google-chrome@canary || echo "(install Chrome Canary: https://www.google.com/chrome/canary/)"
  fi
fi

# a Linux box without a desktop TTS voice can still make the test sample
if [ "$(uname)" = "Linux" ] && ! have espeak-ng && ! have espeak && have apt-get; then
  $SUDO apt-get install -y -qq espeak-ng || true
fi

[ -f .env ] || { cp .env.example .env; chmod 600 .env; echo "created .env"; }
mkdir -p workspace

say "check"
./.venv/bin/python -m reel_editor doctor || true
echo
echo "Installed. Next: follow SKILL.md (B-roll keys, storage, test edit)."
