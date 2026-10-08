"""Read-only snapshots, separate from alert delivery and its retry queue."""
from threading import Lock
from uuid import uuid4
import json
import copy
import time
from .normalize import name, identifier


def member_item(member):
    pid = identifier(member.player_id)
    label = name(member.name)
    if not pid or not label:
        raise ValueError('Invalid member identity')
    info = member.member_info
    activity = getattr(info, 'login_activity', None)
    explicit = info is not None and 'login_activity' in info.model_fields_set
    state = 'unknown'
    if explicit and isinstance(activity, int) and not isinstance(activity, bool):
        state = 'online' if activity == 0 else 'offline' if activity in (1, 2, 3, 4) else 'unknown'
    return {'player_id': pid, 'name': label, 'online_state': state}


def section(items, limit, observed):
    return {'observed_at': observed, 'complete': len(items) <= limit,
            'truncated': len(items) > limit, 'count': min(len(items), limit), 'items': items[:limit]}


class SnapshotPublisher:
    """One in-flight envelope and only the newest pending envelope; no history."""
    def __init__(self, settings):
        self.settings = settings
        self.instance = str(uuid4())
        self.sequence = 0
        self.lock = Lock()
        self.pending = None
        self.sections = {}

    def update(self, kind, value):
        with self.lock:
            sections = {**self.sections, kind: copy.deepcopy(value)}
            sequence = self.sequence + 1
            pending = {'schema_version': 2, 'server_id': self.settings.server_id,
                            'alliance_id': self.settings.alliance_id, 'collector_instance_id': self.instance,
                            'sequence': sequence, 'generated_at': int(time.time()), **copy.deepcopy(sections)}
            # Keep the combined UTF-8 envelope below the HTTP body limit, even
            # with maximal Unicode labels in both sections. Never claim a
            # byte-truncated list is complete.
            while len(json.dumps(pending, ensure_ascii=False).encode('utf-8')) > 1_048_576:
                candidates = [key for key in ('attacks', 'members') if pending.get(key, {}).get('items')]
                if not candidates:
                    raise ValueError('Oversized state envelope')
                key = max(candidates, key=lambda k: len(json.dumps(pending[k], ensure_ascii=False).encode('utf-8')))
                value = pending[key]
                remove = max(1, len(value['items']) // 10)
                del value['items'][-remove:]
                value.update(count=len(value['items']), complete=False, truncated=True)
            self.sections, self.sequence, self.pending = sections, sequence, pending

    def take(self):
        with self.lock:
            pending, self.pending = self.pending, None
            return pending

    def retry(self, value):
        with self.lock:
            if self.pending is None and time.time() - value['generated_at'] <= 120:
                self.pending = value

    def clear(self):
        with self.lock:
            self.sections.clear()
            self.pending = None
