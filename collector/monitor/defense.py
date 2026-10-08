"""Bounded read-only SDI worker. Never called from an Empire receive callback."""
from collections import OrderedDict
from threading import Event, Lock, Thread
from uuid import UUID
import copy
import time
from .normalize import identifier, name
from . import defense_diagnostics as diagnostics


class LookupError(Exception):
    def __init__(self, status, code):
        super().__init__(code)
        self.status, self.code = status, code


def present(model, field):
    return field in getattr(model, 'model_fields_set', set())


def normalize_defense(reply, target, metadata, observed, raw=None):
    capacities = {}
    for key, field, alias in [('wall', 'wall_limit', 'UWL'), ('yard', 'yard_limit', 'UYL'), ('alliance', 'available_yard_limit', 'AUYL')]:
        value = raw.get(alias) if raw is not None else getattr(reply, field) if present(reply, field) else None
        capacities[key] = identifier(value)
    total, alliance = capacities['yard'], capacities['alliance']
    capacities['courtyard'] = total - alliance if total is not None and alliance is not None and total >= alliance else None
    # AS is not a declared field in the pinned SDI model; extra='allow' retains
    # it. Neither receipt time nor AS proves a measurement time in the game.
    age = identifier((raw if raw is not None else reply.model_extra or {}).get('AS'))
    positions = None
    unknown = False
    if present(reply, 'defense_positions'):
        positions = []
        if len(reply.defense_positions) > 7:
            raise ValueError('Too many positions')
        for position in reply.defense_positions:
            if len(position) > 100:
                raise ValueError('Too many unit types')
            units = []
            for uid, amount in position:
                if identifier(uid) is None or identifier(amount) is None:
                    raise ValueError('Invalid units')
                kind = 'troop' if metadata is not None and uid in metadata[0] else 'tool' if metadata is not None and uid in metadata[1] else 'unknown'
                unknown |= kind == 'unknown'
                units.append({'id': uid, 'count': amount, 'kind': kind})
            positions.append(units)
    castellan = None
    if present(reply, 'castellan') and reply.castellan is not None:
        lord = reply.castellan
        castellan = {'id': identifier(lord.commander_id) if present(lord, 'commander_id') else None,
                     'name': name(lord.name) if present(lord, 'name') else None}
    fields = [positions is not None, age is not None, *[v is not None for v in capacities.values()],
              castellan is not None and castellan['id'] is not None and castellan['name'] is not None]
    quality = 'complete' if all(fields) and not unknown else 'partial' if any(fields) else 'unavailable'
    # SCID, gui, gli and tower/own castellans are deliberately not exported.
    return {'target': target, 'observed_at': observed, 'fetched_at': observed, 'source_age_seconds': age, 'quality': quality,
            'capacities': capacities, 'positions': positions, 'castellan': castellan}


def main_castle(profile, player_id):
    from empire_core.enums import Kingdom, MapItemType
    owner = profile.owner
    if owner is None or not present(owner, 'player_id') or owner.player_id != player_id:
        raise LookupError(422, 'owner_mismatch')
    castles = [c for c in profile.get_castles() if c.castle_type == MapItemType.CASTLE and c.kingdom_id == Kingdom.GREEN]
    if len(castles) != 1:
        raise LookupError(422, 'main_castle_missing_or_ambiguous')
    castle = castles[0]
    required = ['castle_id', 'owner_id', 'kingdom_id', 'castle_type', 'x', 'y']
    if any(not present(castle, f) for f in required) or castle.owner_id != player_id or not identifier(castle.castle_id) or identifier(castle.x) is None or identifier(castle.y) is None or castle.x > 1_000_000 or castle.y > 1_000_000 or castle.is_occupied:
        raise LookupError(422, 'unsupported_castle')
    return castle


