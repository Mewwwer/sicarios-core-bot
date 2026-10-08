from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from threading import Event, Thread
from uuid import uuid4
from urllib import request, error
import subprocess
import json
import time
import unittest
from unittest.mock import patch

from test_collector import settings, movement
import test_collector
from monitor.service import Collector
from monitor.state import member_item, section, SnapshotPublisher
from monitor.defense import LookupError, normalize_defense
from monitor.transport import DeliveryError
from empire_core.alliance.models.info import AllianceMember, AllianceMemberInfo
from empire_core.player.models.info import GetPlayerInfoResponse, PlayerOwnerInfo
from empire_core.castle.models.castles import CastleInfo
from empire_core.enums import Kingdom, MapItemType
from empire_core.defense.models import GetSupportDefenseResponse


def member(pid=222, name='Člen', activity=0):
    m = AllianceMember(OID=pid, N=name)
    m._member_info = AllianceMemberInfo(player_id=pid, **({'login_activity': activity} if activity is not None else {}))
    return m


def profile(pid, aid=444, **castle_changes):
    castle = CastleInfo(castle_id=pid+1000, castle_name='Hrad', owner_id=pid, x=pid, y=20,
                        kingdom_id=Kingdom.GREEN, castle_type=MapItemType.CASTLE, **castle_changes)
    p = GetPlayerInfoResponse(owner=PlayerOwnerInfo(OID=pid, AID=aid, N='Člen'))
    # get_castles reads this model's flattened property; use a thin verified
    # profile facade for deterministic relocation tests.
    return SimpleNamespace(owner=p.owner, alliance_id=p.alliance_id, player_name=p.player_name, get_castles=lambda: [castle])


def collector():
    client = test_collector.RefreshTests().client()
    client.state.local_player.id = 333
    client.alliance = SimpleNamespace(get_local_members=lambda **kw: [member(), member(223)])
    client.player = SimpleNamespace(get_player_info=lambda pid, **kw: profile(pid))
    client.defense = SimpleNamespace(get_support_defense_info=lambda *a, **kw: GetSupportDefenseResponse.model_validate({'S': [[[1, 20], [2, 3], [999, 4]]], 'UWL': 100, 'UYL': 200, 'AUYL': 50, 'gui': {'I': [[555, 999]]}, 'gli': {}}))
    # Keep existing failure/reconnect fixtures exercising the same SDI request
    # through the new public packet API. No game connection is constructed.
    client.request_packet = lambda req, command, timeout: SimpleNamespace(error_code=0, payload=
        client.defense.get_support_defense_info(req.target_x, req.target_y, req.source_x, req.source_y, timeout=timeout)
        .model_dump(by_alias=True, exclude_unset=True))
    c = Collector(replace(settings(), game_commands_enabled=True, defense_lookup_enabled=True, health_port=0), client, transport=SimpleNamespace(post=lambda *a: {'result': 'accepted'}))
    c.metadata = ({1}, {2})
    c.refresh_members(); c.refresh()
    return c


def lookup_request(pid=222):
    return {'schema_version': 2, 'server_id': 'test-cz1', 'alliance_id': 444, 'player_id': pid, 'request_id': str(uuid4())}


