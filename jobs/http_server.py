"""HTTP listener ownership with platform-appropriate address reuse."""
import os
import socket
from http.server import ThreadingHTTPServer as _ThreadingHTTPServer


def configure_listener(sock):
    # Windows SO_REUSEADDR permits another listener to take the same address.
    # POSIX keeps the existing quick-restart behavior.
    option = socket.SO_EXCLUSIVEADDRUSE if os.name == 'nt' else socket.SO_REUSEADDR
    sock.setsockopt(socket.SOL_SOCKET, option, 1)


if os.name == 'nt':
    class ThreadingHTTPServer(_ThreadingHTTPServer):
        allow_reuse_address = False

        def server_bind(self):
            configure_listener(self.socket)
            super().server_bind()
else:
    ThreadingHTTPServer = _ThreadingHTTPServer
