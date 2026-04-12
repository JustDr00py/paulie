"""
main.py — Paulie trigger client.

Connects to the running paulie-daemon and signals it to start a dictation
cycle, then exits immediately.

If the daemon is not running, prints instructions and exits with code 1.
"""

from __future__ import annotations

import logging
import os
import socket
import sys

logging.basicConfig(level=logging.WARNING)

# Must match the computation in daemon.py exactly.
SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp",
    f"paulie-{os.getuid()}.sock",
)


def main() -> None:
    # Context manager guarantees the fd is closed on every exit path,
    # including the ConnectionRefusedError / FileNotFoundError branches.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        try:
            sock.connect(SOCKET_PATH)
        except (FileNotFoundError, ConnectionRefusedError):
            print(
                "error: paulie daemon is not running.\n"
                "Start it with:  paulie-daemon &\n"
                "Or add paulie-daemon to KDE Autostart.",
                file=sys.stderr,
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