class MemberAndStateTests(unittest.TestCase):
    def test_missing_ami_missing_activity_unknown_enum_and_legitimate_online_zero(self):
        m = member(); m._member_info = None
        self.assertEqual(member_item(m)['online_state'], 'unknown')
        self.assertEqual(member_item(member(activity=None))['online_state'], 'unknown')
        for value in [0, 1, 4, 99]:
            self.assertEqual(member_item(member(activity=value))['online_state'], {0:'online', 1:'offline', 4:'offline', 99:'unknown'}[value])
        m = member(); m._member_info = AllianceMemberInfo.model_validate([222, 0, 0, 0])
        self.assertEqual(member_item(m)['online_state'], 'unknown')

    def test_failed_member_read_and_failed_movements_preserve_independent_observations(self):
        c = collector(); previous = c.members_snapshot(); attacks = c.publisher.sections['attacks']
        def fail(**kw): raise TimeoutError()
        c.client.alliance.get_local_members = fail
        self.assertFalse(c.refresh_members()); self.assertEqual(c.members_snapshot(), previous)
        self.assertTrue(c.refresh()); self.assertTrue(c.health()['ready'])
        c.client.movements.get_movements = fail
        self.assertFalse(c.refresh()); self.assertEqual(c.publisher.sections['attacks']['items'], attacks['items'])
        self.assertEqual(c.members_snapshot(), previous)
        c.client.movements.get_movements = lambda **kw: []
        with patch.object(c.publisher, 'update', side_effect=ValueError()):
            self.assertTrue(c.refresh())
        self.assertTrue(c.health()['ready']); self.assertFalse(c.fatal)

    def test_coalescing_truncation_retry_and_shutdown_are_bounded(self):
        publisher = SnapshotPublisher(settings())
        for i in range(100): publisher.update('members', section([{'player_id': i}], 250, int(time.time())))
        last = publisher.take(); self.assertEqual(last['sequence'], 100); self.assertIsNone(publisher.take())
        publisher.update('attacks', section([], 500, int(time.time())))
        publisher.retry(last); self.assertEqual(publisher.take()['sequence'], 101)
        truncated = section(list(range(251)), 250, 1000)
        self.assertFalse(truncated['complete']); self.assertTrue(truncated['truncated']); self.assertEqual(truncated['count'], 250)
        publisher.retry(last); publisher.clear(); self.assertIsNone(publisher.take())
        worst = [{'name': '😀'*200} for _ in range(250)]
        publisher.update('members', section(worst, 250, int(time.time())))
        worst_attack = [{'attacker_name': '😀'*200, 'attacker_alliance': '😀'*200, 'defender_name': '😀'*200, 'target_name': '😀'*200} for _ in range(500)]
        publisher.update('attacks', section(worst_attack, 500, int(time.time())))
        bounded = publisher.take()
        self.assertLessEqual(len(json.dumps(bounded, ensure_ascii=False).encode()), 1_048_576)
        self.assertTrue(bounded['attacks']['truncated']); self.assertFalse(bounded['attacks']['complete'])
        previous = publisher.sequence
        with self.assertRaises(UnicodeEncodeError): publisher.update('members', section([{'name': '\ud800'}], 250, int(time.time())))
        self.assertEqual(publisher.sequence, previous)
        self.assertEqual(publisher.sections['members']['items'], worst)

    def test_current_overview_retains_elapsed_attack_without_alerting_it_again(self):
        c = collector(); old = movement(last_updated=time.time()-700)
        c.client.state.get_announced_attacks = lambda: [old]
        self.assertTrue(c.refresh())
        self.assertEqual(c.publisher.sections['attacks']['count'], 1)
        self.assertLess(c.publisher.sections['attacks']['items'][0]['arrival_at'], time.time())
        self.assertEqual(len(c.queue), 0)
        c.client.state.get_announced_attacks = lambda: []
        c.refresh(); self.assertEqual(c.publisher.sections['attacks']['count'], 0)

    def test_snapshot_delivery_failure_never_marks_monitor_fatal_or_blocks_heartbeat(self):
        c = collector(); paths = []
        def post(path, payload):
            paths.append(path)
            if path == '/v2/state':
                c.stop.set(); raise DeliveryError('400', permanent=True)
        c.transport.post = post
        c.state_delivery_loop(); self.assertFalse(c.fatal)
        self.assertIsNotNone(c.publisher.take())
        c.stop.clear(); c.transport.post = lambda path, payload: (paths.append(path), c.stop.set())
        c.heartbeat_loop(); self.assertIn('/v1/heartbeat', paths)


