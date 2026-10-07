"""Send one synthetic attack to a dry-run receiver; never connects to the game."""
import argparse
import json
import os
import sys
import time
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'collector'))
from monitor.transport import CoreTransport, DeliveryError


def main():
    parser = argparse.ArgumentParser(description='SICARIOS synthetic attack')
    parser.add_argument('--movement-id', type=int, default=900001)
    parser.add_argument('--kingdom-id', type=int, default=0)
    args = parser.parse_args()
    if not os.getenv('ATTACK_CORE_URL') or not os.getenv('ATTACK_SHARED_SECRET') or not os.getenv('GGE_SERVER_ID'):
        parser.error('Set ATTACK_CORE_URL, ATTACK_SHARED_SECRET and GGE_SERVER_ID in the environment')
    now = int(time.time())
    payload = {
        'schema_version': 1, 'event_type': 'incoming_attack', 'server_id': os.environ['GGE_SERVER_ID'],
        'movement_id': args.movement_id, 'kingdom_id': args.kingdom_id,
        'attacker_id': 100, 'attacker_name': 'TEST útočník', 'attacker_alliance': 'TEST',
        'defender_id': 200, 'defender_name': 'TEST obránce',
        'target_id': 300, 'target_name': 'TEST hrad', 'target_x': 790, 'target_y': 804,
        'troops': {'value': 416843, 'accuracy': 'estimated'},
        'tools': {'value': None, 'accuracy': 'unknown'}, 'observed_at': now, 'arrival_at': now + 600,
    }
    settings = SimpleNamespace(core_url=os.environ['ATTACK_CORE_URL'].rstrip('/'),
                               secret=os.environ['ATTACK_SHARED_SECRET'], http_timeout=8)
    try:
        print(json.dumps(CoreTransport(settings).post('/v1/attacks', payload)))
    except DeliveryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
