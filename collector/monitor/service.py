from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread, BoundedSemaphore
import hashlib
import hmac
import copy
import json
import logging
import signal
import time

from .state import SnapshotPublisher, member_item, section
from .defense import DefenseLookup, LookupError
from .config import Settings
from .normalize import normalize_attack
from .transport import CoreTransport, DeliveryError, MemoryDeliveryQueue

LOG = logging.getLogger('sicarios.collector')


class Collector:
    def __init__(self, settings, client, transport=None, metadata_loader=None):
        self.settings, self.client = settings, client
        self.transport = transport or CoreTransport(settings)
        self.metadata_loader = metadata_loader
        self.metadata = None
        self.stop = Event()
        self.lock = Lock()
        self.session = 'starting'
        self.last_snapshot = None
        self.queue = MemoryDeliveryQueue(settings.max_pending)
        self.fatal = False
        self.health_server = None
        self.publisher = SnapshotPublisher(settings)
        self.members = None
        self.session_generation = 0
        self.defense = DefenseLookup(self)

    def set_session(self, session):
        with self.lock:
            self.session = session

    def disconnected(self):
        with self.lock:
            self.session = 'disconnected'
            self.session_generation += 1
            self.members = None
        self.publisher.clear()
        self.defense.disconnect()

    def members_snapshot(self):
        with self.lock:
            return copy.deepcopy(self.members)

    def refresh_members(self):
        if not self.settings.game_commands_enabled or not self.client.is_logged_in or not self.alliance_matches():
            return False
        with self.lock:
            generation = self.session_generation
        try:
            members = [member_item(m) for m in self.client.alliance.get_local_members(timeout=5)]
            if len({m['player_id'] for m in members}) != len(members):
                raise ValueError('Duplicate member')
            value = section(members, 250, int(time.time()))
            with self.lock:
                if generation != self.session_generation or not self.client.is_logged_in or not self.alliance_matches():
                    return False
                self.publisher.update('members', value)
                self.members = value
            return True
        except Exception as exc:
            LOG.warning('Member refresh failed (%s); last observation retained', type(exc).__name__)
            return False

    def members_loop(self):
        while self.settings.game_commands_enabled and not self.stop.is_set():
            self.refresh_members()
            self.stop.wait(self.settings.members_poll_seconds)

    def state_delivery_loop(self):
        while self.settings.game_commands_enabled and not self.stop.is_set():
            pending = self.publisher.take()
            if pending is None:
                self.stop.wait(0.25)
                continue
            try:
                self.transport.post('/v2/state', pending)
            except DeliveryError as exc:
                # v2 failures never stop v1 alerts or heartbeat delivery.
                LOG.warning('State delivery failed (%s); newest snapshot will retry', exc.code)
                self.publisher.retry(pending)
                self.stop.wait(5)

    def heartbeat(self):
        with self.lock:
            return {'schema_version': 1, 'server_id': self.settings.server_id,
                    'observed_at': int(time.time()), 'last_snapshot_at': self.last_snapshot, 'session': self.session}

    def health(self):
        heartbeat = self.heartbeat()
        fresh = heartbeat['last_snapshot_at'] is not None and time.time() - heartbeat['last_snapshot_at'] <= 120
        return {'alive': not self.stop.is_set(), 'ready': heartbeat['session'] == 'connected' and fresh,
                'session': heartbeat['session'], 'pending': len(self.queue),
                'game_commands': {'enabled': self.settings.game_commands_enabled,
                                  'defense_enabled': self.settings.defense_lookup_enabled,
                                  'defense_diagnostics_enabled': self.settings.defense_diagnostics_enabled,
                                  'sdi_quarantined': self.defense.poisoned}}

    def enqueue(self, movement, *, checkpoint=None):
        if not self.alliance_matches():
            return None
        payload = normalize_attack(movement, self.settings.server_id, self.settings.alliance_id, self.metadata)
        if payload is not None and not self.queue.submit(payload, checkpoint=checkpoint):
            LOG.warning('Delivery queue full; active attacks will be retried on the next snapshot')
        return payload

    def alliance_matches(self):
        player = self.client.state.local_player
        alliance = player.alliance if player else None
        return alliance is not None and alliance.id == self.settings.alliance_id

    def refresh(self):
        """Only get_movements performs a game request; no action services."""
        if not self.client.is_logged_in:
            if self.session != 'disconnected':
                self.disconnected()
            return False
        if not self.alliance_matches():
            LOG.error('Alliance membership does not match GGE_ALLIANCE_ID; stopping')
            self.fatal = True
            self.set_session('login_failed')
            self.stop.set()
            return False
        with self.lock:
            generation = self.session_generation
        try:
            self.client.movements.get_movements(timeout=8)
        except Exception as exc:
            LOG.warning('Game snapshot failed (%s)', type(exc).__name__)
            # Keep the last success timestamp; never claim failed reads are fresh.
            return False
        with self.lock:
            if generation != self.session_generation or not self.client.is_logged_in:
                return False
        checkpoint = self.queue.checkpoint()
        active = self.client.state.get_announced_attacks()
        if not self.alliance_matches():
            self.set_session('login_failed')
            self.fatal = True
            self.stop.set()
            return False
        active_keys = set()
        state_items = []
        state_valid = True
        observed = int(time.time())
        for movement in active:
            if self.settings.game_commands_enabled:
                try:
                    item = normalize_attack(movement, self.settings.server_id, self.settings.alliance_id, self.metadata, now=observed, include_elapsed=True)
                    if item is not None and len(state_items) <= 500:
                        state_items.append(item)
                except Exception as exc:
                    state_valid = False
                    LOG.warning('Attack overview normalization failed (%s)', type(exc).__name__)
            payload = self.enqueue(movement, checkpoint=checkpoint)
            if payload is not None:
                active_keys.add(self.queue.key(payload))
        self.queue.retain(active_keys, checkpoint=checkpoint)
        with self.lock:
            if generation != self.session_generation or not self.client.is_logged_in:
                return False
            self.last_snapshot = int(time.time())
            self.session = 'connected'
            if self.settings.game_commands_enabled and state_valid:
                try:
                    self.publisher.update('attacks', section(state_items, 500, observed))
                except Exception as exc:
                    LOG.warning('Attack overview publishing failed (%s)', type(exc).__name__)
        self.defense.confirm_session(generation)
        return True

    def delivery_loop(self):
        while not self.stop.is_set():
            task = self.queue.take()
            if task is None:
                self.stop.wait(0.25)
                continue
            key, payload = task
            try:
                self.transport.post('/v1/attacks', payload)
                self.queue.done(key)
            except DeliveryError as exc:
                if exc.permanent:
                    LOG.error('Core rejected an alert (%s); check receiver configuration', exc.code)
                    self.fatal = True
                    self.stop.set()
                    self.client.close()
                    return
                self.queue.retry(key, exc.retry_after)

    def heartbeat_loop(self):
        failed = False
        while not self.stop.is_set():
            try:
                self.transport.post('/v1/heartbeat', self.heartbeat())
                if failed:
                    LOG.info('Core connection recovered')
                failed = False
            except DeliveryError as exc:
                if not failed:
                    LOG.warning('Heartbeat delivery failed (%s)', exc.code)
                failed = True
                if exc.permanent:
                    LOG.error('Core rejected heartbeat; check URL, server ID and shared secret')
                    self.fatal = True
                    self.stop.set()
                    self.client.close()
                    return
            self.stop.wait(self.settings.heartbeat_seconds)

    def metadata_loop(self):
        if self.metadata_loader is None:
            return
        while not self.stop.is_set():
            try:
                self.metadata = self.metadata_loader()
                LOG.info('Unit metadata ready; known units can be separated from tools')
                return
            except Exception as exc:
                LOG.warning('Unit metadata unavailable (%s); troop sizes remain estimated or unknown', type(exc).__name__)
            self.stop.wait(300)

    def start_health(self):
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def do_POST(self):
                status, result = 404, {'error': 'not_found'}
                try:
                    if self.path != '/v2/defense':
                        raise LookupError(404, 'not_found')
                    expected = hashlib.sha256(('Bearer ' + collector.settings.secret).encode()).digest()
                    supplied = hashlib.sha256(self.headers.get('Authorization', '').encode()).digest()
                    if not hmac.compare_digest(expected, supplied):
                        raise LookupError(401, 'unauthorized')
                    if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                        raise LookupError(400, 'invalid_request')
                    length = int(self.headers.get('Content-Length', '0'))
                    if length <= 0 or length > 8192:
                        raise LookupError(400, 'invalid_request')
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise LookupError(400, 'invalid_request')
                    result = collector.defense.lookup(json.loads(body))
                    status = 200
                except LookupError as exc:
                    status, result = exc.status, {'error': exc.code}
                except (ValueError, OSError):
                    status, result = 400, {'error': 'invalid_request'}
                except Exception:
                    status, result = 503, {'error': 'lookup_unavailable'}
                body = json.dumps(result, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path not in ('/healthz', '/readyz'):
                    self.send_error(404)
                    return
                health = collector.health()
                status = 200 if self.path == '/healthz' else (200 if health['ready'] else 503)
                body = json.dumps(health).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        class BoundedServer(ThreadingHTTPServer):
            daemon_threads = True
            slots = BoundedSemaphore(16)

            def process_request(self, request, address):
                if not self.slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, address)
                except Exception:
                    self.slots.release()
                    raise

            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    self.slots.release()

        self.health_server = BoundedServer(('0.0.0.0', self.settings.health_port), Handler)
        Thread(target=self.health_server.serve_forever, daemon=True).start()

    def run(self):
        self.start_health()
        # Callbacks never make HTTP requests. Delivery/heartbeat are independent
        # so one slow alert cannot block game processing or hide a stale feed.
        self.client.state.on_incoming_attack(self.enqueue)
        self.client.state.on_incoming_attack_updated(lambda _old, new: self.enqueue(new))
        self.client.on_disconnect(self.disconnected)

        def lost(_error):
            self.fatal = True
            self.set_session('login_failed')
            self.stop.set()

        self.client.on_session_lost(lost)
        threads = [Thread(target=target, daemon=True) for target in
                   (self.delivery_loop, self.heartbeat_loop, self.metadata_loop, self.members_loop, self.state_delivery_loop)]
        for thread in threads:
            thread.start()
        try:
            if not self.stop.is_set():
                self.client.login(retry=True)
            while not self.stop.is_set():
                self.refresh()
                self.stop.wait(self.settings.poll_seconds)
        except Exception as exc:
            if not self.stop.is_set():
                LOG.error('Game login or collector failed (%s); manual review needed', type(exc).__name__)
                self.fatal = True
                self.set_session('login_failed')
                try:
                    self.transport.post('/v1/heartbeat', self.heartbeat())
                except DeliveryError:
                    pass
        finally:
            self.stop.set()
            self.client.close()
            self.defense.close()
            for thread in [threads[0], threads[1], threads[3], threads[4]]:
                thread.join(timeout=self.settings.http_timeout + 1)
            self.set_session('login_failed' if self.fatal else 'stopped')
            try:
                self.transport.post('/v1/heartbeat', self.heartbeat())
            except DeliveryError:
                pass
            self.health_server.shutdown()
            self.health_server.server_close()
        return 2 if self.fatal else 0


def main():
    logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s %(message)s')
    LOG.setLevel(logging.INFO)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        LOG.error('%s', exc)
        return 2
    # Imported only for real game mode, not for simulated tests.
    from empire_core import EmpireClient, EmpireConfig, GameData
    fields = {'game_url': settings.game_url, 'default_zone': settings.game_zone,
              'connection_timeout': 10, 'login_timeout': 15, 'request_timeout': 8}
    if settings.client_version:
        fields['client_version'] = settings.client_version
    client = EmpireClient(username=settings.username, password=settings.password,
                          config=EmpireConfig(**fields), keep_session=True)

    def load_metadata():
        data = GameData.load()
        return frozenset(data.units), frozenset(data.tools)

    collector = Collector(settings, client, metadata_loader=load_metadata)

    def shutdown(_signum, _frame):
        collector.stop.set()
        client.close()  # Interrupts a pending initial login/relogin, too.

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    LOG.info('Starting alliance attack collector; no attack history is stored')
    return collector.run()
