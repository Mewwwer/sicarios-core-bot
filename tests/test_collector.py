from pathlib import Path
from types import SimpleNamespace
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
from monitor.config import Settings
from monitor.normalize import normalize_attack
from monitor.transport import MemoryDeliveryQueue, CoreTransport, DeliveryError
from monitor.service import Collector
from empire_core import Movement
from empire_core.movements.models import MovementOwner


def movement(**overrides):
    fields = dict(movement_id=123, movement_type=0, owner_id=111, target_id=222, local_player_id=333,
                  target_owner=MovementOwner(player_id=222, alliance_id=444),
                  source_player_name='Útočník', target_player_name='Obránce', target_name='Hrad',
                  total_time=600, progress_time=0, last_updated=1000, target_x=790, target_y=804,
                  target_area_id=555, estimated_size=416843)
    fields.update(overrides)
    return Movement(**fields)


def settings():
    return Settings('test-cz1', 'wss://example.invalid/', 'EmpireEx_test', 444, 'fake', 'fake-password',
                    'http://127.0.0.1:8080', 'test-secret-at-least-32-characters')


class NormalizationTests(unittest.TestCase):
    def test_alliance_target_is_included_despite_not_being_personal_incoming(self):
        attack = movement()
        self.assertFalse(attack.is_incoming)
        payload = normalize_attack(attack, 'test-cz1', 444, now=1000)
        self.assertEqual(payload['defender_id'], 222)
        self.assertEqual(payload['arrival_at'], 1600)
        self.assertEqual(payload['troops'], {'value': 416843, 'accuracy': 'estimated'})

    def test_support_transport_outgoing_return_and_other_alliance_are_excluded(self):
        for overrides in [dict(movement_type=1), dict(movement_type=4), dict(owner_id=333), dict(direction=1),
                          dict(target_owner=MovementOwner(player_id=222, alliance_id=999))]:
            with self.subTest(overrides=overrides):
                self.assertIsNone(normalize_attack(movement(**overrides), 'test-cz1', 444, now=1000))

    def test_personal_attack_can_have_a_missing_target_owner_record(self):
        result = normalize_attack(movement(target_id=333, target_owner=None), 'test-cz1', 444, now=1000)
        self.assertIsNotNone(result)

    def test_no_metadata_does_not_count_tools_as_troops(self):
        attack = movement(units={1: 100, 2: 10}, estimated_size=0)
        result = normalize_attack(attack, 'test-cz1', 444, now=1000)
        self.assertEqual(result['troops'], {'value': None, 'accuracy': 'unknown'})
        result = normalize_attack(attack, 'test-cz1', 444, metadata=({1}, {2}), now=1000)
        self.assertEqual(result['troops']['value'], 100)
        self.assertEqual(result['tools']['value'], 10)
        self.assertEqual(result['troops']['accuracy'], 'exact')

    def test_unclassified_units_do_not_produce_a_falsely_exact_count(self):
        result = normalize_attack(movement(units={1: 100, 99: 100}, estimated_size=0), 'test-cz1', 444,
                                  metadata=({1}, {2}), now=1000)
        self.assertEqual(result['troops']['accuracy'], 'unknown')

    def test_default_fields_remain_unknown_and_expired_movements_are_skipped(self):
        result = normalize_attack(movement(target_x=-1, target_y=-1, total_time=0), 'test-cz1', 444, now=1000)
        self.assertIsNone(result['target_x'])
        self.assertIsNone(result['arrival_at'])
        self.assertIsNone(normalize_attack(movement(), 'test-cz1', 444, now=1601))
        self.assertIsNone(normalize_attack(movement(movement_id=-1), 'test-cz1', 444, now=1000))


