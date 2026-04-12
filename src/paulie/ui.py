"""
ui.py — PyQt6 borderless overlay shown while the user is speaking.

The window is:
  • Always on top
  • Borderless and transparent background
  • Centred horizontally, pinned to the bottom of the primary screen
  • Shows a pulsing "Listening…" label while audio capture is active
  • Transitions to "Processing…" once VAD triggers end-of-speech

External code drives the overlay via Qt signals so that cross-thread updates
are safe:

    overlay.set_processing_signal.emit()   # switch label to "Processing…"
    overlay.quit_signal.emit()             # close the overlay & quit the app
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

# ── Design tokens ─────────────────────────────────────────────────────────────
_WINDOW_WIDTH = 320
_WINDOW_HEIGHT = 64
_BOTTOM_MARGIN = 48          # pixels from the bottom of the screen
_BORDER_RADIUS = 18          # px — pill shape
_BG_COLOR = QColor(18, 18, 18, 210)   # near-black, slightly transparent
_TEXT_COLOR = "#FFFFFF"
_ACCENT_COLOR = "#00D4AA"    # teal — signals activity
_FONT_FAMILY = "Inter, Segoe UI, sans-serif"
_FONT_SIZE_PT = 15
_PULSE_INTERVAL_MS = 800     # ms between opacity pulses


class OverlayWindow(QWidget):
    """
    Frameless, always-on-top status overlay.

    Signals
    -------
    set_processing_signal:
        Emitted by the worker thread when speech ends and STT starts.
    quit_signal:
        Emitted by the worker thread after text is injected (or on error).
    """

    set_listening_signal = pyqtSignal()
    set_processing_signal = pyqtSignal()
    hide_signal = pyqtSignal()
    quit_signal = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._dot_count = 0
        self._base_text = "Loading"
        self._setup_window()
        self._build_ui()
        self._connect_signals()
        self._start_pulse()

    # ── Window chrome ──────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool              # keeps it off the taskbar
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)  # show without activating
        self.setFixedSize(_WINDOW_WIDTH, _WINDOW_HEIGHT)
        self._reposition()

    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        x = geo.left() + (geo.width() - _WINDOW_WIDTH) // 2
        y = geo.bottom() - _WINDOW_HEIGHT - _BOTTOM_MARGIN
        self.move(x, y)

    # ── Widget tree ───────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._label = QLabel("Loading…", self)
        font = QFont()
        font.setFamily(_FONT_FAMILY)
        font.setPointSize(_FONT_SIZE_PT)
        font.setWeight(QFont.Weight.Medium)
        self._label.setFont(font)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(f"color: {_TEXT_COLOR}; background: transparent;")

        # Teal dot indicator
        self._dot = QLabel("●", self)
        dot_font = QFont()
        dot_font.setPointSize(10)
        self._dot.setFont(dot_font)
        self._dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._dot.setStyleSheet("color: #888888; background: transparent;")

        layout.addWidget(self._dot)
        layout.addWidget(self._label)

    # ── Signals ───────────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self.set_listening_signal.connect(self._on_listening)
        self.set_processing_signal.connect(self._on_processing)
        self.hide_signal.connect(self._on_hide)
        self.quit_signal.connect(self._on_quit)

    # ── Animation ─────────────────────────────────────────────────────────────

    def _start_pulse(self) -> None:
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_tick)
        self._pulse_timer.start(_PULSE_INTERVAL_MS)

    def _pulse_tick(self) -> None:
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count
        self._label.setText(f"{self._base_text}{dots}")

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_listening(self) -> None:
        self._dot_count = 0
        self._base_text = "Listening"
        self._label.setText("Listening…")
        self._dot.setStyleSheet(f"color: {_ACCENT_COLOR}; background: transparent;")
        self.setWindowOpacity(1.0)
        self.show()  # ToolTip (xdg_popup) cannot steal keyboard focus by protocol
        if not self._pulse_timer.isActive():
            self._pulse_timer.start(_PULSE_INTERVAL_MS)

    def _on_processing(self) -> None:
        self._pulse_timer.stop()
        self._base_text = "Processing"
        self._label.setText("Processing…")
        self._dot.setStyleSheet("color: #FFB300; background: transparent;")  # amber

    def _on_hide(self) -> None:
        self._pulse_timer.stop()
        self.hide()  # True unmap — tells compositor to return focus to previous window

    def _on_quit(self) -> None:
        self._pulse_timer.stop()
        QApplication.instance().quit()  # type: ignore[union-attr]

    # ── Custom painting ───────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(
            0, 0, self.width(), self.height(),
            _BORDER_RADIUS, _BORDER_RADIUS,
        )
        painter.fillPath(path, _BG_COLOR)
