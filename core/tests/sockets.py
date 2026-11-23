"""A TCP server that accepts connections and never answers, like a hung service."""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


@contextmanager
def hanging_tcp_server() -> Generator[int]:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]
    stop = threading.Event()
    held: list[socket.socket] = []

    def serve() -> None:
        sock.settimeout(0.1)
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
                held.append(conn)
            except TimeoutError:
                continue
        for conn in held:
            conn.close()
        sock.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=2)
