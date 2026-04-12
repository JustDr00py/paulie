"""
stt.py — Parakeet-TDT-0.6B-V3 inference via onnx-asr.

Uses the ONNX INT8 quantized model, which runs fast on CPU with no GPU
required.  The onnx-asr package downloads model weights automatically from
HuggingFace on first use (~640 MB) and caches them in
~/.cache/huggingface/hub/.

onnx-asr accepts numpy arrays directly, so no temp file is needed.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE: int = 16_000
# PAULIE_MODEL is read inside load_model() so that config.apply_config() can
# set it before the model is loaded.  The default is documented here for reference.
_DEFAULT_MODEL = "nemo-parakeet-tdt-0.6b-v3"

# Module-level singleton — model loaded once per process.
_MODEL: Any = None
_MODEL_LOCK = threading.Lock()


def load_model() -> Any:
    """
    Load and return the Parakeet-TDT-0.6B-V3 ONNX model.

    Cached after first call; subsequent calls return immediately.
    Thread-safe: protected by a module-level lock so parallel callers don't
    trigger a double-load race.

    Requires: pip install 'onnx-asr[cpu,hub]'
    """
    global _MODEL  # noqa: PLW0603
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL

        try:
            import onnx_asr  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "onnx-asr is required.  Install it with:\n"
                "  pip install 'onnx-asr[cpu,hub]'"
            ) from exc

        model_name = os.environ.get("PAULIE_MODEL", _DEFAULT_MODEL)
        logger.info("Loading %s …  (downloading weights on first run)", model_name)
        _MODEL = onnx_asr.load_model(model_name)
        logger.info("Model ready.")
        return _MODEL


def transcribe(model: Any, audio: np.ndarray) -> str:
    """
    Transcribe a float32 mono 16 kHz numpy array.

    Parameters
    ----------
    model:
        Instance returned by ``load_model()``.
    audio:
        Float32 numpy array, mono, 16 kHz.

    Returns
    -------
    str
        Recognised text, stripped of leading/trailing whitespace.
        Returns ``""`` on recoverable inference errors.

    Raises
    ------
    MemoryError
        Re-raised as-is so the caller can surface an OOM condition rather
        than silently returning empty text.
    """
    if audio is None or len(audio) == 0:
        logger.warning("Empty audio buffer — skipping transcription.")
        return ""

    try:
        logger.info("Running inference on %.2f s of audio …", len(audio) / SAMPLE_RATE)
        # onnx-asr accepts numpy arrays directly; no temp file needed.
        result = model.recognize(audio, sample_rate=SAMPLE_RATE)
        return str(result).strip()
    except MemoryError:
        logger.exception("Out of memory during transcription.")
        raise  # caller should handle OOM explicitly rather than silently swallowing it
    except Exception:
        logger.exception("Transcription failed.")
        return ""
