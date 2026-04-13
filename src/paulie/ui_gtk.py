"""
ui_gtk.py — GTK3 + wlr-layer-shell overlay backend.

Uses gtk-layer-shell to place the overlay on the Wayland compositor's OVERLAY
layer so it never appears in alt-tab and never steals keyboard focus.

Supported compositors: KDE Plasma (KWin 5.27+), sway, Hyprland, and any
compositor that implements the wlr-layer-shell protocol.
NOT supported: GNOME Shell (use ui_backend = "qt" or "auto" on GNOME).

System packages required (Bazzite / Fedora Atomic):
    rpm-ostree install gtk-layer-shell python3-gobject python3-cairo
    # reboot, then:
    pipx reinstall paulie --system-site-packages

Optional (tray icon):
    rpm-ostree install libayatana-appindicator-gtk3  # then reboot + reinstall

The public API matches QtOverlayBackend — all methods are thread-safe.
"""

from __future__ import annotations

import logging
import math
import os
import random
import sys
import time
from typing import Callable

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('GtkLayerShell', '0.1')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
import cairo
from gi.repository import GLib, Gtk, GtkLayerShell, Pango, PangoCairo

try:
    gi.require_version('AppIndicator3', '0.1')
    from gi.repository import AppIndicator3 as _AppIndicator3
    _HAS_APPINDICATOR = True
except (ImportError, ValueError):
    _HAS_APPINDICATOR = False

logger = logging.getLogger(__name__)

# ── Design tokens (kept in sync with ui.py) ───────────────────────────────────
_W             = 200
_H             = 42
_BOTTOM_MARGIN = 16
_RADIUS        = 14
_LEFT_MARGIN   = 14
_BAR_COUNT     = 5
_BAR_W         = 3
_BAR_GAP       = 2
_BAR_AREA_W    = _BAR_COUNT * _BAR_W + (_BAR_COUNT - 1) * _BAR_GAP   # 23 px
_LABEL_GAP     = 8
_LABEL_X       = _LEFT_MARGIN + _BAR_AREA_W + _LABEL_GAP

# RGBA tuples (0.0–1.0) matching the Qt palette
_BG            = (18/255,  18/255,  18/255,  210/255)
_TEAL          = (0/255,   212/255, 170/255, 1.0)
_WHITE         = (1.0,     1.0,     1.0,     1.0)
_AMBER         = (1.0,     179/255, 0/255,   1.0)
_GREY          = (0.33,    0.33,    0.33,    1.0)
_TRAY_IDLE     = _GREY


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rounded_rect(cr: cairo.Context, x: float, y: float,
                  w: float, h: float, r: float) -> None:
    """Append a rounded-rectangle path to *cr*."""
    cr.new_path()
    cr.arc(x + r,     y + r,     r, math.pi,           3 * math.pi / 2)
    cr.arc(x + w - r, y + r,     r, 3 * math.pi / 2,   0)
    cr.arc(x + w - r, y + h - r, r, 0,                 math.pi / 2)
    cr.arc(x + r,     y + h - r, r, math.pi / 2,       math.pi)
    cr.close_path()


# ── GTK overlay backend ───────────────────────────────────────────────────────