class QueueTests(unittest.TestCase):
    def payload(self, **overrides):
        value = normalize_attack(movement(), 'test-cz1', 444, now=1000)
        value.update(overrides)
        return value

    def test_retries_update_the_same_pending_item_and_success_clears_it(self):
        queue = MemoryDeliveryQueue()
        payload = self.payload()
        self.assertTrue(queue.submit(payload, now=1000))
        key, _ = queue.take(now=1000)
        queue.retry(key, retry_after=5, now=1000)
        self.assertIsNone(queue.take(now=1004))
        queue.submit(self.payload(observed_at=1004), now=1004)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue.take(now=1005)[1]['observed_at'], 1004)
        queue.done(key)
        self.assertEqual(len(queue), 0)

    def test_capacity_is_bounded_and_expired_alerts_are_not_delivered(self):
        queue = MemoryDeliveryQueue(capacity=1)
        self.assertTrue(queue.submit(self.payload(), now=1000))
        self.assertFalse(queue.submit(self.payload(movement_id=124), now=1000))
        self.assertIsNone(queue.take(now=1121))
        self.assertTrue(queue.submit(self.payload(movement_id=124, observed_at=1121), now=1121))
        self.assertIsNone(queue.take(now=1601))

    def test_successful_snapshot_can_drop_a_removed_attack(self):
        queue = MemoryDeliveryQueue()
        queue.submit(self.payload(), now=1000)
        queue.retain(set())
        self.assertEqual(len(queue), 0)


class RefreshTests(unittest.TestCase):
    def client(self):
        attack = movement(last_updated=time.time())
        state = SimpleNamespace(local_player=SimpleNamespace(alliance=SimpleNamespace(id=444)),
                                get_announced_attacks=lambda: [attack])
        # Any access to attack/defense/rewards fails: only this read is provided.
        return SimpleNamespace(is_logged_in=True, state=state,
                               movements=SimpleNamespace(get_movements=lambda **_kwargs: []))

    def test_reads_announced_alliance_attacks_and_requeues_after_core_restart(self):
        collector = Collector(settings(), self.client(), transport=object())
        self.assertTrue(collector.refresh())
        key, _payload = collector.queue.take()
        collector.queue.done(key)
        self.assertTrue(collector.refresh())
        self.assertEqual(len(collector.queue), 1)
        self.assertEqual(collector.heartbeat()['session'], 'connected')

    def test_failed_snapshot_preserves_last_success_timestamp_and_pending_alerts(self):
        client = self.client()
        collector = Collector(settings(), client, transport=object())
        collector.refresh()
        previous = collector.last_snapshot
        def fail(**_kwargs):
            raise TimeoutError('simulated')
        client.movements.get_movements = fail
        self.assertFalse(collector.refresh())
        self.assertEqual(previous, collector.last_snapshot)
        self.assertEqual(len(collector.queue), 1)

    def test_wrong_alliance_stops_collection(self):
        client = self.client()
        client.state.local_player.alliance.id = 999
        collector = Collector(settings(), client, transport=object())
        self.assertFalse(collector.refresh())
        self.assertTrue(collector.stop.is_set())
        self.assertEqual(len(collector.queue), 0)

    def test_snapshot_does_not_drop_or_overwrite_newer_callback_attacks(self):
        client = self.client()
        collector = Collector(settings(), client, transport=object())
        old = movement(last_updated=time.time(), estimated_size=100)
        def snapshot_with_concurrent_callbacks():
            # The game state list was read; callbacks arrive before refresh
            # finishes normalizing it and removing missing queue entries.
            collector.enqueue(movement(last_updated=time.time(), estimated_size=200))
            collector.enqueue(movement(movement_id=124, last_updated=time.time()))
            return [old]
        client.state.get_announced_attacks = snapshot_with_concurrent_callbacks
        self.assertTrue(collector.refresh())
        self.assertEqual(len(collector.queue), 2)
        key, payload = collector.queue.take()
        self.assertEqual(payload['troops']['value'], 200)
        collector.queue.done(key)
        self.assertEqual(collector.queue.take()[1]['movement_id'], 124)
        # A later successful snapshot can still remove both withdrawn attacks.
        client.state.get_announced_attacks = lambda: []
        self.assertTrue(collector.refresh())
        self.assertEqual(len(collector.queue), 0)


class ConfigTests(unittest.TestCase):
    def test_missing_server_or_secrets_fail_without_disclosing_values(self):
        with self.assertRaisesRegex(ValueError, 'Missing GGE_SERVER_ID'):
            Settings.from_env({})
        self.assertNotIn('fake-password', repr(settings()))
        self.assertNotIn('test-secret', repr(settings()))


if __name__ == '__main__':
    unittest.main()
