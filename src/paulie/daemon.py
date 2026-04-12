"""
daemon.py — Paulie persistent daemon.

Loads models once at startup, then listens on a Unix socket for trigger
signals from the `paulie` client. Each trigger starts a full
record → transcribe → inject cycle.

Usage
-----
Start at login (add to KDE Autostart):
    paulie-daemon

Then bind your global hotkey to:
    paulie
"""

from __future__ import annotations

import atexit
import errno
import logging
import os
import re
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .audio import load_vad_model, record_until_silence, record_utterances
from .config import apply_config, write_default_config
from .inject import inject_text, restore_focus, save_focus
from .stt import load_model, transcribe
from .ui import OverlayWindow

logger = logging.getLogger(__name__)

# Prefer XDG_RUNTIME_DIR (/run/user/<uid>, mode 0700, managed by systemd-logind)
# so the socket lives in a directory only the owning user can access at all.
# Fall back to /tmp only when the runtime dir is absent (non-systemd environments).
SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp",
    f"paulie-{os.getuid()}.sock",
)


def _cleanup_socket() -> None:
    """Remove the socket file on exit so no stale path is left behind."""
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass


class _TriggerSource(QObject):
    """Accepts connections on a Unix socket and emits a Qt signal for each one."""

    triggered = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass
        except OSError as exc:
            # e.g. IsADirectoryError — log and continue; bind() will surface the
            # real error with a clear message rather than crashing here silently.
            logger.warning("Could not remove stale socket path %s: %s", SOCKET_PATH, exc)

        # SEC-01: set umask before bind so the socket inode is created at 0600
        # atomically — no window between creation and chmod where another process
        # could connect with permissive interim permissions.
        old_umask = os.umask(0o177)
        try:
            self._sock.bind(SOCKET_PATH)
        finally:
            os.umask(old_umask)
        # Belt-and-suspenders: explicit chmod covers filesystems that ignore umask.
        os.chmod(SOCKET_PATH, 0o600)
        self._sock.listen(1)

        # Clean up socket file on both graceful exit and SIGTERM.
        atexit.register(_cleanup_socket)
        # SEC-09: call QApplication.quit() from the signal handler instead of
        # sys.exit() — Qt's quit() is documented as safe to call from signal
        # context and avoids invoking Python's exception machinery mid-inference.
        signal.signal(signal.SIGTERM, lambda *_: QApplication.instance().quit())

        logger.info("Listening for triggers on %s", SOCKET_PATH)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
                self._handle_connection(conn)
            except OSError as exc:
                if exc.errno in (errno.EINTR, errno.EAGAIN):
                    # Transient interruption — retry immediately.
                    continue
                logger.exception("Fatal socket error — accept loop exiting.")
                break
            except Exception:
                # Log unexpected errors but keep the loop alive.
                logger.exception("Unexpected error in accept loop — retrying.")
        self._sock.close()

    def _handle_connection(self, conn: socket.socket) -> None:
        """
        Read the first byte to determine intent, then act.

        Protocol
        --------
        ``\\x01``  Trigger a dictation cycle (new explicit clients).
        ``\\x00``  Status ping — respond with a JSON line and do not trigger.
        ``b""``   Connection closed before sending data (legacy clients) — trigger.
        Any other byte is treated as a trigger for forward-compatibility.
        """
        import json
        try:
            conn.settimeout(0.1)
            try:
                byte = conn.recv(1)
            except OSError:
                byte = b""

            if byte == b"\x00":
                # Status ping — reply and do not trigger.
                payload = json.dumps({
                    "running": True,
                    "socket":  SOCKET_PATH,
                }).encode()
                try:
                    conn.sendall(payload + b"\n")
                except OSError:
                    pass
                logger.debug("Status ping answered.")
            else:
                # b"" (legacy), b"\x01" (explicit), or anything else → trigger.
                self.triggered.emit()
        finally:
            conn.close()


