from pathlib import Path
from types import SimpleNamespace
from urllib.request import urlopen
import json
import os
from queue import Queue, Empty
from threading import Thread
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
from monitor.normalize import normalize_attack
from monitor.transport import CoreTransport, DeliveryError
from test_collector import movement


class ProtocolIntegrationTests(unittest.TestCase):
    def test_python_normalization_transport_and_node_receiver_with_retry(self):
        secret = 'local-test-secret-at-least-32-characters'
        env = {**os.environ, 'ATTACK_SHARED_SECRET': secret, 'TEST_FAIL_FIRST': 'true'}
        harness = Path(__file__).with_name('receiver-harness.mjs')
        child = subprocess.Popen(['node', str(harness)], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            ready = Queue()
            Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True).start()
            try:
                line = ready.get(timeout=5)
            except Empty:
                self.fail('Local receiver did not start')
            origin = json.loads(line)['origin']
            transport = CoreTransport(SimpleNamespace(core_url=origin, secret=secret, http_timeout=2))
            payload = normalize_attack(movement(last_updated=time.time()), 'test-cz1', 444)
            with self.assertRaises(DeliveryError) as rejected:
                transport.post('/v1/attacks', payload)
            self.assertFalse(rejected.exception.permanent)
            self.assertEqual(rejected.exception.code, '503')
            self.assertEqual(transport.post('/v1/attacks', payload)['result'], 'sent_dry_run')
            self.assertEqual(transport.post('/v1/attacks', payload)['result'], 'duplicate')
            other_world = {**payload, 'kingdom_id': 2}
            self.assertEqual(transport.post('/v1/attacks', other_world)['result'], 'sent_dry_run')
            now = int(time.time())
            transport.post('/v1/heartbeat', {'schema_version': 1, 'server_id': 'test-cz1',
                                            'observed_at': now, 'last_snapshot_at': now, 'session': 'connected'})
            with urlopen(origin + '/readyz') as response:
                self.assertTrue(json.load(response)['ready'])
            wrong = CoreTransport(SimpleNamespace(core_url=origin, secret='wrong-secret', http_timeout=2))
            with self.assertRaises(DeliveryError) as rejected:
                wrong.post('/v1/attacks', payload)
            self.assertTrue(rejected.exception.permanent)
            self.assertEqual(rejected.exception.code, '401')
            output, errors = child.communicate(input='close\n', timeout=5)
            self.assertEqual(child.returncode, 0, errors)
            report = json.loads(output.strip())
            self.assertEqual(report['attackCount'], 2)
            self.assertEqual(report['state']['attacks']['status'], 'missing')
            self.assertIn('Waiting for complete v0.2 data', report['overview']['content'])
            self.assertEqual(report['messages'][0]['troops']['accuracy'], 'estimated')
            self.assertEqual(report['messages'][0]['defender_name'], 'Obránce')
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=5)


    def test_fake_collector_v2_snapshots_to_http_node_and_mock_discord(self):
        from dataclasses import replace
        from test_game_commands import collector
        secret = 'local-test-secret-at-least-32-characters'
        child = subprocess.Popen(['node', str(Path(__file__).with_name('receiver-harness.mjs'))],
                                 env={**os.environ, 'ATTACK_SHARED_SECRET': secret}, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            ready = Queue()
            Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True).start()
            origin = json.loads(ready.get(timeout=5))['origin']
            c = collector()
            c.settings = replace(c.settings, core_url=origin, secret=secret)
            transport = CoreTransport(c.settings)
            snapshot = c.publisher.take()
            self.assertEqual(transport.post('/v2/state', snapshot)['result'], 'accepted')
            # Same snapshot retry is rejected as out-of-order, not alerted.
            with self.assertRaises(DeliveryError) as rejected: transport.post('/v2/state', snapshot)
            self.assertEqual(rejected.exception.code, '409')
            key, payload = c.queue.take()
            self.assertEqual(transport.post('/v1/attacks', payload)['result'], 'sent_dry_run')
            self.assertEqual(transport.post('/v1/attacks', payload)['result'], 'duplicate')
            c.client.state.get_announced_attacks = lambda: []
            c.refresh()
            self.assertEqual(transport.post('/v2/state', c.publisher.take())['result'], 'accepted')
            transport.post('/v1/heartbeat', c.heartbeat())
            with urlopen(origin+'/readyz') as response: self.assertTrue(json.load(response)['ready'])
            output, errors = child.communicate(input='close\n', timeout=5)
            self.assertEqual(child.returncode, 0, errors)
            result = json.loads(output)
            self.assertEqual(result['attackCount'], 1)
            self.assertEqual(result['state']['members']['status'], 'fresh')
            self.assertIn('No current attacks', json.dumps(result['overview']))
            self.assertIn('Online 2', json.dumps(result['online']))
        finally:
            if child.poll() is None: child.kill()
            child.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