class DefenseLookup:
    def __init__(self, collector, clock=time.time, monotonic=time.monotonic):
        self.collector = collector
        self.clock, self.monotonic = clock, monotonic
        self.lock = Lock()
        self.generation = 0
        self.connected = False
        self.poisoned = False
        self.blocked_generation = -1
        self.active = None
        self.last_start = -float('inf')
        self.cache = OrderedDict()
        self.thread = None
        self.diagnostic_attempts = 0

    def disconnect(self):
        with self.lock:
            self.generation += 1
            self.connected = False
            self.cache.clear()

    def confirm_session(self, expected_generation=None):
        # Only a successful movement refresh after a logged-in new session
        # confirms recovery. An old worker must finish before another SDI.
        with self.lock:
            if expected_generation is not None and expected_generation != self.generation:
                return
            if not self.collector.client.is_logged_in:
                return
            if not self.connected:
                self.connected = True
                self.poisoned = self.generation <= self.blocked_generation
                self.cache.clear()

    def validate_request(self, value):
        cfg = self.collector.settings
        if not isinstance(value, dict) or set(value) != {'schema_version', 'request_id', 'server_id', 'alliance_id', 'player_id'} or type(value['schema_version']) is not int or value['schema_version'] != 2 or identifier(value['alliance_id']) != cfg.alliance_id or value['server_id'] != cfg.server_id or value['alliance_id'] != cfg.alliance_id or not identifier(value['player_id']):
            raise LookupError(400, 'invalid_request')
        try:
            if not isinstance(value['request_id'], str) or str(UUID(value['request_id'])) != value['request_id']:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise LookupError(400, 'invalid_request') from None

    def member_allowed(self, pid):
        c = self.collector
        section = c.members_snapshot()
        if section is None or not section['complete'] or section['truncated'] or self.clock() - section['observed_at'] > 120:
            raise LookupError(503, 'member_data_unavailable')
        if not c.client.is_logged_in or not c.alliance_matches():
            raise LookupError(503, 'session_unavailable')
        if c.client.state.local_player.id == pid:
            raise LookupError(422, 'own_castle_unsupported')
        if not any(m['player_id'] == pid for m in section['items']):
            raise LookupError(422, 'not_current_member')

    def lookup(self, value):
        self.validate_request(value)
        cfg = self.collector.settings
        if not cfg.game_commands_enabled or not cfg.defense_lookup_enabled:
            raise LookupError(503, 'feature_disabled')
        pid = value['player_id']
        self.member_allowed(pid)
        with self.lock:
            now = self.clock()
            for key in list(self.cache):
                if now - self.cache[key]['observed_at'] > 30:
                    del self.cache[key]
            if not self.connected or self.poisoned or self.collector.stop.is_set():
                raise LookupError(503, 'sdi_quarantined_or_session_unavailable')
            if pid in self.cache:
                result = copy.deepcopy(self.cache[pid])
                return self.envelope(value, result)
            # Reject concurrent calls, even same target: no waiter queue.
            if self.active is not None or self.monotonic() - self.last_start < 5:
                raise LookupError(429, 'busy_or_cooldown')
            job = {'generation': self.generation, 'done': Event(), 'result': None, 'error': None}
            self.active = job
            self.last_start = self.monotonic()
            self.thread = Thread(target=self.work, args=(pid, job, value['request_id']), daemon=True)
            self.thread.start()
        if not job['done'].wait(12):
            with self.lock:
                if job['generation'] == self.generation:
                    self.poisoned = True
                    self.blocked_generation = self.generation
            raise LookupError(504, 'lookup_timeout')
        with self.lock:
            if job['generation'] != self.generation or not self.connected or self.collector.stop.is_set():
                raise LookupError(503, 'session_changed')
            if job['error']:
                raise job['error']
            result = copy.deepcopy(job['result'])
        self.member_allowed(pid)
        return self.envelope(value, result)

    def envelope(self, request, result):
        return {'schema_version': 2, 'request_id': request['request_id'],
                'server_id': request['server_id'], 'alliance_id': request['alliance_id'], **result}

    def work(self, pid, job, request_id):
        c = self.collector
        deadline = self.monotonic() + 11.5
        sdi_started = False
        diagnostic = None
        def timeout():
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise LookupError(504, 'lookup_timeout')
            return min(5, remaining)
        def check_generation():
            with self.lock:
                if job['generation'] != self.generation or not self.connected or c.stop.is_set():
                    raise LookupError(503, 'session_changed')
        def profile(player_id):
            result = c.client.player.get_player_info(player_id, timeout=timeout())
            check_generation()
            if result.owner is None or not present(result.owner, 'alliance_id') or result.alliance_id != c.settings.alliance_id:
                raise LookupError(422, 'profile_alliance_mismatch')
            return result
        try:
            target_profile = profile(pid)
            target = main_castle(target_profile, pid)
            local_id = c.client.state.local_player.id
            source = main_castle(profile(local_id), local_id)
            if (source.x, source.y) == (target.x, target.y):
                raise LookupError(422, 'own_castle_unsupported')
            check_generation()
            self.member_allowed(pid)
            sdi_started = True
            from empire_core.defense.models import GetSupportDefenseRequest, GetSupportDefenseResponse
            request = GetSupportDefenseRequest(TX=target.x, TY=target.y, SX=source.x, SY=source.y)
            if c.settings.defense_diagnostics_enabled and self.diagnostic_attempts < diagnostics.LIMIT:
                self.diagnostic_attempts += 1
                diagnostic = {'diagnostic_version': 1, 'request_id': request_id,
                    'server_id': c.settings.server_id, 'alliance_id': c.settings.alliance_id,
                    'generation': job['generation'], 'requested_at': int(self.clock()),
                    'target': {'player_id': pid, 'castle_id': target.castle_id, 'kingdom_id': 0, 'x': target.x, 'y': target.y},
                    'request_coordinates': {'TX': target.x, 'TY': target.y, 'SX': source.x, 'SY': source.y}}
            # Same session/frame/Connection.request/command lock as the service
            # method. Capture the whitelist before Pydantic's SpyPositions
            # coercion; no global packet observer can misassign a late reply.
            packet = c.client.request_packet(request, request.get_response_command(), timeout=timeout())
            observed = int(self.clock())
            if packet.error_code != 0 or not isinstance(packet.payload, dict):
                raise LookupError(503, 'lookup_failed')
            raw = packet.payload
            if diagnostic is not None:
                diagnostic.update(fetched_at=observed, wire=diagnostics.selected_fields(raw))
            # The library coerces and drops malformed S rows. Refuse them so
            # an invalid position cannot become a supposedly measured empty one.
            if 'S' in raw and (not isinstance(raw['S'], list) or len(raw['S']) > 7 or any(
                not isinstance(p, list) or len(p) > 100 or any(not isinstance(row, list) or len(row) != 2
                    or identifier(row[0]) is None or identifier(row[1]) is None for row in p) for p in raw['S'])):
                raise ValueError('Invalid SDI positions')
            reply = GetSupportDefenseResponse.model_validate({k: raw[k] for k in ('S', 'AS', 'UWL', 'UYL', 'AUYL', 'B') if k in raw})
            if diagnostic is not None:
                diagnostic['model'] = diagnostics.model_fields(reply)
            check_generation()
            # Re-read the profile to refuse a relocation or membership change
            # during lookup. No map writes or game actions are used.
            checked = main_castle(profile(pid), pid)
            if (checked.castle_id, checked.x, checked.y) != (target.castle_id, target.x, target.y):
                raise LookupError(422, 'target_moved')
            self.member_allowed(pid)
            result = normalize_defense(reply, {'player_id': pid, 'name': name(target_profile.player_name) or str(pid),
                'castle_id': target.castle_id, 'castle_name': name(target.castle_name) or str(target.castle_id),
                'x': target.x, 'y': target.y, 'kingdom_id': 0}, c.metadata, observed, raw)
            if diagnostic is not None:
                diagnostic['normalized'] = {'capacities': result['capacities'], 'source_age_seconds': result['source_age_seconds'],
                    'wall': [p[:20] for p in result['positions'][:3]] if result['positions'] is not None else None,
                    'quality': result['quality']}
            with self.lock:
                if job['generation'] == self.generation and not self.poisoned and not c.stop.is_set():
                    while len(self.cache) >= 100:
                        self.cache.popitem(last=False)
                    self.cache[pid] = result
                    job['result'] = result
                else:
                    job['error'] = LookupError(503, 'session_changed')
        except Exception as exc:
            # Any uncertain SDI failure quarantines this generation, including
            # send/network/parser failures. Serializing alone is insufficient.
            with self.lock:
                if sdi_started:
                    self.poisoned = True
                    self.blocked_generation = self.generation
                job['error'] = exc if isinstance(exc, LookupError) else LookupError(504 if isinstance(exc, TimeoutError) else 503, 'lookup_failed')
        finally:
            with self.lock:
                if self.active is job:
                    self.active = None
                job['done'].set()
            if diagnostic is not None:
                diagnostic['outcome'] = job['error'].code if job['error'] else 'accepted'
                diagnostics.emit(diagnostic)

    def close(self):
        self.disconnect()
        if self.thread:
            self.thread.join(timeout=13)
