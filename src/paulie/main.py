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

SOCKET_PATH = f"/tmp/paulie-{os.getuid()}.sock"


def main() -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(SOCKET_PATH)
        sock.close()
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
