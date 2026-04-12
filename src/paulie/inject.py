"""
inject.py — Text injection via ydotool, with focus save/restore.
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def save_focus() -> str | None:
    """
    Return an opaque token representing the currently focused window.
    Tries xdotool first (XWayland apps), then KWin DBus (native Wayland apps).
    Returns None if neither is available.
    """
    # xdotool — works for any XWayland window
    try:
        r = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=1,
        )
        if r.returncode == 0 and r.stdout.strip():
            wid = r.stdout.strip()
            # Also grab the window name for diagnostics
            name_r = subprocess.run(
                ["xdotool", "getwindowname", wid],
                capture_output=True, text=True, timeout=1,
            )
            name = name_r.stdout.strip() if name_r.returncode == 0 else "?"
            logger.info("save_focus: xdotool captured window %s (%s)", wid, name)
            return f"xdotool:{wid}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("save_focus: xdotool not available")

    # KWin DBus — works for native Wayland windows on KDE
    try:
        r = subprocess.run(
            ["qdbus", "org.kde.KWin", "/KWin", "activeClient"],
            capture_output=True, text=True, timeout=1,
        )
        if r.returncode == 0 and r.stdout.strip():
            wid = r.stdout.strip()
            logger.info("save_focus: kwin captured client %s", wid)
            return f"kwin:{wid}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("save_focus: qdbus not available")

    logger.warning("save_focus: no focus-capture tool available (install xdotool).")
    return None


def restore_focus(token: str | None) -> None:
    """Restore keyboard focus to the window captured by save_focus()."""
    if not token:
        logger.warning("restore_focus: no saved window — skipping")
        return
    method, _, window_id = token.partition(":")
    logger.info("restore_focus: restoring via %s to %s", method, window_id)
    try:
        if method == "xdotool":
            r = subprocess.run(
                ["xdotool", "windowactivate", "--sync", window_id],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode != 0:
                logger.warning("restore_focus: xdotool failed: %s", r.stderr.strip())
        elif method == "kwin":
            r = subprocess.run(
                ["qdbus", "org.kde.KWin", "/KWin", "activateWindow", window_id],
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode != 0:
                logger.warning("restore_focus: qdbus failed: %s", r.stderr.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("restore_focus failed: %s", exc)


def inject_text(text: str) -> None:
    """Inject transcribed text into the focused window via ydotool."""
    if not text:
        logger.warning("Empty transcription — nothing to inject.")
        return

    env = os.environ.copy()
    if "YDOTOOL_SOCKET" not in env:
        env["YDOTOOL_SOCKET"] = os.path.join(os.path.expanduser("~"), ".ydotool_socket")

    logger.info("Injecting %d chars.", len(text))
    try:
        subprocess.run(
            ["ydotool", "type", "--", text],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        logger.info("ydotool: text injected.")
    except FileNotFoundError:
        logger.error("ydotool not found — install it with: rpm-ostree install ydotool")
    except subprocess.TimeoutExpired:
        logger.error("ydotool timed out after 10 s.")
    except subprocess.CalledProcessError as exc:
        logger.error(
            "ydotool failed (%d): %s",
            exc.returncode,
            exc.stderr.strip() if exc.stderr else "(no stderr)",
        )
