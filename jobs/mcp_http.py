"""Optional authenticated Streamable HTTP sidecar owned by the panel listener."""
from contextlib import closing
import hmac
import os
from pathlib import Path
import socket
import threading
import time

from . import agent_api, config as config_mod
from .agent_client import APIClient
from .mcp_server import make_server
from .http_server import configure_listener


class BearerGuard:
    """Reject untrusted headers before the SDK can log them or create a session."""
    def __init__(self, app, token, authority):
        self.app, self.token, self.authority = app, ('Bearer ' + token).encode(), authority.encode()

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            headers = scope.get('headers', [])
            auth = [v for k, v in headers if k.lower() == b'authorization']
            hosts = [v for k, v in headers if k.lower() == b'host']
            status = None
            if len(auth) != 1 or not hmac.compare_digest(auth[0], self.token):
                status = 401
            elif hosts != [self.authority] or any(k.lower() == b'origin' for k, _ in headers):
                status = 403
            if status:
                extra = [(b'www-authenticate', b'Bearer')] if status == 401 else []
                await send({'type': 'http.response.start', 'status': status,
                            'headers': [(b'content-length', b'0'), (b'cache-control', b'no-store'), *extra]})
                await send({'type': 'http.response.body', 'body': b''})
                return
        await self.app(scope, receive, send)


def settings(config):
    value = config.get('mcp', {})
    if not isinstance(value, dict) or type(value.get('enabled', False)) is not bool:
        raise ValueError('mcp.enabled must be boolean')
    allowed = {'enabled', 'token_file', 'tls_certfile', 'tls_keyfile'}
    if set(value) - allowed:
        raise ValueError('Unknown mcp setting; listener inherits panel host and port +1')
    if not value.get('enabled', False):
        return None
    if not isinstance(value.get('token_file'), str) or not value['token_file']:
        raise ValueError('mcp.token_file is required')
    def path(key):
        raw = value.get(key)
        if raw is None:
            return None
        if not isinstance(raw, str) or not raw:
            raise ValueError('Invalid mcp file setting')
        target = Path(raw)
        return str(target if target.is_absolute() else config_mod.ROOT / target)
    resolved = {key: path(key) for key in ('token_file', 'tls_certfile', 'tls_keyfile')}
    if bool(resolved['tls_certfile']) != bool(resolved['tls_keyfile']):
        raise ValueError('MCP TLS requires both certificate and key')
    return resolved


class MCPHTTPService:
    def __init__(self, config, panel_server):
        self.api = self.api_thread = self.http = self.http_thread = self.socket = None
        options = settings(config)
        if options is None:
            raise ValueError('MCP is not enabled')
        import uvicorn
        from mcp.server.transport_security import TransportSecuritySettings

        host, port = panel_server.server_address[:2]
        configured_host = str((config.get('panel') or {}).get('host') or host).strip().lower()
        container = host == '0.0.0.0' and os.environ.get('JOBS_CONTAINER') == '1'
        if (host in ('0.0.0.0', '::') and not container) or not 0 < port < 65535:
            raise ValueError('MCP requires a specific panel address and an available next port')
        # Docker publishes loopback ports on the host; wildcard is only the bind
        # address inside its namespace, never an HTTP authority or panel link.
        if container:
            configured_host = '127.0.0.1'
        self.host, self.port = host, port + 1
        token = agent_api.read_token(options['token_file'])
        with closing(agent_api.read_connection(config_mod.db_path(config))):
            pass
        try:
            # Reserve the public derived port before an ephemeral internal listener
            # can be assigned that same port by the kernel.
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            configure_listener(self.socket)
            self.socket.bind((host, self.port))
            self.socket.listen(128)
            self.socket.setblocking(False)
            # Internal API stays loopback-only and authenticated, with no fixed extra port.
            self.api = agent_api.make_server(config, token, port=0,
                panel_url=f'http://{configured_host}:{port}/')
            server = make_server(APIClient(f'http://127.0.0.1:{self.api.server_port}', token),
                                 allow_label_writes=self.api.api.allow_label_writes)
            authority = f'{configured_host}:{self.port}'
            security = TransportSecuritySettings(allowed_hosts=[authority], allowed_origins=[])
            app = BearerGuard(server.streamable_http_app(json_response=True, stateless_http=True,
                max_request_body_size=64*1024, transport_security=security, host=host), token, authority)
            cfg = uvicorn.Config(app, host=host, port=self.port, access_log=False,
                log_config=None, log_level='warning', timeout_graceful_shutdown=5,
                ssl_certfile=options['tls_certfile'], ssl_keyfile=options['tls_keyfile'])
            cfg.load()  # Validate optional TLS files before publishing either service.
            self.http = uvicorn.Server(cfg)
            self.api_thread = threading.Thread(target=self.api.serve_forever, daemon=True)
            self.api_thread.start()
            self.http_thread = threading.Thread(target=self.http.run, kwargs={'sockets': [self.socket]}, daemon=True)
            self.http_thread.start()
            deadline = time.monotonic() + 10
            while not self.http.started:
                if not self.http_thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError('MCP HTTP startup failed')
                time.sleep(.01)
        except BaseException:
            self.close()
            raise
        self.url = f'{"https" if options["tls_certfile"] else "http"}://{authority}/mcp'

    def close(self):
        if self.http:
            self.http.should_exit = True
        if self.http_thread and self.http_thread.is_alive():
            self.http_thread.join(timeout=10)
        if self.socket:
            self.socket.close()
        if self.api:
            if self.api_thread and self.api_thread.is_alive():
                self.api.shutdown()
                self.api_thread.join(timeout=5)
            self.api.server_close()


def start(config, panel_server):
    if settings(config) is None:
        return None
    return MCPHTTPService(config, panel_server)