class _Daemon(QObject):
    def __init__(self, overlay: OverlayWindow, model: object) -> None:
        super().__init__()
        self._overlay = overlay
        self._model = model
        # threading.Event has explicit memory-ordering semantics across threads;
        # safer than a bare bool read/written from both main and worker threads.
        self._busy = threading.Event()
        # Set by a second hotkey press while a pipeline is already running.
        # Checked inside record_until_silence() to stop recording immediately.
        self._abort_event = threading.Event()

        self._source = _TriggerSource(self)
        self._source.triggered.connect(self._on_trigger)

    def _on_trigger(self) -> None:
        if self._busy.is_set():
            # Second hotkey press while a pipeline is already running — cancel it.
            logger.info("Cancel requested — aborting current pipeline.")
            self._abort_event.set()
            return
        self._busy.set()
        self._abort_event.clear()
        # Capture focus BEFORE the overlay becomes visible so we know
        # which window to return to after injection.
        focused = save_focus()
        self._overlay.set_listening_signal.emit()
        mode = os.environ.get("PAULIE_MODE", "single").strip().lower()
        target = self._pipeline_utterance if mode == "utterance" else self._pipeline
        threading.Thread(target=target, args=(focused,), daemon=True).start()

    def _pipeline(self, focused: str | None) -> None:
        try:
            audio = record_until_silence(
                on_speech_start=self._overlay.set_recording_signal.emit,
                abort_event=self._abort_event,
            )

            # Abort requested — discard audio and hide without injecting.
            if self._abort_event.is_set():
                logger.info("Pipeline cancelled — discarding audio.")
                self._overlay.hide_signal.emit()
                return

            self._overlay.set_processing_signal.emit()
            text = transcribe(self._model, audio)
            # SEC-07: log the content only at DEBUG — full transcription text is
            # privacy-sensitive and would otherwise persist in the systemd journal.
            logger.debug("Transcription: %r", text)
            logger.info("Transcription complete (%d chars).", len(text))
            # Hide overlay first so it cannot re-steal focus, then restore
            # the original window and give the compositor a moment to settle
            # before ydotool fires.
            self._overlay.hide_signal.emit()
            restore_focus(focused)
            time.sleep(0.3)  # wait for hide + focus handback to settle
            inject_text(text)
            if text:
                self._overlay.set_last_text_signal.emit(text)
        except Exception:
            logger.exception("Unhandled exception in pipeline.")
            # Ensure the overlay is always dismissed, even on error.
            self._overlay.hide_signal.emit()
        finally:
            self._busy.clear()

    def _pipeline_utterance(self, focused: str | None) -> None:
        """
        Utterance mode pipeline.

        Keeps the microphone open across multiple sentences.  Each utterance
        is transcribed and injected concurrently so audio collection continues
        without waiting for the previous transcription to finish.
        """
        focus_restored = False

        def _transcribe_and_inject(audio: "np.ndarray") -> None:  # noqa: F821
            nonlocal focus_restored
            text = transcribe(self._model, audio)
            logger.debug("Utterance transcription: %r", text)
            logger.info("Utterance: %d chars.", len(text))
            if not text:
                return
            # Restore focus before the very first injection so the target
            # window is active.  Subsequent utterances go to the same window.
            if not focus_restored:
                restore_focus(focused)
                time.sleep(0.15)
                focus_restored = True
            inject_text(text)
            self._overlay.set_last_text_signal.emit(text)

        try:
            # Single-worker pool keeps injections in order while still allowing
            # audio collection to continue during transcription.
            with ThreadPoolExecutor(max_workers=1) as pool:
                futures: list = []

                def on_utterance(audio: "np.ndarray") -> None:  # noqa: F821
                    futures.append(pool.submit(_transcribe_and_inject, audio))

                record_utterances(
                    on_utterance=on_utterance,
                    on_speech_start=self._overlay.set_recording_signal.emit,
                    abort_event=self._abort_event,
                )

                # Drain any in-flight transcriptions before hiding the overlay.
                for future in futures:
                    try:
                        future.result()
                    except Exception:
                        logger.exception("Utterance transcription/injection failed.")

        except Exception:
            logger.exception("Unhandled exception in utterance pipeline.")
        finally:
            self._overlay.hide_signal.emit()
            self._busy.clear()


def _list_devices() -> None:
    """Print available audio input devices and exit."""
    import sounddevice as sd
    devices = sd.query_devices()
    default_input = sd.default.device[0] if isinstance(sd.default.device, (list, tuple)) else sd.default.device
    print("Available input devices (* = system default):\n")
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] < 1:
            continue
        marker = "*" if i == default_input else " "
        print(f"  {marker} [{i:2d}]  {dev['name']}")
    print(
        "\nSet the device in ~/.config/paulie/paulie.conf:\n"
        '  device = "name substring"   # or an integer index\n'
        "Or via environment variable:  PAULIE_DEVICE=<name or index>"
    )


def main() -> None:
    if "--init-config" in sys.argv:
        write_default_config()
        return

    if "--list-devices" in sys.argv:
        _list_devices()
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    # Apply config file values before models read their settings from os.environ.
    apply_config()

    # Fail early with a clear message when the display environment is absent
    # (common when launched as a bare systemd user service without importing
    # the compositor's environment variables).
    if not os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
        sys.exit(
            "error: Neither WAYLAND_DISPLAY nor DISPLAY is set.\n"
            "If running via systemd, run first:\n"
            "  systemctl --user import-environment WAYLAND_DISPLAY XDG_RUNTIME_DIR"
        )

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = OverlayWindow()

    logger.info("Loading models…")
    with ThreadPoolExecutor(max_workers=2) as executor:
        stt_future = executor.submit(load_model)
        vad_future = executor.submit(load_vad_model)

    # Resolve both futures so startup exceptions from either model surface here
    # and abort the daemon with a clear traceback rather than failing silently
    # on the first recording attempt.
    model = stt_future.result()
    vad_future.result()
    logger.info("Models ready. Paulie daemon running.")

    # Store on `app` so the daemon object is owned by a stable reference for
    # the lifetime of the event loop (avoids the noqa: F841 smell).
    app._daemon = _Daemon(overlay, model)  # type: ignore[attr-defined]

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
