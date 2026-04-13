"""
config.py — Load settings from ~/.config/paulie/paulie.conf (TOML).

The config file is optional.  Environment variables always take precedence
over config file values — the config file only fills in values that are not
already set in the environment.

Supported keys (all optional):

    silence_s     = 1.0       # seconds of silence before recording stops
    vad_threshold = 0.45      # silero-VAD speech probability cutoff (0.0–1.0)
    model         = "nemo-parakeet-tdt-0.6b-v3"   # onnx-asr model name
    device        = ""        # sounddevice input device name or integer index

Example ~/.config/paulie/paulie.conf:

    silence_s     = 0.8
    vad_threshold = 0.40
    # model = "whisper-base"
    # device = "HDA Intel PCH"
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

logger = logging.getLogger(__name__)

# Mapping from config file key → environment variable name.
_KEY_TO_ENV: dict[str, str] = {
    "silence_s":         "PAULIE_SILENCE_S",
    "vad_threshold":     "PAULIE_VAD_THRESHOLD",
    "max_record_s":      "PAULIE_MAX_RECORD_S",
    "model":             "PAULIE_MODEL",
    "device":            "PAULIE_DEVICE",
    "inject_mode":       "PAULIE_INJECT",
    "mode":              "PAULIE_MODE",
    "utterance_pause_s": "PAULIE_UTTERANCE_PAUSE_S",
    "filler_words":      "PAULIE_FILLER_WORDS",
    "ui_backend":        "PAULIE_UI_BACKEND",
}

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "paulie" / "paulie.conf"


_DEFAULT_CONFIG_CONTENT = """\
# ~/.config/paulie/paulie.conf
# Paulie STT daemon configuration — TOML format.
#
# Environment variables always override these values.
# Restart paulie-daemon after making changes:
#   systemctl --user restart paulie-daemon

# Seconds of silence after speech before recording stops and transcription begins.
# Lower values feel snappier; higher values give you more time to pause mid-sentence.
silence_s = 1.0

# silero-VAD speech probability cutoff (0.0–1.0).
# Raise this if background noise triggers false starts.
# Lower this if the first syllable of a word is being clipped.
vad_threshold = 0.45

# Hard ceiling on recording duration in seconds, regardless of VAD state.
# Increase this for long-form dictation (lectures, meeting notes, etc.).
# At 180 wpm a 10-minute recording produces roughly 10 800 chars — well within
# what ydotool can inject.  Set to 0 to disable the ceiling (not recommended).
max_record_s = 120.0

# onnx-asr model name.  All models are downloaded automatically from HuggingFace
# on first use and cached in ~/.cache/huggingface/hub/.
#
# ── English / European (25 languages) ────────────────────────────────────────
# nemo-parakeet-tdt-0.6b-v3     640 MB   25 EU langs  Fast, default, recommended
# nemo-parakeet-tdt-0.6b-v2     640 MB   English      Slightly higher EN accuracy
# nemo-parakeet-rnnt-0.6b       620 MB   English      RNN-T decoder variant
# nemo-parakeet-ctc-0.6b        620 MB   English      CTC decoder, simplest
# nemo-canary-1b-v2             980 MB   25 EU langs  Highest accuracy; slower
#
# ── Multilingual (99+ languages, includes Whisper) ───────────────────────────
# onnx-community/whisper-tiny    39 MB   99+ langs    Fastest; lower accuracy
# onnx-community/whisper-base   140 MB   99+ langs    Good balance
# onnx-community/whisper-small  367 MB   99+ langs    Better accuracy
# onnx-community/whisper-large-v3-turbo  809 MB  99+ langs  Near-large quality
#
# ── Russian ───────────────────────────────────────────────────────────────────
# gigaam-v3-rnnt                220 MB   Russian      Best Russian accuracy
# gigaam-v3-ctc                 220 MB   Russian      CTC variant
# gigaam-v3-e2e-rnnt            220 MB   Russian      Includes punctuation
#
model = "nemo-parakeet-tdt-0.6b-v3"

