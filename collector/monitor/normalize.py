import math
import re
import time


def name(value):
    if not isinstance(value, str):
        return None
    value = re.sub(r'[\x00-\x1f\x7f]', '', value).strip()[:200]
    return value or None


def identifier(value, *, allow_negative=False):
    if not isinstance(value, int) or isinstance(value, bool) or abs(value) > 2**53 - 1:
        return None
    if value < 0 and not allow_negative:
        return None
    return value


def normalize_attack(movement, server_id, expected_alliance_id, metadata=None, now=None, *, include_elapsed=False):
    """Normalize an attack ALREADY classified by get_announced_attacks/callback.

    The ordinary is_incoming property only matches our account, so it must
    not be used to select the alliance feed. Metadata is (troop_ids, tool_ids).
    No network work is performed here or on the library callback thread.
    """
    now = time.time() if now is None else now
    if not movement.is_attack or movement.is_returning or movement.is_mine:
        return None
    if movement.target_id <= 0:
        return None
    if movement.target_alliance_id != expected_alliance_id and movement.target_id != movement.local_player_id:
        return None
    movement_id = identifier(movement.movement_id)
    kingdom = identifier(movement.kingdom_id)
    if movement_id is None or kingdom is None:
        return None
    # Default -1 coordinates are unknown, never a real map location.
    arrival = movement.estimated_arrival
    if movement.total_time <= 0 or not math.isfinite(arrival):
        arrival = None
    elif arrival <= now and not include_elapsed:
        return None
    else:
        arrival = math.ceil(arrival)
    unknown = {'value': None, 'accuracy': 'unknown'}
    troops, tools = dict(unknown), dict(unknown)
    units = movement.units
    if units and metadata is not None:
        troop_ids, tool_ids = metadata
        if all(uid in troop_ids or uid in tool_ids for uid in units):
            troops = {'value': sum(n for uid, n in units.items() if uid in troop_ids), 'accuracy': 'exact'}
            tools = {'value': sum(n for uid, n in units.items() if uid in tool_ids), 'accuracy': 'exact'}
    if troops['accuracy'] == 'unknown' and movement.estimated_size > 0:
        troops = {'value': movement.estimated_size, 'accuracy': 'estimated'}
    return {
        'schema_version': 1, 'event_type': 'incoming_attack', 'server_id': server_id,
        'kingdom_id': kingdom, 'movement_id': movement_id,
        'attacker_id': identifier(movement.owner_id, allow_negative=True),
        'attacker_name': name(movement.source_player_name),
        'attacker_alliance': name(movement.source_alliance_name),
        'defender_id': identifier(movement.target_id),
        'defender_name': name(movement.target_player_name),
        'target_id': identifier(movement.target_area_id), 'target_name': name(movement.target_name),
        'target_x': identifier(movement.target_x), 'target_y': identifier(movement.target_y),
        'troops': troops, 'tools': tools, 'observed_at': int(now), 'arrival_at': arrival,
    }
