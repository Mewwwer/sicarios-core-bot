"""Real installed transport, synthetic RFC 7692 frames and loopback handshakes."""
from collections import deque
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha1
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import patch
import base64
import socketserver
import tempfile
import unittest
import zlib

from test_game_commands import collector
from empire_core.network import deflate
from empire_core.network.connection import Connection
from patches.apply_deflate import install, original_connection
import websocket

TEXT, BINARY, CONT, PING = 1, 2, 0, 9
TAIL = b'\x00\x00\xff\xff'


def frame(data, opcode=BINARY, fin=True, rsv1=False, rsv2=False, rsv3=False):
    return websocket.ABNF(int(fin), int(rsv1), int(rsv2), int(rsv3), opcode, 0, data).format()


def compress(data, compressor=None):
    compressor = compressor if compressor is not None else zlib.compressobj(wbits=-15)
    result = compressor.compress(data) + compressor.flush(zlib.Z_SYNC_FLUSH)
    assert result.endswith(TAIL)
    return result[:-4]


class ByteSocket:
    def __init__(self, data, chunk=4096, timeout_at=None):
        self.data, self.chunk, self.timeout_at = data, chunk, timeout_at
        self.calls = 0
        self.sent = b''

    def recv(self, size):
        self.calls += 1
        if self.calls == self.timeout_at:
            raise websocket.WebSocketTimeoutException('synthetic poll timeout')
        size = min(size, self.chunk)
        result, self.data = self.data[:size], self.data[size:]
        return result

    def send(self, data):
        self.sent += data
        return len(data)

    def gettimeout(self): return None
    def settimeout(self, value): pass


def ws_socket(data, extension='permessage-deflate', **options):
    ws = websocket.WebSocket(skip_utf8_validation=True)
    ws.sock = ByteSocket(data, **options)
    ws.connected = True
    ws.handshake_response = SimpleNamespace(headers={} if extension is None else {'sec-websocket-extensions': extension})
    deflate.accept_permessage_deflate(ws)
    return ws