# Microphone device — name substring or integer index.
# Leave empty to use the system default.
# List available devices with:
#   paulie-daemon --list-devices
device = ""

# Text injection mode.
#   ydotool   — simulate keystrokes via uinput (default, requires ydotoold)
#   clipboard — write to clipboard and send Ctrl+V (requires wl-clipboard + wtype,
#               or xclip + xdotool on X11; no ydotoold needed)
inject_mode = "ydotool"

# Dictation mode.
#   single    — one recording per hotkey press, transcribed as a whole (default)
#   utterance — mic stays open across sentences; each sentence
#               is transcribed and injected as you finish speaking it
#               (works best with ui_backend = "gtk" or "auto" on KDE/sway —
#               the GTK backend keeps focus on the target window between
#               injections; Qt can cause focus to drift)
mode = "single"

# Utterance mode only: seconds of silence that end one sentence and trigger
# transcription of that sentence.  The next sentence begins when you speak again.
# In utterance mode, set silence_s higher (e.g. 2.0) so the session doesn't
# end during a natural thinking pause between sentences.
utterance_pause_s = 0.5

# Strip common spoken filler words (um, uh, you know, etc.) from transcriptions.
# Can also be toggled live from the system tray icon without restarting.
filler_words = false

# Overlay UI backend.
#   auto   — use GTK + wlr-layer-shell on native Wayland if available, Qt otherwise
#   qt     — always use PyQt6 (works on X11, XWayland, and Wayland)
#   gtk    — always use GTK + wlr-layer-shell (requires python3-gobject + gtk-layer-shell)
#
# The GTK backend places the overlay on the compositor's OVERLAY layer so it
# never appears in alt-tab and never steals keyboard focus on KDE Plasma / sway.
# GNOME does not support wlr-layer-shell; use qt or auto on GNOME.
#
# To enable the GTK backend on Bazzite:
#   rpm-ostree install gtk-layer-shell python3-gobject python3-cairo
#   # reboot, then:
#   pipx reinstall paulie --system-site-packages
ui_backend = "auto"
"""


def write_default_config() -> None:
    """
    Write a commented default config file to ``~/.config/paulie/paulie.conf``.

    Exits with a non-zero status if the file already exists (to avoid
    silently overwriting user edits) or if the directory cannot be created.
    """
    import sys

    config_path = Path(os.environ.get("PAULIE_CONFIG", str(_DEFAULT_CONFIG_PATH)))

    if config_path.exists():
        print(
            f"error: config file already exists at {config_path}\n"
            "Remove it first if you want to regenerate defaults.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_DEFAULT_CONFIG_CONTENT, encoding="utf-8")
    except OSError as exc:
        print(f"error: could not write config file: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Config written to {config_path}")


def apply_config() -> None:
    """
    Read the TOML config file and set environment variables for any key that
    is not already overridden by the calling environment.

    Must be called before audio.py / stt.py read their settings so that the
    values are in place when those modules first check ``os.environ``.

    Safe to call multiple times — subsequent calls are no-ops if all keys are
    already set in the environment.
    """
    config_path = Path(os.environ.get("PAULIE_CONFIG", str(_DEFAULT_CONFIG_PATH)))

    if not config_path.exists():
        logger.debug("No config file found at %s — using defaults.", config_path)
        return

    try:
        with open(config_path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Could not read config file %s: %s", config_path, exc)
        return

    for key, env_var in _KEY_TO_ENV.items():
        if key not in data:
            continue
        if env_var in os.environ:
            logger.debug(
                "Config: %s ignored — overridden by environment (%r).",
                key, os.environ[env_var],
            )
            continue
        value = str(data[key])
        os.environ[env_var] = value
        logger.info("Config: set %s = %r (from %s)", env_var, value, config_path)