class GtkOverlayBackend:
    """
    GTK3 + wlr-layer-shell overlay.

    Public API (all methods are thread-safe via GLib.idle_add):
        set_listening()
        set_recording()
        set_processing()
        set_last_text(text)
        hide()
        quit()
        on_filler_toggle(callback)
        run()
    """

    def __init__(self) -> None:
        self._label_text   = ""
        self._label_color  = _WHITE
        self._anim_mode    = "idle"
        self._anim_color   = _GREY
        self._bar_heights  = [0.12] * _BAR_COUNT
        self._bar_targets  = [0.12] * _BAR_COUNT
        self._timer_id: int | None = None
        self._filler_cb: Callable[[bool], None] | None = None

        self._win = Gtk.Window()
        self._win.set_default_size(_W, _H)
        self._win.set_resizable(False)
        self._win.set_decorated(False)
        self._win.set_app_paintable(True)
        self._win.connect("delete-event", lambda *_: True)  # never close on X

        # RGBA visual — required for transparent background on composited desktops
        screen = self._win.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self._win.set_visual(visual)
        else:
            logger.warning("RGBA visual not available — overlay background may be opaque.")

        # layer-shell: OVERLAY layer, pinned to bottom edge, no keyboard interaction
        GtkLayerShell.init_for_window(self._win)
        GtkLayerShell.set_layer(self._win, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self._win, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_margin(self._win, GtkLayerShell.Edge.BOTTOM, _BOTTOM_MARGIN)
        GtkLayerShell.set_keyboard_mode(self._win, GtkLayerShell.KeyboardMode.NONE)
        # No left/right anchor → compositor centres the window horizontally

        canvas = Gtk.DrawingArea()
        canvas.connect("draw", self._on_draw)
        self._win.add(canvas)

        self._tray: object | None = None
        self._tray_status_item: Gtk.MenuItem | None = None
        self._tray_last_item:   Gtk.MenuItem | None = None
        self._build_tray()

    # ── Window drawing ────────────────────────────────────────────────────────

    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()

        # Clear to fully transparent so the compositor sees through the window
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Pill background
        cr.set_source_rgba(*_BG)
        _rounded_rect(cr, 0, 0, w, h, _RADIUS)
        cr.fill()

        # Animated equaliser bars
        self._draw_bars(cr, h)

        # Status label
        if self._label_text:
            self._draw_label(cr, h)

        return False

    def _draw_bars(self, cr: cairo.Context, h: int) -> None:
        bar_max_h = h - 12   # 6 px padding top + bottom
        cr.set_source_rgba(*self._anim_color)
        for i in range(_BAR_COUNT):
            frac  = max(0.12, self._bar_heights[i])
            bar_h = max(3, int(frac * bar_max_h))
            x     = _LEFT_MARGIN + i * (_BAR_W + _BAR_GAP)
            y     = (h - bar_h) // 2
            _rounded_rect(cr, x, y, _BAR_W, bar_h, 2)
            cr.fill()

    def _draw_label(self, cr: cairo.Context, h: int) -> None:
        cr.set_source_rgba(*self._label_color)
        layout = PangoCairo.create_layout(cr)
        layout.set_text(self._label_text, -1)
        desc = Pango.FontDescription()
        desc.set_family("Inter, Segoe UI, Sans")
        desc.set_size(11 * Pango.SCALE)
        desc.set_weight(Pango.Weight.MEDIUM)
        layout.set_font_description(desc)
        _ink, logical = layout.get_pixel_extents()
        y = (h - logical.height) // 2
        cr.move_to(_LABEL_X, y)
        PangoCairo.show_layout(cr, layout)

    # ── Tray icon ─────────────────────────────────────────────────────────────

    def _build_tray(self) -> None:
        if not _HAS_APPINDICATOR:
            logger.info("AppIndicator3 not available — tray icon disabled.")
            return

        self._tray = _AppIndicator3.Indicator.new(
            "paulie-stt",
            "audio-input-microphone-symbolic",
            _AppIndicator3.IndicatorCategory.APPLICATION_STATUS,
        )
        self._tray.set_status(_AppIndicator3.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        self._tray_status_item = Gtk.MenuItem(label="Idle")
        self._tray_status_item.set_sensitive(False)
        menu.append(self._tray_status_item)

        menu.append(Gtk.SeparatorMenuItem())

        self._tray_last_item = Gtk.MenuItem(label="No transcription yet")
        self._tray_last_item.set_sensitive(False)
        menu.append(self._tray_last_item)

        menu.append(Gtk.SeparatorMenuItem())

        filler_item = Gtk.CheckMenuItem(label="Remove filler words")
        initial = os.environ.get("PAULIE_FILLER_WORDS", "false").lower() == "true"
        filler_item.set_active(initial)
        filler_item.connect("toggled", self._on_filler_toggled)
        menu.append(filler_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="Quit Paulie")
        quit_item.connect("activate", lambda *_: self.quit())
        menu.append(quit_item)

        menu.show_all()
        self._tray.set_menu(menu)

    def _tray_set_status(self, text: str) -> None:
        if self._tray_status_item is not None:
            self._tray_status_item.set_label(text)

    def _on_filler_toggled(self, item: Gtk.CheckMenuItem) -> None:
        if self._filler_cb is not None:
            self._filler_cb(item.get_active())

    # ── Animation timer ───────────────────────────────────────────────────────

    def _start_timer(self) -> None:
        if self._timer_id is None:
            self._timer_id = GLib.timeout_add(50, self._tick)

    def _stop_timer(self) -> None:
        if self._timer_id is not None:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _tick(self) -> bool:
        if self._anim_mode == "listen":
            for i in range(_BAR_COUNT):
                self._bar_heights[i] += (self._bar_targets[i] - self._bar_heights[i]) * 0.3
                if random.random() < 0.3:
                    self._bar_targets[i] = random.uniform(0.2, 1.0)
        elif self._anim_mode == "process":
            t = time.monotonic()
            for i in range(_BAR_COUNT):
                self._bar_heights[i] = 0.35 + 0.45 * math.sin(t * 4.0 + i * 1.2)
        self._win.queue_draw()
        return True   # keep timer running

    # ── Public API (thread-safe) ──────────────────────────────────────────────

    def set_listening(self) -> None:
        GLib.idle_add(self._on_listening)

    def set_recording(self) -> None:
        GLib.idle_add(self._on_recording)

    def set_processing(self) -> None:
        GLib.idle_add(self._on_processing)

    def set_last_text(self, text: str) -> None:
        GLib.idle_add(self._on_last_text, text)

    def hide(self) -> None:
        GLib.idle_add(self._on_hide)

    def quit(self) -> None:
        GLib.idle_add(Gtk.main_quit)

    def on_filler_toggle(self, cb: Callable[[bool], None]) -> None:
        self._filler_cb = cb

    def run(self) -> None:
        # Realise the window so layer-shell can set it up, then hide until
        # the first set_listening() call shows it.
        self._win.show_all()
        self._win.hide()
        Gtk.main()
        sys.exit(0)

    # ── Slot handlers (GTK main thread only) ──────────────────────────────────

    def _on_listening(self) -> None:
        self._label_text  = "Listening\u2026"
        self._label_color = _WHITE
        self._anim_mode   = "listen"
        self._anim_color  = _TEAL
        self._win.show_all()
        self._start_timer()
        self._tray_set_status("Listening\u2026")

    def _on_recording(self) -> None:
        self._label_text  = "Recording\u2026"
        self._label_color = _WHITE
        self._anim_color  = _WHITE
        self._tray_set_status("Recording\u2026")

    def _on_processing(self) -> None:
        self._label_text  = "Processing\u2026"
        self._label_color = _AMBER
        self._anim_mode   = "process"
        self._anim_color  = _AMBER
        self._tray_set_status("Processing\u2026")

    def _on_last_text(self, text: str) -> None:
        if not text or self._tray_last_item is None:
            return
        display = text if len(text) <= 60 else text[:57] + "\u2026"
        self._tray_last_item.set_label(f"Last: {display}")

    def _on_hide(self) -> None:
        self._stop_timer()
        self._anim_mode   = "idle"
        self._bar_heights = [0.12] * _BAR_COUNT
        self._win.hide()
        self._tray_set_status("Idle")