@contextmanager
def local_server(plans):
    """Handshake rejects a missing extension; sends no game login or credentials."""
    pending, headers_seen = deque(plans), []
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(3)
            self.rfile.readline(8192)
            headers = {}
            for _ in range(50):
                line = self.rfile.readline(8192).strip()
                if not line: break
                key, value = line.decode('ascii').split(':', 1)
                headers[key.lower()] = value.strip()
            headers_seen.append(headers)
            if 'permessage-deflate' not in headers.get('sec-websocket-extensions', ''):
                self.request.sendall(b'HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                return
            accepted = base64.b64encode(sha1((headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
            self.request.sendall(b'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ' + accepted + b'\r\nSec-WebSocket-Extensions: permessage-deflate\r\n\r\n')
            plan = pending.popleft()
            if plan['release'].wait(5):
                self.request.sendall(plan['data'])
            # A real server acknowledges close. Leaving it unread would make
            # websocket-client's old-socket cleanup wait three seconds during
            # reconnect, racing the next synthetic server's release deadline.
            self.request.settimeout(0.1)
            while not plan['finish'].is_set():
                try:
                    data = self.request.recv(4096)
                except TimeoutError:
                    continue
                if not data:
                    return
                if data[0] & 0x0f == 8:
                    self.request.sendall(frame(b'\x03\xe8', opcode=8))
                    return
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True
    server = Server(('127.0.0.1', 0), Handler)
    runner = Thread(target=server.serve_forever, daemon=True); runner.start()
    try:
        yield f'ws://127.0.0.1:{server.server_address[1]}/', headers_seen
    finally:
        for plan in plans:
            plan['release'].set(); plan['finish'].set()
        server.shutdown(); server.server_close(); runner.join(2)


class DeflateTests(unittest.TestCase):
    def test_takeover_reuses_a_dictionary_across_compressed_messages_and_plain_interleave(self):
        compressor = zlib.compressobj(wbits=-15)
        message = b'%xt%gam%1%0%{"units":123456789,"dictionary":"repeated words"}%'
        first, second = compress(message, compressor), compress(message, compressor)
        with self.assertRaises(zlib.error): zlib.decompressobj(-15).decompress(second + TAIL)
        ws = ws_socket(frame(first, rsv1=True) + frame(b'plain', TEXT) + frame(second, rsv1=True))
        self.assertEqual([ws.recv_data()[1] for _ in range(3)], [message, b'plain', message])

    def test_no_takeover_resets_context_and_accepts_smaller_server_windows(self):
        message = b'repeated message'*10
        for bits in [9, 15]:
            data = b''.join(frame(compress(message, zlib.compressobj(wbits=-bits)), rsv1=True) for _ in range(2))
            ws = ws_socket(data, f'permessage-deflate; server_no_context_takeover; server_max_window_bits={bits}')
            self.assertEqual([ws.recv_data()[1] for _ in range(2)], [message]*2)

    def test_fragmented_binary_and_text_with_interleaved_ping_and_uncompressed_outbound(self):
        for opcode in [BINARY, TEXT]:
            message = ('česká zpráva ' * 40).encode()
            packed = compress(message)
            data = frame(packed[:3], opcode, fin=False, rsv1=True) + frame(b'ping', PING)
            data += frame(packed[3:8], CONT, fin=False) + frame(packed[8:], CONT)
            ws = ws_socket(data)
            self.assertEqual(ws.recv_data(), (opcode, message))
            self.assertTrue(ws.sock.sent)  # A pong was emitted, without RSV1.
            self.assertEqual(ws.sock.sent[0] & 0x40, 0)
            ws.sock.sent = b''; ws.send('outgoing')
            self.assertEqual(ws.sock.sent[0] & 0x40, 0)

    def test_fragmented_plain_message_does_not_inherit_previous_compression_flag(self):
        ws = ws_socket(frame(compress(b'packed'), rsv1=True) + frame(b'plain ', fin=False) + frame(b'fragment', CONT))
        self.assertEqual([ws.recv_data()[1] for _ in range(2)], [b'packed', b'plain fragment'])

    def test_partial_reads_and_poll_timeouts_preserve_inflater_and_frame_state(self):
        message = b'fragmented after poll timeout'*10
        packed = compress(message)
        data = frame(packed[:5], fin=False, rsv1=True) + frame(packed[5:], CONT)
        for timeout_at in [1, 2, 3, 6, 9, len(data)-1]:
            ws = ws_socket(data, chunk=1, timeout_at=timeout_at)
            with self.assertRaises(websocket.WebSocketTimeoutException): ws.recv_data()
            self.assertEqual(ws.recv_data()[1], message)

    def test_prefetched_bytes_survive_frame_buffer_replacement(self):
        ws = ws_socket(b'', extension=None)
        ws.frame_buffer.recv_buffer = [frame(compress(b'prefetched'), rsv1=True)]
        ws.handshake_response.headers['sec-websocket-extensions'] = 'permessage-deflate'
        self.assertTrue(deflate.accept_permessage_deflate(ws))
        self.assertEqual(ws.recv_data()[1], b'prefetched')

    def test_without_negotiation_plain_works_but_rsv1_is_rejected(self):
        ws = ws_socket(frame(b'plain') + frame(compress(b'packed'), rsv1=True), extension=None)
        self.assertNotIsInstance(ws.frame_buffer, deflate.InflatingFrameBuffer)
        self.assertEqual(ws.recv_data()[1], b'plain')
        with self.assertRaises(websocket.WebSocketProtocolException): ws.recv_data()

    def test_invalid_compression_rsv_control_and_fragment_sequences_are_rejected(self):
        packed = compress(b'split')
        cases = [frame(b'\xff', rsv1=True), frame(b'bad', rsv2=True), frame(b'bad', rsv3=True),
                 frame(b'ping', PING, rsv1=True), frame(b'ping', PING, fin=False),
                 frame(packed[:3], fin=False, rsv1=True) + frame(packed[3:], CONT, rsv1=True),
                 frame(b'orphan', CONT), frame(b'first', fin=False) + frame(b'nested')]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(websocket.WebSocketProtocolException):
                ws_socket(data).recv_data()

    def test_inflated_limit_applies_across_fragments_and_resets_per_message(self):
        self.assertEqual(deflate.MAX_MESSAGE_BYTES, 64 * 1024 * 1024)
        with patch.object(deflate, 'MAX_MESSAGE_BYTES', 1024):
            message = b'x'*1024
            ws = ws_socket(frame(compress(message), rsv1=True) + frame(compress(message), rsv1=True))
            self.assertEqual([ws.recv_data()[1] for _ in range(2)], [message]*2)
            for fragmented in [False, True]:
                packed = compress(b'x'*1025)
                data = frame(packed, rsv1=True) if not fragmented else frame(packed[:len(packed)//2], fin=False, rsv1=True) + frame(packed[len(packed)//2:], CONT)
                with self.assertRaises(websocket.WebSocketPayloadException): ws_socket(data).recv_data()


class BackportInstallTests(unittest.TestCase):
    def test_installed_source_idempotence_and_refusal_of_drift(self):
        import empire_core.network.connection as module
        source = Path(module.__file__).read_bytes()
        original = original_connection(source)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'network').mkdir()
            target = root / 'network' / 'connection.py'; target.write_bytes(original)
            with self.assertRaisesRegex(ValueError, 'not installed'): install(root, check=True)
            install(root); install(root); install(root, check=True)
            self.assertEqual(target.read_bytes(), source)
            target.write_bytes(source + b'\n# unexpected drift\n')
            with self.assertRaisesRegex(ValueError, 'Unreviewed'): install(root)
            target.write_bytes(source)
            (root / 'network' / 'deflate.py').write_bytes(b'unreviewed')
            with self.assertRaisesRegex(ValueError, 'Unexpected'): install(root)


class ConnectionDeflateTests(unittest.TestCase):
    def test_http_400_without_offer_and_compressed_routing_reconnect_with_fresh_context(self):
        message = b'%xt%gam%1%0%{}%'
        plans = [{'data': frame(compress(message), rsv1=True) + frame(b'\xff', rsv1=True), 'release': Event(), 'finish': Event()},
                 {'data': frame(compress(message), rsv1=True), 'release': Event(), 'finish': Event()}]
        c = collector(); c.settings = replace(c.settings, defense_diagnostics_enabled=False)
        c.defense.poisoned = True; c.defense.blocked_generation = c.defense.generation
        c.defense.cache[222] = {'observed_at': 1000}
        received, dropped = Event(), Event(); packets = []
        with local_server(plans) as (url, headers):
            baseline = websocket.WebSocket()
            with self.assertRaises(websocket.WebSocketBadStatusException) as error:
                baseline.connect(url, http_no_proxy=['127.0.0.1'])
            self.assertEqual(error.exception.status_code, 400)
            conn = Connection(url)
            conn.on_packet = lambda p: (packets.append(p), received.set())
            conn.add_disconnect_listener(lambda: (c.disconnected(), dropped.set()))
            try:
                conn.connect(timeout=2); old_buffer = conn.ws.frame_buffer
                waiter = conn.create_waiter('sdi')
                plans[0]['release'].set()
                self.assertTrue(received.wait(2)); self.assertTrue(dropped.wait(2))
                self.assertFalse(conn.connected)
                self.assertIsInstance(conn.close_error, websocket.WebSocketProtocolException)
                self.assertTrue(waiter.event.is_set()); self.assertIsNotNone(waiter.error)
                self.assertEqual(c.heartbeat()['session'], 'disconnected')
                self.assertFalse(c.defense.cache); self.assertTrue(c.defense.poisoned)
                self.assertFalse(c.health()['ready']); self.assertIsNone(c.publisher.take())
                received.clear(); conn.connect(timeout=2)
                self.assertEqual(conn.generation, 2)
                self.assertIsNot(conn.ws.frame_buffer, old_buffer)
                plans[1]['release'].set(); self.assertTrue(received.wait(2))
                self.assertIsNone(conn.close_error)
                self.assertEqual([p.command_id for p in packets], ['gam', 'gam'])
                c.refresh_members(); c.refresh()  # Confirm a logged-in new session, as before.
                self.assertFalse(c.defense.poisoned)
                self.assertTrue(c.health()['ready'])
                self.assertEqual(c.heartbeat()['session'], 'connected')
                self.assertIn('permessage-deflate', headers[1]['sec-websocket-extensions'])
                self.assertIn('permessage-deflate', headers[2]['sec-websocket-extensions'])
            finally:
                conn.disconnect(); c.defense.close()
