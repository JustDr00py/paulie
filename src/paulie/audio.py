"""
audio.py — Microphone capture with Silero-VAD silence detection.

Records from the default input device at 16 kHz mono.  A chunk of audio is
fed to the VAD model every CHUNK_MS milliseconds.  Recording stops when:
  - Speech has been detected at least once, AND
  - Silence has persisted for longer than PAULIE_SILENCE_S seconds.

Two safety ceilings prevent runaway recording:
  - MAX_PRE_SPEECH_S: give up if no speech starts within this window.
  - MAX_RECORD_S: hard cap on total recording time regardless of VAD state.

Recording can also be cancelled at any time by setting the optional
``abort_event`` threading.Event passed to ``record_until_silence``.

Returns a numpy float32 array (16 kHz, mono) ready for transcription.
An empty array is returned when no speech was detected, the pre-speech
timeout fired, or an abort was requested.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from typing import Callable

import numpy as np
import sounddevice as sd
import torch

logger = logging.getLogger(__name__)

# ── Audio constants ────────────────────────────────────────────────────────────
SAMPLE_RATE: int = 16_000           # Hz — required by Parakeet & silero-VAD
CHUNK_MS: int = 32                  # ms per VAD chunk (must be 32 ms for silero)
CHUNK_SAMPLES: int = SAMPLE_RATE * CHUNK_MS // 1000   # 512 samples
MAX_PRE_SPEECH_S: float = 8.0       # abort if no speech starts within this window
MAX_RECORD_S: float = 120.0         # hard ceiling regardless of VAD state
# 120 s accommodates fast speakers (~180 wpm ≈ 2160 chars) while still bounding
# runaway recording from a stuck VAD.  30 s was too short for real dictation.
#
# PAULIE_SILENCE_S and PAULIE_VAD_THRESHOLD are read inside record_until_silence()
# rather than at module level so that config.apply_config() can set them before
# the first recording begins.

# Module-level singleton — loaded once per process.
_VAD_MODEL: torch.nn.Module | None = None
_VAD_LOCK = threading.Lock()


def load_vad_model() -> torch.nn.Module:
    """
    Load and return the silero-VAD model.

    Cached after first call; subsequent calls return immediately.
    Tries the PyPI ``silero-vad`` package first (v5+), then falls back to
    torch.hub for environments that only have the older package.

    Thread-safe: protected by a module-level lock so parallel callers don't
    trigger a double-load race.
    """
    global _VAD_MODEL  # noqa: PLW0603
    with _VAD_LOCK:
        if _VAD_MODEL is not None:
            return _VAD_MODEL

        try:
            from silero_vad import load_silero_vad  # type: ignore[import]
            _VAD_MODEL = load_silero_vad(onnx=False)
            logger.info("Loaded silero-VAD via silero_vad package.")
        except ImportError:
            logger.info("Falling back to torch.hub for silero-VAD.")
            _VAD_MODEL, _ = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                verbose=False,
            )

        # Set once here after loading so callers don't have to remember to do it.
        _VAD_MODEL.eval()  # type: ignore[union-attr]
        return _VAD_MODEL  # type: ignore[return-value]


def record_until_silence(
    on_speech_start: Callable[[], None] | None = None,
    abort_event: threading.Event | None = None,
) -> np.ndarray:
    """
    Block and record microphone audio until silence is detected.

    Parameters
    ----------
    on_speech_start:
        Optional zero-argument callback invoked the first time speech is
        detected.  Useful for updating the UI from "Listening…" to
        "Recording…".

        .. warning::
            This callback is invoked from the VAD processing thread, **not**
            the Qt main thread.  Any UI mutations inside the callback must be
            posted via a signal/slot or ``QMetaObject.invokeMethod``.

    abort_event:
        Optional threading.Event.  When set by an external thread, recording
        stops immediately and an empty array is returned.  The caller is
        responsible for clearing the event before the next recording begins.

    Returns
    -------
    np.ndarray
        Float32 mono PCM at 16 000 Hz.  An empty array is returned when no
        speech was detected, the pre-speech timeout fired, or an abort was
        requested.
    """
    # Read tunable settings here (not at module level) so that config values
    # applied via os.environ after import are honoured on every call.
    silence_threshold_s: float = float(os.environ.get("PAULIE_SILENCE_S", "1.0"))
    vad_threshold: float = float(os.environ.get("PAULIE_VAD_THRESHOLD", "0.45"))

    vad_model = load_vad_model()

    # silero-VAD tracks hidden state internally; reset before a new utterance.
    if hasattr(vad_model, "reset_states"):
        vad_model.reset_states()

    silence_limit_chunks = int(silence_threshold_s * SAMPLE_RATE / CHUNK_SAMPLES)
    max_chunks = int(MAX_RECORD_S * SAMPLE_RATE / CHUNK_SAMPLES)

    all_chunks: list[np.ndarray] = []
    silence_chunks: int = 0
    speech_detected: bool = False
    speech_cb_fired: bool = False
    start_time: float = time.monotonic()

    # Shared queue between the sounddevice callback (audio thread) and the
    # VAD loop (this thread) to avoid blocking the callback.
    _queue: deque[np.ndarray] = deque()
    _stop_event = threading.Event()

    def _audio_callback(indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning("sounddevice status: %s", status)
        # indata shape: (frames, channels) — take channel 0.
        _queue.append(indata[:, 0].copy())

    # PAULIE_DEVICE can be a device name substring or integer index.
    _device_env = os.environ.get("PAULIE_DEVICE", "")
    _device: str | int | None = None
    if _device_env:
        _device = int(_device_env) if _device_env.isdigit() else _device_env

    logger.info("Opening microphone stream (16 kHz mono, device=%r).", _device)
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=CHUNK_SAMPLES,
        callback=_audio_callback,
        device=_device,
    ):
        while not _stop_event.is_set():
            # Caller-requested cancel (e.g. second hotkey press).
            if abort_event is not None and abort_event.is_set():
                logger.info("Recording cancelled by abort signal.")
                break

            # Pre-speech timeout: if the user triggered recording but never
            # spoke (walked away, mic issue), don't block forever.
            if not speech_detected:
                if time.monotonic() - start_time > MAX_PRE_SPEECH_S:
                    logger.warning(
                        "No speech detected within %.1f s — aborting recording.",
                        MAX_PRE_SPEECH_S,
                    )
                    _stop_event.set()
                    continue

            # Hard cap: prevent runaway accumulation when VAD never settles
            # (e.g. continuous background noise above the threshold).
            if len(all_chunks) >= max_chunks:
                logger.warning(
                    "Maximum recording duration (%.1f s) reached — stopping.",
                    MAX_RECORD_S,
                )
                _stop_event.set()
                continue

            if not _queue:
                # Yield the GIL briefly; avoids busy-spin.
                sd.sleep(5)
                continue

            chunk = _queue.popleft()
            all_chunks.append(chunk)

            tensor = torch.from_numpy(chunk)
            with torch.no_grad():
                speech_prob: float = vad_model(tensor, SAMPLE_RATE).item()

            if speech_prob >= vad_threshold:
                silence_chunks = 0
                if not speech_detected:
                    speech_detected = True
                    logger.info("Speech detected.")
                    if on_speech_start and not speech_cb_fired:
                        speech_cb_fired = True
                        on_speech_start()
            else:
                if speech_detected:
                    silence_chunks += 1
                    if silence_chunks >= silence_limit_chunks:
                        logger.info(
                            "Silence threshold reached after %.1f s — stopping.",
                            silence_threshold_s,
                        )
                        _stop_event.set()

    if not all_chunks:
        return np.zeros(0, dtype=np.float32)

    audio = np.concatenate(all_chunks, axis=0)
    logger.info("Recorded %.2f seconds of audio.", len(audio) / SAMPLE_RATE)
    return audio
