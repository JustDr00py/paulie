"""
ui.py — PyQt6 borderless overlay and system tray icon.

The overlay window is:
  • Always on top
  • Borderless and transparent background
  • Centred horizontally, pinned to the bottom of the primary screen
  • Shows animated equalizer bars + label while audio capture is active
  • Transitions between Listening / Recording / Processing states

A system tray icon mirrors the overlay state with a coloured dot and exposes
the last transcription and a Quit action via its context menu.

External code drives both via Qt signals so that cross-thread updates are safe:

    overlay.set_listening_signal.emit()    # waiting for speech
    overlay.set_recording_signal.emit()    # speech confirmed
    overlay.set_processing_signal.emit()   # inference running
    overlay.set_last_text_signal.emit(s)   # update tray with last transcription
    overlay.hide_signal.emit()             # hide overlay, tray returns to idle
    overlay.quit_signal.emit()             # quit the application
"""

from __future__ import annotations

import logging
import math
import random
import time

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

# ── Design tokens ─────────────────────────────────────────────────────────────
_WINDOW_WIDTH  = 260
_WINDOW_HEIGHT = 56
_BOTTOM_MARGIN = 16
_BORDER_RADIUS = 18
_BG_COLOR      = QColor(18, 18, 18, 210)
_TEXT_COLOR    = "#FFFFFF"
_ACCENT_COLOR  = "#00D4AA"   # teal  — listening (waiting for speech)
_WHITE_COLOR   = "#FFFFFF"   # white — recording (speech confirmed)
_AMBER_COLOR   = "#FFB300"   # amber — processing (inference running)
_FONT_FAMILY   = "Inter, Segoe UI, sans-serif"
_FONT_SIZE_PT  = 13


# ── Sound-wave widget ─────────────────────────────────────────────────────────

class _SoundWave(QWidget):
    """
    Animated equalizer bars.

    Listening  → random bar heights, teal, fast (~50 ms tick).
    Processing → slow sine ripple, amber.
    Idle       → all bars at minimum height, no timer.
    """

    _BAR_COUNT = 5
    _BAR_W     = 4
    _BAR_GAP   = 3
    _MIN_FRAC  = 0.12   # minimum bar height as fraction of widget height

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        total_w = self._BAR_COUNT * self._BAR_W + (self._BAR_COUNT - 1) * self._BAR_GAP
        self.setFixedSize(total_w, 22)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._color   = QColor("#888888")
        self._mode    = "idle"
        self._heights = [self._MIN_FRAC] * self._BAR_COUNT
        self._targets = [self._MIN_FRAC] * self._BAR_COUNT

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_listening(self) -> None:
        self._mode  = "listen"
        self._color = QColor(_ACCENT_COLOR)
        self._timer.start(50)

    def set_recording(self) -> None:
        self._mode  = "listen"   # same animation, different colour
        self._color = QColor(_WHITE_COLOR)
        # timer is already running from set_listening()

    def set_processing(self) -> None:
        self._mode  = "process"
        self._color = QColor(_AMBER_COLOR)
        # timer keeps running — just changes behaviour in _tick

    def set_idle(self) -> None:
        self._mode = "idle"
        self._timer.stop()
        self._heights = [self._MIN_FRAC] * self._BAR_COUNT
        self.update()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _tick(self) -> None:
        if self._mode == "listen":
            for i in range(self._BAR_COUNT):
                self._heights[i] += (self._targets[i] - self._heights[i]) * 0.3
                if random.random() < 0.3:
                    self._targets[i] = random.uniform(0.2, 1.0)
        elif self._mode == "process":
            t = time.monotonic()
            for i in range(self._BAR_COUNT):
                self._heights[i] = 0.35 + 0.45 * math.sin(t * 4.0 + i * 1.2)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        h = self.height()
        for i in range(self._BAR_COUNT):
            frac  = max(self._MIN_FRAC, self._heights[i])
            bar_h = max(3, int(frac * h))
            x     = i * (self._BAR_W + self._BAR_GAP)
            y     = (h - bar_h) // 2
            path  = QPainterPath()
            path.addRoundedRect(x, y, self._BAR_W, bar_h, 2, 2)
            painter.fillPath(path, self._color)


# ── Tray icon helper ──────────────────────────────────────────────────────────

_TRAY_IDLE_COLOR = "#555555"

