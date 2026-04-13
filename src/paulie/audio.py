"""
audio.py — Microphone capture with Silero-VAD silence detection.

Two recording modes are provided:

``record_until_silence`` (single mode)
    Records one continuous clip.  Stops when silence exceeds
    PAULIE_SILENCE_S, the hard ceiling is reached, or an abort is requested.
    Returns a numpy float32 array ready for transcription.

``record_utterances`` (utterance mode)
    Keeps the microphone open across multiple sentences.  Calls
    ``on_utterance(audio)`` at each natural pause (PAULIE_UTTERANCE_PAUSE_S),
    allowing the caller to transcribe and inject each sentence as it finishes.
    The session ends when silence exceeds PAULIE_SILENCE_S (recommend ≥ 2 s
    in utterance mode) or an abort is requested.

Safety ceilings common to both modes:
  - MAX_PRE_SPEECH_S: give up if no speech starts within this window.
  - PAULIE_MAX_RECORD_S: hard cap on total recording time.
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
# PAULIE_SILENCE_S, PAULIE_VAD_THRESHOLD, and PAULIE_MAX_RECORD_S are read
# inside record_until_silence() rather than at module level so that
# config.apply_config() can set them before the first recording begins.

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
    max_record_s: float = float(os.environ.get("PAULIE_MAX_RECORD_S", "120.0"))

    vad_model = load_vad_model()

    # silero-VAD tracks hidden state internally; reset before a new utterance.
    if hasattr(vad_model, "reset_states"):
        vad_model.reset_states()

    silence_limit_chunks = int(silence_threshold_s * SAMPLE_RATE / CHUNK_SAMPLES)
    max_chunks = int(max_record_s * SAMPLE_RATE / CHUNK_SAMPLES)

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
                    max_record_s,
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


def record_utterances(
    on_utterance: Callable[[np.ndarray], None],
    on_speech_start: Callable[[], None] | None = None,
    abort_event: threading.Event | None = None,
) -> None:
    """
    Keep the microphone open and call ``on_utterance(audio)`` at each natural
    sentence pause, continuing until a longer session-end silence or abort.

    Parameters
    ----------
    on_utterance:
        Called with a float32 16 kHz numpy array for each detected utterance.
        Invoked from the recording thread — the implementation should be
        non-blocking (e.g. submit work to a thread pool and return immediately).

    on_speech_start:
        Called once when speech is first detected in the session.  Same
        threading caveat as ``record_until_silence``.

    abort_event:
        When set, recording stops immediately and any buffered audio is
        discarded.

    Environment variables
    ---------------------
    PAULIE_UTTERANCE_PAUSE_S  Silence that ends one utterance (default 0.5 s).
    PAULIE_SILENCE_S          Silence that ends the whole session (default 2.0 s
                              in utterance mode — increase if you pause to think).
    PAULIE_VAD_THRESHOLD      Speech probability cutoff (default 0.45).
    PAULIE_MAX_RECORD_S       Hard ceiling on total session duration (default 120 s).
    """
    utterance_pause_s: float = float(os.environ.get("PAULIE_UTTERANCE_PAUSE_S", "0.5"))
    # Use a longer default for session-end in utterance mode so a natural
    # thinking pause doesn't close the session prematurely.
    session_end_s: float = float(os.environ.get("PAULIE_SILENCE_S", "2.0"))
    vad_threshold: float = float(os.environ.get("PAULIE_VAD_THRESHOLD", "0.45"))
    max_record_s: float = float(os.environ.get("PAULIE_MAX_RECORD_S", "120.0"))

    # Guard: session-end must be longer than the utterance pause or the session
    # would end the moment an utterance finishes.
    if session_end_s <= utterance_pause_s:
        session_end_s = utterance_pause_s + 1.0
        logger.warning(
            "PAULIE_SILENCE_S (%.1f s) ≤ PAULIE_UTTERANCE_PAUSE_S (%.1f s) — "
            "using %.1f s as session-end silence.",
            session_end_s - 1.0, utterance_pause_s, session_end_s,
        )

    vad_model = load_vad_model()
    # Reset only at session start — NOT between utterances.  Keeping the model
    # warm avoids the cold-start clipping that would otherwise drop the first
    # syllable of each new sentence.
    if hasattr(vad_model, "reset_states"):
        vad_model.reset_states()

    utterance_pause_chunks: int = int(utterance_pause_s * SAMPLE_RATE / CHUNK_SAMPLES)
    session_end_chunks: int    = int(session_end_s    * SAMPLE_RATE / CHUNK_SAMPLES)
    max_chunks: int            = int(max_record_s     * SAMPLE_RATE / CHUNK_SAMPLES)
    # Minimum confirmed speech chunks before a flush is sent for transcription.
    # Clips shorter than ~320 ms are almost certainly noise or a stray syllable.
    min_speech_chunks: int     = int(0.32 * SAMPLE_RATE / CHUNK_SAMPLES)
    # Consecutive above-threshold frames required before declaring speech.
    # Prevents single-chunk noise spikes from opening a new utterance.
    _SPEECH_ENTRY_REQUIRED: int = 2
    # Grace period (chunks) after a flush before session-end silence is counted.
    # Gives the VAD model a moment to stabilise for the next sentence.
    _POST_FLUSH_GRACE: int      = int(0.15 * SAMPLE_RATE / CHUNK_SAMPLES)

    utterance_buf: list[np.ndarray] = []
    silence_chunks: int   = 0
    speech_in_utt: bool   = False   # speech confirmed in the current utterance
    session_started: bool = False   # at least one utterance has been heard
    speech_cb_fired: bool = False
    total_chunks: int     = 0
    speech_entry_buf: int = 0       # consecutive above-threshold frames (hysteresis)
    speech_chunks_in_utt: int = 0  # confirmed speech frames in current utterance
    post_flush_grace: int = 0       # counts down after each utterance flush
    start_time: float     = time.monotonic()

    _queue: deque[np.ndarray] = deque()

    def _audio_callback(indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.warning("sounddevice status: %s", status)
        _queue.append(indata[:, 0].copy())

    _device_env = os.environ.get("PAULIE_DEVICE", "")
    _device: str | int | None = None
    if _device_env:
        _device = int(_device_env) if _device_env.isdigit() else _device_env

    logger.info("Utterance mode: opening microphone (device=%r).", _device)
    logger.info(
        "Utterance pause: %.1f s  |  Session end: %.1f s  |  Max: %.0f s",
        utterance_pause_s, session_end_s, max_record_s,
    )

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        blocksize=CHUNK_SAMPLES,
        callback=_audio_callback,
        device=_device,
    ):
        while True:
            # ── Abort / ceiling checks ─────────────────────────────────────
            if abort_event is not None and abort_event.is_set():
                logger.info("Utterance recording cancelled.")
                return

            if total_chunks >= max_chunks:
                logger.warning("Maximum recording duration reached — ending session.")
                break

            if not session_started:
                if time.monotonic() - start_time > MAX_PRE_SPEECH_S:
                    logger.warning("No speech detected within %.1f s — ending session.", MAX_PRE_SPEECH_S)
                    return

            # ── Consume one chunk ──────────────────────────────────────────
            if not _queue:
                sd.sleep(5)
                continue

            chunk = _queue.popleft()
            utterance_buf.append(chunk)
            total_chunks += 1

            tensor = torch.from_numpy(chunk)
            with torch.no_grad():
                speech_prob: float = vad_model(tensor, SAMPLE_RATE).item()

            # ── VAD decision ───────────────────────────────────────────────
            if speech_prob >= vad_threshold:
                speech_entry_buf = min(speech_entry_buf + 1, _SPEECH_ENTRY_REQUIRED)
                silence_chunks   = 0
                post_flush_grace = 0

                # Only declare speech after N consecutive above-threshold frames
                # so that brief noise spikes don't open a false utterance.
                if speech_entry_buf >= _SPEECH_ENTRY_REQUIRED:
                    speech_chunks_in_utt += 1
                    if not speech_in_utt:
                        speech_in_utt   = True
                        session_started = True
                        logger.info("Speech detected.")
                        if on_speech_start and not speech_cb_fired:
                            speech_cb_fired = True
                            on_speech_start()
            else:
                speech_entry_buf = 0

                if speech_in_utt:
                    silence_chunks += 1
                    # ── Utterance boundary ─────────────────────────────────
                    if silence_chunks >= utterance_pause_chunks:
                        audio = np.concatenate(utterance_buf)
                        if speech_chunks_in_utt >= min_speech_chunks:
                            logger.info(
                                "Utterance boundary — flushing %.2f s of audio.",
                                len(audio) / SAMPLE_RATE,
                            )
                            on_utterance(audio)
                        else:
                            logger.debug(
                                "Utterance too short (%d speech chunks) — discarding.",
                                speech_chunks_in_utt,
                            )
                        # Reset utterance state; VAD model stays warm.
                        utterance_buf        = []
                        silence_chunks       = 0
                        speech_in_utt        = False
                        speech_chunks_in_utt = 0
                        post_flush_grace     = _POST_FLUSH_GRACE
                else:
                    # ── Session-end silence (no speech in current window) ──
                    if post_flush_grace > 0:
                        post_flush_grace -= 1
                    else:
                        silence_chunks += 1
                        if session_started and silence_chunks >= session_end_chunks:
                            logger.info(
                                "Session-end silence (%.1f s) reached — ending.", session_end_s
                            )
                            break

    # Flush any trailing speech that didn't hit the utterance-pause threshold
    # (e.g. user was still speaking when max_record_s fired).
    if utterance_buf and speech_in_utt and speech_chunks_in_utt >= min_speech_chunks:
        audio = np.concatenate(utterance_buf)
        if len(audio) / SAMPLE_RATE >= 0.1:
            logger.info("Flushing trailing %.2f s of audio.", len(audio) / SAMPLE_RATE)
            on_utterance(audio)