class DefenseTests(unittest.TestCase):
    def test_model_defaults_and_explicit_empty_positions_remain_distinct_and_own_data_is_excluded(self):
        missing = normalize_defense(GetSupportDefenseResponse(), {}, None, 1000)
        self.assertEqual(missing['quality'], 'unavailable'); self.assertIsNone(missing['positions'])
        self.assertTrue(all(v is None for v in missing['capacities'].values()))
        explicit = normalize_defense(GetSupportDefenseResponse.model_validate({'S': [], 'UWL': 0}), {}, None, 1000)
        self.assertEqual(explicit['positions'], []); self.assertEqual(explicit['capacities']['wall'], 0)
        c = collector(); result = c.defense.lookup(lookup_request())
        self.assertEqual(result['positions'][0], [{'id': 1, 'count': 20, 'kind': 'troop'}, {'id': 2, 'count': 3, 'kind': 'tool'}, {'id': 999, 'count': 4, 'kind': 'unknown'}])
        self.assertEqual(result['quality'], 'partial')
        self.assertNotIn('gui', json.dumps(result)); self.assertNotIn('gli', json.dumps(result)); self.assertNotIn('SCID', json.dumps(result))

    def test_membership_owner_world_and_main_castle_restrictions(self):
        for pid in [333, 999]:
            c = collector()
            with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request(pid))
            self.assertEqual(e.exception.status, 422)
        for change in ['alliance', 'owner', 'outpost', 'world', 'missing', 'ambiguous']:
            c = collector()
            original = c.client.player.get_player_info
            def changed(pid, **kw):
                p = original(pid)
                if pid == 222:
                    if change == 'alliance': p.alliance_id = 999
                    elif change == 'owner': p.owner = PlayerOwnerInfo(OID=999, AID=444)
                    else:
                        castle = p.get_castles()[0]
                        if change == 'outpost': castle.castle_type = MapItemType.OUTPOST
                        if change == 'world': castle.kingdom_id = Kingdom.ICE
                        p.get_castles = lambda: [] if change == 'missing' else [castle, castle] if change == 'ambiguous' else [castle]
                return p
            c.client.player.get_player_info = changed
            with self.subTest(change=change), self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request())
            self.assertEqual(e.exception.status, 422)

    def test_changed_profile_after_sdi_rejects_relocation_and_membership_changes(self):
        for change in ['move', 'alliance']:
            c = collector(); calls = []
            def changed(pid, **kw):
                p = profile(pid); calls.append(pid)
                if pid == 222 and calls.count(222) > 1:
                    if change == 'move': p.get_castles()[0].x += 1
                    else: p.alliance_id = 999
                return p
            c.client.player.get_player_info = changed
            with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request())
            self.assertEqual(e.exception.status, 422)
            self.assertFalse(c.defense.cache); self.assertTrue(c.health()['ready'])

    def test_sdi_timeout_quarantines_late_replies_until_a_confirmed_natural_new_session(self):
        c = collector(); calls = []
        def timeout(*a, **kw): calls.append(a); raise TimeoutError()
        c.client.defense.get_support_defense_info = timeout
        with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request())
        self.assertEqual(e.exception.status, 504); self.assertTrue(c.defense.poisoned)
        c.defense.last_start = -1000
        with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request(223))
        self.assertEqual(e.exception.status, 503); self.assertEqual(len(calls), 1)
        # A late packet has no waiter. Repeated movement successes are the SAME
        # session and must not unlock SDI.
        c.refresh(); self.assertTrue(c.defense.poisoned)
        c.disconnected(); c.client.is_logged_in = True
        c.refresh_members(); c.refresh()
        c.client.defense.get_support_defense_info = lambda *a, **kw: GetSupportDefenseResponse(S=[], SCID=9999)
        result = c.defense.lookup(lookup_request(223))
        self.assertEqual(result['target']['player_id'], 223); self.assertEqual(result['positions'], [])
        self.assertTrue(c.health()['ready'])

    def test_http_deadline_discards_late_sdi_success_and_keeps_the_worker_bounded(self):
        c = collector(); entered, release = Event(), Event()
        def delayed(*a, **kw):
            entered.set(); release.wait(2)
            return GetSupportDefenseResponse(S=[[[1, 99]]])
        c.client.defense.get_support_defense_info = delayed
        class ShortWait(Event):
            def wait(self, timeout=None): return super().wait(min(timeout or 0.02, 0.02))
        try:
            with patch('monitor.defense.Event', ShortWait):
                with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request())
            self.assertEqual(e.exception.status, 504); self.assertTrue(entered.is_set())
            self.assertIsNotNone(c.defense.active); self.assertTrue(c.defense.poisoned)
            with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request(223))
            self.assertEqual(e.exception.status, 503)
            self.assertTrue(c.refresh()); self.assertTrue(c.health()['ready'])
            release.set(); c.defense.thread.join(1)
            self.assertFalse(c.defense.cache); self.assertIsNone(c.defense.active)
            self.assertTrue(c.defense.poisoned)
        finally:
            release.set(); c.defense.close()

    def test_busy_worker_does_not_block_callbacks_movement_refresh_heartbeat_or_new_generation_guard(self):
        c = collector(); entered, release = Event(), Event(); results = []
        def delayed(*a, **kw): entered.set(); release.wait(3); return GetSupportDefenseResponse(S=[[[1, 9]]])
        c.client.defense.get_support_defense_info = delayed
        def run():
            try: results.append(c.defense.lookup(lookup_request()))
            except LookupError as exc: results.append(exc)
        worker = Thread(target=run); worker.start(); self.assertTrue(entered.wait(1))
        self.assertIsNotNone(c.enqueue(movement(movement_id=777, last_updated=time.time())))
        self.assertTrue(c.refresh()); self.assertEqual(c.heartbeat()['session'], 'connected')
        with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request(223))
        self.assertEqual(e.exception.status, 429)
        c.disconnected(); c.refresh_members(); c.refresh()
        release.set(); worker.join(2); self.assertFalse(worker.is_alive())
        self.assertIsInstance(results[0], LookupError); self.assertEqual(results[0].status, 503)
        self.assertFalse(c.defense.cache)
        # An old request may have sent during a reconnect race. Conservatively
        # quarantine that new generation too; a third session recovers it.
        self.assertTrue(c.defense.poisoned)
        c.refresh(); self.assertTrue(c.defense.poisoned)
        c.disconnected(); c.refresh_members(); c.refresh(); self.assertFalse(c.defense.poisoned)

    def test_cache_cooldown_expiry_bounds_and_parser_failure_are_isolated(self):
        c = collector(); result = c.defense.lookup(lookup_request()); req = lookup_request()
        cached = c.defense.lookup(req); self.assertEqual(cached['observed_at'], result['observed_at']); self.assertEqual(cached['request_id'], req['request_id'])
        with self.assertRaises(LookupError) as e: c.defense.lookup(lookup_request(223))
        self.assertEqual(e.exception.status, 429)
        c.defense.cache.clear(); c.defense.last_start = -1000
        with patch('monitor.defense.normalize_defense', side_effect=ValueError()):
            with self.assertRaises(LookupError): c.defense.lookup(lookup_request())
        self.assertFalse(c.defense.cache); self.assertTrue(c.health()['ready']); self.assertTrue(c.defense.poisoned)
        # Populate more than 100 targets over a fake clock; validate worker
        # eviction rather than merely inspecting a declared constant.
        c = collector(); c.members = section([member_item(member(i)) for i in range(1, 152)], 250, int(time.time()))
        c.defense.monotonic = lambda: time.monotonic() + c.defense.clock_offset
        c.defense.clock_offset = 0
        for i in range(1, 102):
            c.defense.clock_offset += 6; c.defense.lookup(lookup_request(i))
        self.assertEqual(len(c.defense.cache), 100); self.assertNotIn(1, c.defense.cache)
        c.defense.clock = lambda: time.time() + 31
        c.defense.clock_offset += 6; c.defense.lookup(lookup_request(102))
        self.assertEqual(len(c.defense.cache), 1)
        c.defense.close(); self.assertFalse(c.defense.cache)

    def test_core_defense_command_through_http_to_fake_game_and_mock_discord(self):
        c = collector(); c.start_health()
        try:
            origin = 'http://127.0.0.1:' + str(c.health_server.server_port)
            result = subprocess.run(['node', str(Path(__file__).with_name('defense-harness.mjs')), origin],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            reply = json.loads(result.stdout)
            self.assertEqual(reply['allowedMentions']['parse'], [])
            self.assertIn('partial', reply['embeds'][0]['description'])
            self.assertIn('ID 999: 4 (unknown)', reply['embeds'][0]['fields'][0]['value'])
        finally:
            c.stop.set(); c.defense.close(); c.health_server.shutdown(); c.health_server.server_close()

    def test_collector_run_and_shutdown_with_independent_workers_and_no_real_credentials(self):
        c = collector(); c.client.is_logged_in = False
        callbacks = {}; logged_in, polled = Event(), Event(); delivered = []
        c.client.state.on_incoming_attack = lambda cb: callbacks.update(attack=cb)
        c.client.state.on_incoming_attack_updated = lambda cb: callbacks.update(update=cb)
        c.client.on_disconnect = lambda cb: callbacks.update(disconnect=cb)
        c.client.on_session_lost = lambda cb: callbacks.update(lost=cb)
        c.client.close = lambda: setattr(c.client, 'is_logged_in', False)
        def login(**kw): c.client.is_logged_in = True; logged_in.set()
        c.client.login = login
        c.client.movements.get_movements = lambda **kw: polled.set()
        c.transport.post = lambda path, body: delivered.append(path)
        results = []
        runner = Thread(target=lambda: results.append(c.run()))
        runner.start()
        self.assertTrue(logged_in.wait(1)); self.assertTrue(polled.wait(1))
        callbacks['attack'](movement(last_updated=time.time()))
        c.stop.set(); c.client.close(); runner.join(3)
        self.assertFalse(runner.is_alive()); self.assertEqual(results, [0])
        self.assertIn('/v1/heartbeat', delivered)
        self.assertEqual(c.session, 'stopped'); self.assertFalse(c.defense.cache)

    def test_http_endpoint_auth_flags_schema_body_and_normalized_output(self):
        c = collector(); c.start_health()
        origin = 'http://127.0.0.1:' + str(c.health_server.server_port)
        def post(value, secret=None):
            req = request.Request(origin+'/v2/defense', data=json.dumps(value).encode(), headers={'Authorization': 'Bearer ' + (c.settings.secret if secret is None else secret), 'Content-Type': 'application/json'})
            try:
                with request.urlopen(req, timeout=3) as response: return response.status, json.load(response)
            except error.HTTPError as response:
                with response: return response.code, json.load(response)
        try:
            self.assertEqual(post(lookup_request(), 'bad')[0], 401)
            self.assertEqual(post({**lookup_request(), 'x': 1})[0], 400)
            self.assertEqual(post({**lookup_request(), 'schema_version': 2.0})[0], 400)
            self.assertEqual(post({'padding': 'x'*8192})[0], 400)
            c.settings = replace(c.settings, defense_lookup_enabled=False)
            self.assertEqual(post(lookup_request())[0], 503)
            c.settings = replace(c.settings, defense_lookup_enabled=True)
            code, result = post(lookup_request()); self.assertEqual(code, 200); self.assertEqual(result['target']['player_id'], 222)
            self.assertEqual(post(lookup_request(999))[0], 422)
            with request.urlopen(origin+'/readyz') as response: self.assertTrue(json.load(response)['ready'])
        finally:
            c.stop.set(); c.defense.close(); c.health_server.shutdown(); c.health_server.server_close()
