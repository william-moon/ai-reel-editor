# Third-party material

The code in this repository is MIT licensed (see `LICENSE`). It ships or downloads the following third-party
material, each under its own licence.

## Bundled in this repository (`assets/fonts/`)

| File(s) | Project | Licence | Full text |
|---|---|---|---|
| `Rubik-Bold.ttf`, `Rubik-ExtraBold.ttf`, `Rubik-VF.ttf`, `Rubik-Italic-VF.ttf` | Rubik, Copyright 2015 The Rubik Project Authors | SIL Open Font License 1.1 | `licenses/OFL-Rubik.txt` |
| `Poppins-*.ttf` | Poppins, Copyright 2020 The Poppins Project Authors | SIL Open Font License 1.1 | `licenses/OFL-Poppins.txt` |
| `Montserrat-VF.ttf`, `Montserrat-Italic-VF.ttf` | Montserrat, Copyright 2024 The Montserrat Project Authors | SIL Open Font License 1.1 | `licenses/OFL-Montserrat.txt` |
| `fa-solid-900.ttf` | Font Awesome 6 Free (Solid) by Fonticons, Inc. | Font: SIL OFL 1.1; icons: CC BY 4.0 | `licenses/FontAwesome-LICENSE.txt` |
| `fa-icons.json` | icon name -> code point table for the Font Awesome font above | facts, no licence needed | |

The variable fonts were renamed on disk only (`Rubik[wght].ttf` -> `Rubik-VF.ttf`); the fonts themselves are
unmodified and keep their internal names.

## Downloaded on first use (not in the repository)

| What | Source | Licence / terms |
|---|---|---|
| Emoji images (emoji stickers, titles) | OpenMoji 15.1 (https://openmoji.org), fallback Twemoji 15.1 (https://github.com/jdecked/twemoji) | OpenMoji: CC BY-SA 4.0. Twemoji graphics: CC BY 4.0. **If you publish videos that contain these emoji, credit them**, e.g. "Emoji: OpenMoji (CC BY-SA 4.0)". |
| `models/face_landmarker.task` | Google MediaPipe Face Landmarker | Apache License 2.0 |
| `models/sh.rnnn` (voice denoise model) | rnnoise-models by GregorR (somnolent-hogwash) | The model author states the trained models are not subject to copyright |
| Speech recognition model (Whisper large-v3-turbo, CTranslate2 conversion) | OpenAI Whisper via faster-whisper / Hugging Face | MIT |
| Stock B-roll clips | Pexels (https://www.pexels.com/license/) and Pixabay (https://pixabay.com/service/license-summary/) | Free to use, including commercially, no attribution required; you may not resell or redistribute the clips unaltered or imply endorsement. Pixabay forbids hotlinking, so clips are downloaded and cached locally. A credits file is written next to every finished video anyway. Check the current terms yourself. |
| Envato clips (optional adapter) | Your own Envato subscription, through your own logged-in browser | Envato licence terms (licensed per project). Check them yourself. |

## Python packages (installed by pip, not bundled)

numpy (BSD), Pillow (MIT-CMU), opencv-python-headless (Apache 2.0), faster-whisper (MIT), ctranslate2 (MIT),
anthropic (MIT), mediapipe (Apache 2.0, optional), pytest (MIT, tests only).

## External programs (installed separately)

ffmpeg (LGPL/GPL depending on the build), rclone (MIT, optional), Node.js + playwright-core (MIT/Apache 2.0, only
for the optional Envato adapter), Claude Code (Anthropic's terms).
