"""
inject.py — Text injection via ydotool, with focus save/restore.
"""

from __future__ import annotations

import logging
import os
import re
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
            if wid.isdigit():   # sanity-check before a second subprocess call
                name_r = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, text=True, timeout=1,
                )
                name = name_r.stdout.strip() if name_r.returncode == 0 else "?"
                # SEC-08: window title is behavioural metadata — keep at DEBUG so it
                # does not persist in the systemd journal at normal log levels.
                logger.debug("save_focus: xdotool captured window %s (%s)", wid, name)
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
            logger.debug("save_focus: kwin captured client %s", wid)
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

    # SEC-06: validate window_id before passing to a subprocess.  Both ids come
    # from our own save_focus(), but defensive validation ensures unexpected output
    # (empty string, null bytes, multi-line) can never reach a child process.
    if method == "xdotool":
        if not window_id.isdigit():
            logger.warning("restore_focus: unexpected xdotool window ID %r — skipping", window_id)
            return
    elif method == "kwin":
        if not re.fullmatch(r"[0-9A-Fa-f\-]+", window_id):
            logger.warning("restore_focus: unexpected KWin client ID %r — skipping", window_id)
            return

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

    # Resolve the ydotoold socket path.  ydotoold can place its socket in
    # several locations depending on how it was started:
    #   1. $YDOTOOL_SOCKET          — explicit user/admin override (always wins)
    #   2. ~/.ydotool_socket        — default when started manually or via KDE Autostart
    #   3. $XDG_RUNTIME_DIR/ydotool_socket — default for some systemd unit configs
    #   4. /tmp/.ydotool_socket     — legacy / root-daemon fallback
    # Probe in that order so we use whichever socket is actually live.
    xdg = os.environ.get("XDG_RUNTIME_DIR", "")
    _candidates = [
        os.environ.get("YDOTOOL_SOCKET", ""),
        os.path.join(os.path.expanduser("~"), ".ydotool_socket"),
        os.path.join(xdg, "ydotool_socket") if xdg else "",
        "/tmp/.ydotool_socket",
    ]
    ydotool_socket = next(
        (p for p in _candidates if p and os.path.exists(p)),
        _candidates[1],   # fall back to ~/.ydotool_socket if none are found
    )

    # SEC-04: pass a minimal, explicit environment to the subprocess rather than
    # inheriting the full daemon environment.  This prevents LD_PRELOAD, PYTHONPATH,
    # or other hostile variables from affecting the ydotool child process.
    env: dict[str, str] = {
        "PATH":            os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME":            os.environ.get("HOME", ""),
        "XDG_RUNTIME_DIR": xdg,
        "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", ""),
        "DISPLAY":         os.environ.get("DISPLAY", ""),
        "YDOTOOL_SOCKET":  ydotool_socket,
    }

    # ydotool's default --key-delay is 12 ms/char — fine for short strings but
    # it causes timeouts on longer dictations (900+ chars exceeds 10 s).
    # 1 ms/char is imperceptible to every modern application and keeps even a
    # 2000-char injection under 2 seconds.
    KEY_DELAY_MS = 1
    # Timeout: allow 50 ms per character as a generous ceiling, minimum 10 s.
    timeout = max(10, len(text) * KEY_DELAY_MS * 50 // 1000)

    logger.info("Injecting %d chars via %s (timeout=%ds).", len(text), ydotool_socket, timeout)
    try:
        subprocess.run(
            ["ydotool", "type", f"--key-delay={KEY_DELAY_MS}", "--", text],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        logger.info("ydotool: text injected.")
    except FileNotFoundError:
        logger.error("ydotool not found — install it with: rpm-ostree install ydotool")
    except subprocess.TimeoutExpired:
        logger.error("ydotool timed out after %d s.", timeout)
    except subprocess.CalledProcessError as exc:
        logger.error(
            "ydotool failed (%d): %s",
            exc.returncode,
            exc.stderr.strip() if exc.stderr else "(no stderr)",
        )
