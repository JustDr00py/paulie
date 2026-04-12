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

import logging
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from .audio import load_vad_model, record_until_silence
from .inject import inject_text, restore_focus, save_focus
from .stt import load_model, transcribe
from .ui import OverlayWindow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

SOCKET_PATH = f"/tmp/paulie-{os.getuid()}.sock"


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
        self._sock.bind(SOCKET_PATH)
        self._sock.listen(1)
        logger.info("Listening for triggers on %s", SOCKET_PATH)
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
                conn.close()
                self.triggered.emit()
            except Exception:
                logger.exception("Socket accept error.")
                break


class _Daemon(QObject):
    def __init__(self, overlay: OverlayWindow, model) -> None:
        super().__init__()
        self._overlay = overlay
        self._model = model
        self._busy = False

        self._source = _TriggerSource(self)
        self._source.triggered.connect(self._on_trigger)

    def _on_trigger(self) -> None:
        if self._busy:
            logger.info("Trigger ignored — pipeline already running.")
            return
        self._busy = True
        # Capture focus BEFORE the overlay becomes visible so we know
        # which window to return to after injection.
        focused = save_focus()
        self._overlay.set_listening_signal.emit()
        threading.Thread(target=self._pipeline, args=(focused,), daemon=True).start()

    def _pipeline(self, focused: str | None) -> None:
        try:
            audio = record_until_silence()
            self._overlay.set_processing_signal.emit()
            text = transcribe(self._model, audio)
            logger.info("Transcription: %r", text)
            # Hide overlay first so it cannot re-steal focus, then restore
            # the original window and give the compositor a moment to settle
            # before ydotool fires.
            # hide() unmaps the Wayland surface so the compositor returns focus
            # to the previous window. restore_focus() is a belt-and-suspenders
            # backup in case the compositor doesn't do it automatically.
            self._overlay.hide_signal.emit()
            restore_focus(focused)
            time.sleep(0.3)   # wait for hide + focus handback to settle
            inject_text(text)
        except Exception:
            logger.exception("Unhandled exception in pipeline.")
        finally:
            self._busy = False


def main() -> None:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = OverlayWindow()

    logger.info("Loading models…")
    with ThreadPoolExecutor(max_workers=2) as executor:
        stt_future = executor.submit(load_model)
        executor.submit(load_vad_model)
    model = stt_future.result()
    logger.info("Models ready. Paulie daemon running.")

    daemon = _Daemon(overlay, model)  # noqa: F841 — must stay alive for the event loop

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
