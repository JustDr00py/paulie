"""
main.py — Paulie trigger client.

Usage
-----
paulie              Send a trigger to the running daemon (start / cancel dictation).
paulie status       Print daemon status and exit.

If the daemon is not running, prints instructions and exits with code 1.

IPC protocol (one byte sent by the client):
    \\x01  Trigger (or legacy bare-connect)
    \\x00  Status ping — daemon responds with a JSON line, no dictation started.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

# Must match the computation in daemon.py exactly.
SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp",
    f"paulie-{os.getuid()}.sock",
)

_DAEMON_NOT_RUNNING = (
    "error: paulie daemon is not running.\n"
    "Start it with:  paulie-daemon &\n"
    "Or add paulie-daemon to KDE Autostart."
)


def _connect() -> socket.socket:
    """Return a connected Unix socket, or print an error and exit."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCKET_PATH)
    except (FileNotFoundError, ConnectionRefusedError):
        print(_DAEMON_NOT_RUNNING, file=sys.stderr)
        sys.exit(1)
    return sock


def _trigger() -> None:
    with _connect() as sock:
        sock.sendall(b"\x01")


def _status() -> None:
    config_path = Path(os.environ.get(
        "PAULIE_CONFIG",
        Path.home() / ".config" / "paulie" / "paulie.conf",
    ))

    # Try to ping the daemon.
    running = False
    info: dict = {}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.connect(SOCKET_PATH)
            sock.sendall(b"\x00")
            sock.settimeout(1.0)
            try:
                raw = sock.recv(4096)
                info = json.loads(raw.decode().strip())
                running = info.get("running", False)
            except (OSError, json.JSONDecodeError):
                # Connected but no valid response — daemon is running (old version).
                running = True
    except (FileNotFoundError, ConnectionRefusedError):
        running = False

    status_str = "running" if running else "not running"
    config_str = "exists" if config_path.exists() else "not found (using defaults)"

    print(f"Daemon:      {status_str}")
    print(f"Socket:      {SOCKET_PATH}")
    print(f"Config:      {config_path}  [{config_str}]")

    if not running:
        sys.exit(1)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        _status()
        return

    _trigger()


if __name__ == "__main__":
    main()
