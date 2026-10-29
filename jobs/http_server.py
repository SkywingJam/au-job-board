"""HTTP listener ownership with platform-appropriate address reuse."""
import os
import socket
import socketserver
from http.server import ThreadingHTTPServer as _ThreadingHTTPServer


def configure_listener(sock):
    # Windows SO_REUSEADDR permits another listener to take the same address.
    # POSIX keeps the existing quick-restart behavior.
    option = socket.SO_EXCLUSIVEADDRUSE if os.name == 'nt' else socket.SO_REUSEADDR
    sock.setsockopt(socket.SOL_SOCKET, option, 1)


class ThreadingHTTPServer(_ThreadingHTTPServer):
    # Windows SO_REUSEADDR lets a second listener share the port; claim it exclusively.
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        # http.server sets server_name via socket.getfqdn(host), a reverse DNS
        # lookup that macOS CI runners time out after 35 s per bind. Nothing here
        # reads server_name, so record the listening address instead.
        if os.name == 'nt':
            configure_listener(self.socket)
        socketserver.TCPServer.server_bind(self)
        self.server_name = self.server_address[0]
        self.server_port = self.server_address[1]
