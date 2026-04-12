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
    "silence_s":     "PAULIE_SILENCE_S",
    "vad_threshold": "PAULIE_VAD_THRESHOLD",
    "model":         "PAULIE_MODEL",
    "device":        "PAULIE_DEVICE",
}

_DEFAULT_CONFIG_PATH = Path.home() / ".config" / "paulie" / "paulie.conf"


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