def _make_tray_icon(color: str) -> QIcon:
    """Return a 22×22 QIcon containing a filled circle in *color*."""
    size = 22
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    margin = 3
    painter.drawEllipse(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.end()
    return QIcon(pixmap)


# ── Overlay window ────────────────────────────────────────────────────────────

class OverlayWindow(QWidget):
    """
    Frameless, always-on-top status overlay.

    Signals
    -------
    set_listening_signal  — show listening state
    set_processing_signal — show processing state
    hide_signal           — hide the overlay
    quit_signal           — quit the application
    """

    set_listening_signal  = pyqtSignal()
    set_recording_signal  = pyqtSignal()
    set_processing_signal = pyqtSignal()
    set_last_text_signal  = pyqtSignal(str)
    hide_signal           = pyqtSignal()
    quit_signal           = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._setup_window()
        self._build_ui()
        self._build_tray()
        self._connect_signals()

    # ── Window chrome ─────────────────────────────────────────────────────────

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)

        row = QHBoxLayout()
        row.setContentsMargins(18, 0, 18, 0)
        row.setSpacing(10)
        row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._wave = _SoundWave(self)
        row.addWidget(self._wave)

        self._label = QLabel("", self)
        font = QFont()
        font.setFamily(_FONT_FAMILY)
        font.setPointSize(_FONT_SIZE_PT)
        font.setWeight(QFont.Weight.Medium)
        self._label.setFont(font)
        self._label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        self._label.setStyleSheet(f"color: {_TEXT_COLOR}; background: transparent;")
        row.addWidget(self._label)

        outer.addLayout(row)

    # ── Tray icon ─────────────────────────────────────────────────────────────

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available — tray icon disabled.")
            self._tray: QSystemTrayIcon | None = None
            return

        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(_make_tray_icon(_TRAY_IDLE_COLOR))
        self._tray.setToolTip("Paulie — idle")

        menu = QMenu()
        self._tray_status_action = menu.addAction("Idle")
        self._tray_status_action.setEnabled(False)
        menu.addSeparator()
        self._tray_last_action = menu.addAction("No transcription yet")
        self._tray_last_action.setEnabled(False)
        menu.addSeparator()
        quit_action = menu.addAction("Quit Paulie")
        quit_action.triggered.connect(self.quit_signal.emit)

        self._tray.setContextMenu(menu)
        self._tray.show()

    def _tray_set(self, color: str, status: str) -> None:
        if self._tray is None:
            return
        self._tray.setIcon(_make_tray_icon(color))
        self._tray.setToolTip(f"Paulie — {status}")
        self._tray_status_action.setText(status.capitalize())

    # ── Signals ───────────────────────────────────────────────────────────────

    def _connect_signals(self) -> None:
        self.set_listening_signal.connect(self._on_listening)
        self.set_recording_signal.connect(self._on_recording)
        self.set_processing_signal.connect(self._on_processing)
        self.set_last_text_signal.connect(self._on_last_text)
        self.hide_signal.connect(self._on_hide)
        self.quit_signal.connect(self._on_quit)

    # ── Slot handlers ─────────────────────────────────────────────────────────

    def _on_listening(self) -> None:
        self._label.setText("Listening…")
        self._label.setStyleSheet(f"color: {_TEXT_COLOR}; background: transparent;")
        self._wave.set_listening()
        self.setWindowOpacity(1.0)
        self.show()
        # Defer reposition so the compositor maps the window before we move it.
        QTimer.singleShot(0, self._reposition)
        self._tray_set(_ACCENT_COLOR, "listening…")

    def _on_recording(self) -> None:
        self._label.setText("Recording…")
        self._label.setStyleSheet(f"color: {_WHITE_COLOR}; background: transparent;")
        self._wave.set_recording()
        self._tray_set(_WHITE_COLOR, "recording…")

    def _on_processing(self) -> None:
        self._label.setText("Processing…")
        self._label.setStyleSheet(f"color: {_AMBER_COLOR}; background: transparent;")
        self._wave.set_processing()
        self._tray_set(_AMBER_COLOR, "processing…")

    def _on_last_text(self, text: str) -> None:
        if not text or self._tray is None:
            return
        display = text if len(text) <= 60 else text[:57] + "…"
        self._tray_last_action.setText(f"Last: {display}")
        self._tray.setToolTip(f"Paulie — {display}")

    def _on_hide(self) -> None:
        self._wave.set_idle()
        self.hide()
        self._tray_set(_TRAY_IDLE_COLOR, "idle")

    def _on_quit(self) -> None:
        self._wave.set_idle()
        if self._tray is not None:
            self._tray.hide()
        QApplication.instance().quit()  # type: ignore[union-attr]

    # ── Custom painting ───────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), _BORDER_RADIUS, _BORDER_RADIUS)
        painter.fillPath(path, _BG_COLOR)
