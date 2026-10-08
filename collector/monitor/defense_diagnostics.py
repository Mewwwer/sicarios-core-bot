"""Small whitelist projections; never serialize a packet or an arbitrary value."""
import json
import logging
from .normalize import identifier

LOG = logging.getLogger('sicarios.collector')
LIMIT = 5  # Fresh SDI attempts per process. Cache hits do not consume a slot.


def selected_fields(payload):
    result = {}
    for key in ('AS', 'UWL', 'UYL', 'AUYL'):
        value = payload.get(key)
        valid = identifier(value)
        result[key] = {'status': 'missing' if key not in payload else 'invalid' if valid is None else 'present',
                       'value': valid}
    positions = payload.get('S')
    result['S'] = {'status': 'missing' if 'S' not in payload else 'present' if isinstance(positions, list) else 'invalid',
                   'position_count': len(positions) if isinstance(positions, list) else None, 'wall': []}
    if isinstance(positions, list):
        # Only the three wall positions are needed for this discrepancy.
        for position in positions[:3]:
            rows = position[:20] if isinstance(position, list) else []
            pairs = [row for row in rows if isinstance(row, list) and len(row) == 2
                     and identifier(row[0]) is not None and identifier(row[1]) is not None]
            result['S']['wall'].append({'pairs': pairs,
                'invalid': not isinstance(position, list) or len(pairs) != len(rows),
                'truncated': isinstance(position, list) and len(position) > 20})
    return result


def model_fields(reply):
    # Do not model_dump: that would also traverse gui/gli, equipment and extras.
    payload = {}
    for key, field in [('S', 'defense_positions'), ('UWL', 'wall_limit'),
                       ('UYL', 'yard_limit'), ('AUYL', 'available_yard_limit')]:
        if field in reply.model_fields_set:
            payload[key] = getattr(reply, field)
    if 'AS' in (reply.model_extra or {}):
        payload['AS'] = reply.model_extra['AS']
    return selected_fields(payload)


def emit(record):
    # Called only on the SDI worker, after its result/error is released; no
    # receive-thread callbacks, disk files, HTTP export or diagnostic history.
    try:
        encoded = json.dumps(record, separators=(',', ':'), ensure_ascii=True)
        if len(encoded) <= 16_384:
            LOG.info('SICARIOS_DEFENSE_DIAGNOSTIC %s', encoded)
    except Exception:
        # Logging must never quarantine SDI or stop the attack monitor.
        pass
