"""Opt-in synthetic Streamable HTTP authentication, app lifecycle and capacity."""
import asyncio
from contextlib import closing
import errno
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
import httpx2
from jobs import agent_api, mcp_http, panel
from jobs.http_server import ThreadingHTTPServer
import test_mcp as fixtures

TOKEN = fixtures.TOKEN


def bind_conflict(error):
    # Native Winsock also reports WSAEACCES for an exclusive occupied port.
    return error.errno == errno.EADDRINUSE or (os.name == 'nt' and getattr(error, 'winerror', None) == 10013)



class HTTPMCP(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.MCPFixture()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = dict(self.fixture.server.api.config)
        self.config['mcp'] = {'enabled': True, 'token_file': str(self.fixture.token_file)}
        self.panel = self.bind_panel()
        self.addCleanup(self.panel.server_close)
        self.config['panel'] = {'host': '127.0.0.1', 'port': self.panel.server_port}
        self.service = None
        self.addCleanup(self.stop)

    def bind_panel(self, host='127.0.0.1'):
        for _ in range(30):
            server = ThreadingHTTPServer((host, 0), panel.Handler)
            if server.server_port == 65535:
                server.server_close()
                continue
            with socket.socket() as probe:
                try:
                    probe.bind((host, server.server_port+1))
                    return server
                except OSError as error:
                    server.server_close()
                    if not bind_conflict(error):
                        raise
        self.fail('Could not reserve adjacent synthetic ports')

    def container_panel(self):
        self.panel.server_close()
        self.panel = self.bind_panel('0.0.0.0')
        self.addCleanup(self.panel.server_close)
        self.config['panel'] = {'host': '0.0.0.0', 'port': self.panel.server_port}

    def start_container(self):
        # The probe cannot reserve the derived port through production startup.
        # Reselect only when a listener really claimed that wildcard port.
        for attempt in range(5):
            try:
                return self.start()
            except OSError as error:
                if not bind_conflict(error) or attempt == 4:
                    raise
                with socket.socket() as probe:
                    try:
                        probe.bind(('0.0.0.0', self.panel.server_port+1))
                    except OSError as conflict:
                        if not bind_conflict(conflict):
                            raise
                    else:
                        raise error
                self.container_panel()

    def stop(self):
        if self.service:
            self.service.close()

    def start(self):
        self.service = mcp_http.start(self.config, self.panel)
        self.assertEqual(self.service.port, self.panel.server_port+1)
        return self.service

    def request(self, *, headers=None, method='POST', body=b'{}'):
        connection = http.client.HTTPConnection('127.0.0.1', self.panel.server_port+1, timeout=5)
        value = {'Content-Type': 'application/json', 'Authorization': 'Bearer '+TOKEN}
        value.update(headers or {})
        connection.request(method, '/mcp', body=body, headers=value)
        response = connection.getresponse(); response.read()
        result = response.status, dict(response.getheaders())
        connection.close()
        return result

    async def test_real_http_tools_snapshot_and_full_body(self):
        service = self.start()
        async with httpx2.AsyncClient(headers={'Authorization': 'Bearer '+TOKEN}) as http:
            async with Client(streamable_http_client(service.url, http_client=http), mode='legacy') as client:
                self.assertEqual(client.server_info.name, 'AU Job Board (read-only)')
                self.assertEqual({t.name for t in (await client.list_tools()).tools},
                    {'list_jobs','get_job','list_batches','get_batch'})
                first = await client.call_tool('list_jobs', {'query': {'limit': 1}})
                self.assertFalse(first.is_error)
                page = first.structured_content
                uid, sid = page['items'][0]['uid'], page['snapshot_id']
                with closing(agent_api.read_connection(self.fixture.path)) as conn:
                    with self.assertRaises(sqlite3.OperationalError):
                        conn.execute('DELETE FROM jobs')
                next_page = await client.call_tool('list_jobs', {'query': {'cursor': page['next_cursor']}})
                self.assertEqual(next_page.structured_content['snapshot_id'], sid)
                detail = await client.call_tool('get_job', {'uid': uid, 'snapshot_id': sid})
                self.assertTrue(detail.structured_content['item']['description'])
                batches = await client.call_tool('list_batches', {'limit': 1})
                bid = batches.structured_content['items'][0]['batch_id']
                batch = await client.call_tool('get_batch', {'batch_id': bid})
                self.assertEqual(batch.structured_content['item']['batch_id'], bid)
                bad = await client.call_tool('list_jobs', {'query': {'view': 'recommend', 'geo': 'VIC'}})
                self.assertTrue(bad.is_error)

    async def test_auth_host_origin_and_request_size(self):
        self.start()
        for headers, status in [({'Authorization': ''}, 401),
                                ({'Authorization': 'Bearer wrong'}, 401),
                                ({'Host': 'evil.invalid'}, 403),
                                ({'Origin': 'null'}, 403)]:
            self.assertEqual(self.request(headers=headers)[0], status)
        status, headers = self.request(headers={'Authorization': ''})
        self.assertIn('www-authenticate', {k.lower():v for k,v in headers.items()})
        self.assertNotIn('access-control-allow-origin', {k.lower():v for k,v in headers.items()})
        self.assertEqual(self.request(body=b'x'*(65536+1))[0], 413)

    async def test_container_listener_keeps_loopback_authority_and_auth(self):
        self.container_panel()
        with patch.dict(os.environ, {'JOBS_CONTAINER': '0'}):
            with self.assertRaises(ValueError):
                self.start()
        with patch.dict(os.environ, {'JOBS_CONTAINER': '1'}):
            service = self.start_container()
        self.assertEqual(service.url, f'http://127.0.0.1:{service.port}/mcp')
        for headers, status in [({'Authorization': ''}, 401),
                                ({'Host': f'0.0.0.0:{service.port}'}, 403),
                                ({'Host': 'evil.invalid'}, 403),
                                ({'Origin': 'null'}, 403)]:
            self.assertEqual(self.request(headers=headers)[0], status)
        async with httpx2.AsyncClient(headers={'Authorization': 'Bearer '+TOKEN}) as http:
            async with Client(streamable_http_client(service.url, http_client=http), mode='legacy') as client:
                self.assertEqual(client.server_info.name, 'AU Job Board (read-only)')
                page = (await client.call_tool('list_jobs', {'query': {'limit': 1}})).structured_content
                detail = await client.call_tool('get_job', {'uid': page['items'][0]['uid'],
                                                         'snapshot_id': page['snapshot_id']})
                self.assertFalse(detail.is_error)
                self.assertTrue(detail.structured_content['item']['panel_url'].startswith(
                    f'http://127.0.0.1:{self.panel.server_port}/'))

    async def test_container_fixture_recovers_from_port_handoff_collision(self):
        self.container_panel()
        blocked_port = self.panel.server_port+1
        with socket.socket() as occupied:
            # Occupy after the fixture's successful probe, before MCP binds.
            occupied.bind(('0.0.0.0', blocked_port))
            occupied.listen()
            with patch.dict(os.environ, {'JOBS_CONTAINER': '1'}):
                with self.assertRaises(OSError) as caught:
                    self.start()
                self.assertTrue(bind_conflict(caught.exception), caught.exception)
                service = self.start_container()
            self.assertNotEqual(service.port, blocked_port)
            self.assertEqual(self.request(headers={'Authorization': ''})[0], 401)

    def test_container_fixture_does_not_retry_unrelated_startup_errors(self):
        self.container_panel()
        for error in (OSError(errno.EACCES, 'controlled permission error'),
                      OSError(errno.EADDRINUSE, 'controlled internal port error'),
                      ValueError('controlled configuration error')):
            with self.subTest(error=error), patch.object(mcp_http, 'start', side_effect=error) as start:
                with self.assertRaises(type(error)) as caught:
                    self.start_container()
                self.assertIs(caught.exception, error)
                self.assertEqual(start.call_count, 1)

    def test_container_fixture_bounds_port_collision_reselection(self):
        self.container_panel()
        start = self.start
        occupied = []
        def collide():
            listener = socket.socket()
            self.addCleanup(listener.close)
            listener.bind(('0.0.0.0', self.panel.server_port+1))
            listener.listen()
            occupied.append(listener)
            return start()
        with patch.dict(os.environ, {'JOBS_CONTAINER': '1'}), \
                patch.object(self, 'start', side_effect=collide) as attempts:
            with self.assertRaises(OSError) as caught:
                self.start_container()
        self.assertTrue(bind_conflict(caught.exception), caught.exception)
        self.assertEqual(attempts.call_count, 5)
        self.assertEqual(len(occupied), 5)

    async def test_tls_certificate_verified(self):
        root = self.fixture.root
        cfg = root/'openssl.cnf'
        cfg.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n'
                       '[dn]\nCN=localhost\n[ext]\nsubjectAltName=IP:127.0.0.1\n')
        cert, key = root/'cert.pem', root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-config',str(cfg),'-keyout',str(key),'-out',str(cert)], check=True, capture_output=True)
        self.config['mcp'].update(tls_certfile=str(cert), tls_keyfile=str(key))
        service = self.start()
        async with httpx2.AsyncClient(verify=ssl.create_default_context(cafile=cert),
                headers={'Authorization': 'Bearer '+TOKEN}) as http:
            async with Client(streamable_http_client(service.url, http_client=http)) as client:
                self.assertEqual(len((await client.list_tools()).tools), 4)

    async def test_conflict_and_invalid_settings_cleanup(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1',self.panel.server_port+1)); occupied.listen()
            with self.assertRaises(OSError):
                mcp_http.start(self.config,self.panel)
        for value in [{'enabled':'yes'}, {'enabled':True,'token_file':'missing'},
                      {'enabled':True,'host':'0.0.0.0'},
                      {'enabled':True,'token_file':str(self.fixture.token_file),'tls_certfile':'missing'}]:
            self.config['mcp'] = value
            with self.assertRaises((ValueError,OSError)):
                mcp_http.start(self.config,self.panel)
        self.assertIsNone(mcp_http.start({}, self.panel))

    async def test_application_start_stop_owns_mcp_and_cli_listener_overrides(self):
        # Start the actual CLI/panel entry, rather than just the sidecar class.
        port = self.panel.server_port
        self.panel.server_close()
        root = self.fixture.root
        config = dict(self.config)
        config['panel'] = {'host':'localhost','port':port+10}
        path = root/'config.json';path.write_text(json.dumps(config))
        output = self.enterContext((root/'app.log').open('w+'))
        proc = subprocess.Popen([sys.executable,'-B','-m','jobs.cli','--config',str(path),
            '--rules',str(root/'rules.yaml'),'--skills',str(root/'skills.yaml'),
            'panel','--host','127.0.0.1','--port',str(port)], cwd=ROOT,
            stdout=output,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
            env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1'))
        def cleanup():
            if proc.poll() is None:
                proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
                try: proc.wait(timeout=12)
                except subprocess.TimeoutExpired: proc.kill();proc.wait(timeout=5)
        self.addCleanup(cleanup)
        # Cold CI runners import the full CLI stack slowly; bound by wall time.
        deadline = time.monotonic() + 120
        while True:
            if proc.poll() is not None:
                self.fail('Application exited: '+(root/'app.log').read_text())
            try:
                if self.request(headers={'Authorization':''})[0] == 401:
                    break
            except (OSError, http.client.HTTPException):
                # A Windows client can briefly self-connect if its ephemeral
                # source port equals the not-yet-listening destination port.
                pass
            if time.monotonic() > deadline:
                self.fail('Application MCP startup timed out: '+(root/'app.log').read_text())
            await asyncio.sleep(.05)
        connection = http.client.HTTPConnection('127.0.0.1',port,timeout=10)
        connection.request('GET','/?view=all')
        response = await asyncio.to_thread(connection.getresponse)
        response.read(); self.assertEqual(response.status,200);connection.close()
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == 'nt' else signal.SIGINT)
        self.assertEqual(await asyncio.to_thread(proc.wait, timeout=12), 0)
        for value in (port,port+1):
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(('127.0.0.1',value)), 0)

    def test_shared_snapshot_settings_and_large_synthetic_input(self):
        store = agent_api.snapshot_store({})
        self.assertEqual((store.max_snapshot_bytes,store.max_bytes,store.max_rows),
                         (128*1024*1024,512*1024*1024,50000))
        cfg={'agent_api':{'snapshots':{'max_snapshot_mib':2,'max_total_mib':3,'max_rows':100,'ttl_seconds':10}}}
        configured=agent_api.AgentAPI(self.config | cfg).store
        self.assertEqual((configured.max_snapshot_bytes,configured.max_bytes,configured.max_rows,configured.ttl),
                         (2*1024*1024,3*1024*1024,100,10))
        for value in [{'ttl_seconds':0},{'max_rows':True},{'unknown':1},
                      {'max_snapshot_mib':3,'max_total_mib':2}]:
            with self.assertRaises(ValueError):
                agent_api.snapshot_store({'agent_api':{'snapshots':value}})
        body='x'*(17*1024*1024)
        snapshot=store.create(lambda: ({'query':{'limit':1}},['synthetic:large'],
                                      {'synthetic:large':{'uid':'synthetic:large','description':body}}))
        self.assertEqual(store.get(snapshot.snapshot_id).records['synthetic:large']['description'],body)


if __name__=='__main__':
    unittest.main(verbosity=2)
