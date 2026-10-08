"""Offline evidence for SDI → pinned model → DTO; not a live game fixture."""
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from test_game_commands import collector, lookup_request
from monitor.config import Settings
from monitor.defense import LookupError, normalize_defense
from monitor.defense_diagnostics import selected_fields
from empire_core import EmpireClient
from empire_core.defense.models import GetSupportDefenseRequest, GetSupportDefenseResponse


def packet(payload, error_code=0):
    return SimpleNamespace(payload=payload, error_code=error_code)


class DefenseEvidenceTests(unittest.TestCase):
    def test_yard_derivation_requires_explicit_consistent_fields_and_preserves_zero(self):
        for raw, expected in [({'UYL': 1029100, 'AUYL': 286100}, 743000),
                              ({'UYL': 0, 'AUYL': 0}, 0), ({'UYL': 10}, None),
                              ({'AUYL': 10}, None), ({'UYL': 9, 'AUYL': 10}, None),
                              ({'UYL': True, 'AUYL': 0}, None), ({'UYL': '10', 'AUYL': 0}, None)]:
            with self.subTest(raw=raw):
                d = normalize_defense(GetSupportDefenseResponse.model_validate(raw), {}, None, 1000, raw)
                self.assertEqual(d['capacities']['courtyard'], expected)
                if expected is None: self.assertNotEqual(d['quality'], 'complete')

    def test_source_as_is_retained_as_extra_and_receipt_never_becomes_measurement_time(self):
        for value in [None, 0, 50, -1, '50', True, 1.5]:
            raw = {} if value is None else {'AS': value}
            reply = GetSupportDefenseResponse.model_validate(raw)
            self.assertNotIn('AS', GetSupportDefenseResponse.model_fields)
            self.assertEqual(reply.model_extra, raw)
            d = normalize_defense(reply, {}, None, 1000)
            self.assertEqual(d['fetched_at'], 1000)
            self.assertEqual(d['source_age_seconds'], value if type(value) is int and value >= 0 else None)
            self.assertNotIn('measured_at', d)

    def test_reported_counts_pass_through_without_correction_or_tool_summing(self):
        raw = {'S': [[[489, 744], [227, 2894], [238, 1213]],
                     [[489, 759], [227, 2953], [238, 3565]], [[2, 99]]],
               'UWL': 8998, 'UYL': 1029100, 'AUYL': 286100, 'AS': 50}
        d = normalize_defense(GetSupportDefenseResponse.model_validate(raw), {}, ({489, 227, 238}, {2}), 1000)
        self.assertEqual(d['capacities']['wall'], 8998)
        self.assertEqual([[u['count'] for u in p] for p in d['positions']], [[744, 2894, 1213], [759, 2953, 3565], [99]])
        self.assertEqual(sum(u['count'] for p in d['positions'][:3] for u in p if u['kind'] == 'troop'), 12128)
        self.assertEqual(d['positions'][2][0]['kind'], 'tool')

    def test_pinned_packet_api_uses_the_same_session_frame_and_waiter_check(self):
        # Real library methods over a fake connection, without constructing a
        # logged-in client. No observer, alternative session or network access.
        client = EmpireClient.__new__(EmpireClient)
        client.config = SimpleNamespace(default_zone='offline-zone')
        calls = []
        client.connection = SimpleNamespace(room_id=7, request=lambda *a, **kw: (calls.append((a, kw)), packet({'S': [], 'AS': 50}))[1])
        req = GetSupportDefenseRequest(TX=574, TY=528, SX=10, SY=20)
        result = client.request_packet(req, req.get_response_command(), timeout=2)
        self.assertEqual(calls[0], ((client.frame(req), 'sdi'), {'timeout': 2, 'accepts': None}))
        self.assertEqual(GetSupportDefenseResponse.model_validate(result.payload).model_extra['AS'], 50)
        calls.clear()
        typed = client.request(req, GetSupportDefenseResponse, timeout=2)
        self.assertEqual(calls[0], ((client.frame(req), 'sdi'), {'timeout': 2, 'accepts': None}))
        self.assertEqual(typed.defense_positions, [])

    def test_diagnostics_default_off_and_config_requires_defense(self):
        c = collector()
        with patch('monitor.defense.diagnostics.emit') as emit:
            c.defense.lookup(lookup_request()); c.defense.thread.join(1)
        emit.assert_not_called()
        self.assertFalse(c.settings.defense_diagnostics_enabled)
        env = {'GGE_SERVER_ID': 'offline', 'GGE_GAME_URL': 'wss://example.invalid/',
               'ATTACK_CORE_URL': 'http://127.0.0.1', 'ATTACK_SHARED_SECRET': 'x'*32,
               'DEFENSE_DIAGNOSTICS_ENABLED': 'true'}
        with self.assertRaisesRegex(ValueError, 'requires DEFENSE_LOOKUP_ENABLED'): Settings.from_env(env)

    def test_bounded_diagnostic_whitelist_context_stages_cache_and_attack_refresh(self):
        c = collector(); c.settings = replace(c.settings, defense_diagnostics_enabled=True)
        raw = {'S': [[[1, 20], [2, 3], [999, 4]], [[1, 7]], []],
               'UWL': 100, 'UYL': 200, 'AUYL': 50, 'AS': 50,
               'gui': {'I': 'forbidden-gui'}, 'gli': 'forbidden-gli',
               'SCID': 987, 'B': {}, 'secret': 'forbidden-secret', 'credentials': 'forbidden-password'}
        c.client.request_packet = lambda *a, **kw: packet(raw)
        with patch('monitor.defense.diagnostics.emit') as emit:
            for _ in range(7):
                c.defense.cache.clear(); c.defense.last_start = -float('inf')
                req = lookup_request(); result = c.defense.lookup(req); c.defense.thread.join(1)
                c.defense.lookup(lookup_request())  # Cache hit: no packet, no new record.
                self.assertTrue(c.refresh()); self.assertTrue(c.health()['ready'])
            self.assertEqual(emit.call_count, 5)
        d = emit.call_args_list[0].args[0]
        self.assertEqual(d['outcome'], 'accepted')
        self.assertEqual(d['target']['player_id'], 222)
        self.assertEqual(d['request_coordinates'], {'TX': 222, 'TY': 20, 'SX': 333, 'SY': 20})
        self.assertEqual(d['wire'], d['model'])
        self.assertEqual(d['normalized']['wall'][0], result['positions'][0])
        self.assertEqual(d['normalized']['capacities']['courtyard'], 150)
        text = json.dumps(d)
        for forbidden in ['gui', 'gli', 'SCID', 'forbidden-', c.settings.secret, c.settings.password]:
            self.assertNotIn(forbidden, text)
        self.assertLess(len(text), 16_384)

    def test_invalid_wire_cannot_become_empty_measured_positions_and_quarantines(self):
        for positions in [None, [None], [[['1', '20']]], [[[1, True]]], [[{'secret': 'never-log'}]]]:
            c = collector(); c.settings = replace(c.settings, defense_diagnostics_enabled=True)
            c.client.request_packet = lambda *a, **kw: packet({'S': positions})
            with patch('monitor.defense.diagnostics.emit') as emit:
                with self.assertRaises(LookupError): c.defense.lookup(lookup_request())
                c.defense.thread.join(1)
            self.assertTrue(c.defense.poisoned); self.assertFalse(c.defense.cache)
            self.assertTrue(c.refresh()); self.assertTrue(c.health()['ready'])
            self.assertEqual(emit.call_args.args[0]['outcome'], 'lookup_failed')
            self.assertNotIn('never-log', json.dumps(emit.call_args.args[0]))

    def test_projection_limits_and_log_failure_never_changes_accepted_result(self):
        selected = selected_fields({'S': [[[i, 1] for i in range(100)]]*7, 'AS': {'secret': 'never-log'}})
        self.assertEqual(len(selected['S']['wall']), 3)
        self.assertTrue(selected['S']['wall'][0]['truncated'])
        self.assertEqual(len(selected['S']['wall'][0]['pairs']), 20)
        self.assertNotIn('never-log', json.dumps(selected))
        c = collector(); c.settings = replace(c.settings, defense_diagnostics_enabled=True)
        with patch('monitor.defense_diagnostics.LOG.info', side_effect=OSError()):
            result = c.defense.lookup(lookup_request()); c.defense.thread.join(1)
        self.assertEqual(result['target']['player_id'], 222)
        self.assertFalse(c.defense.poisoned); self.assertTrue(c.health()['ready'])

    def test_error_reply_is_quarantined_without_logging_its_payload(self):
        c = collector(); c.settings = replace(c.settings, defense_diagnostics_enabled=True)
        c.client.request_packet = lambda *a, **kw: packet({'credentials': 'never-log'}, error_code=92)
        with patch('monitor.defense.diagnostics.emit') as emit:
            with self.assertRaises(LookupError): c.defense.lookup(lookup_request())
            c.defense.thread.join(1)
        self.assertTrue(c.defense.poisoned)
        self.assertNotIn('never-log', json.dumps(emit.call_args.args[0]))

    def test_diagnostic_after_reconnect_is_discarded_and_cannot_unlock_sdi(self):
        c = collector(); c.settings = replace(c.settings, defense_diagnostics_enabled=True)
        def crossing(*a, **kw):
            c.disconnected(); c.refresh_members(); c.refresh()
            return packet({'S': [[[1, 99]]], 'AS': 50})
        c.client.request_packet = crossing
        with patch('monitor.defense.diagnostics.emit') as emit:
            with self.assertRaises(LookupError): c.defense.lookup(lookup_request())
            c.defense.thread.join(1)
        d = emit.call_args.args[0]
        self.assertEqual(d['outcome'], 'session_changed')
        self.assertNotIn('normalized', d)
        self.assertTrue(c.defense.poisoned); self.assertFalse(c.defense.cache)
        self.assertTrue(c.refresh()); self.assertTrue(c.health()['ready'])
