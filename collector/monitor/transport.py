from urllib import request, error
from dataclasses import dataclass
from collections import OrderedDict
from http.client import HTTPException
import json
import threading
import time


class DeliveryError(Exception):
    def __init__(self, code='network', *, permanent=False, retry_after=0):
        super().__init__(f'Core delivery error: {code}')
        self.code, self.permanent, self.retry_after = code, permanent, retry_after


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CoreTransport:
    def __init__(self, settings):
        self.origin, self.secret, self.timeout = settings.core_url, settings.secret, settings.http_timeout
        self.opener = request.build_opener(NoRedirect())

    def post(self, path, payload):
        req = request.Request(self.origin + path, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                              headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.secret},
                              method='POST')
        try:
            with self.opener.open(req, timeout=self.timeout) as response:
                data = response.read(32_769)
                if len(data) > 32_768:
                    raise DeliveryError('invalid_response')
                result = json.loads(data)
                expected = {'accepted'} if path == '/v1/heartbeat' else {'sent', 'sent_dry_run', 'duplicate', 'expired'}
                if not isinstance(result, dict) or result.get('result') not in expected:
                    raise DeliveryError('invalid_response')
                return result
        except error.HTTPError as exc:
            try:
                retry_after = min(60, max(0, float(exc.headers.get('Retry-After', '0'))))
            except (ValueError, TypeError):
                retry_after = 0
            raise DeliveryError(str(exc.code), permanent=exc.code < 500 and exc.code not in (408, 429),
                                retry_after=retry_after) from None
        except (error.URLError, TimeoutError, OSError, HTTPException, ValueError):
            # Never include a URL, password, token or raw response in logs.
            raise DeliveryError('network_or_response') from None


@dataclass
class Pending:
    payload: dict
    due: float
    revision: int
    attempts: int = 0


class MemoryDeliveryQueue:
    """Bounded, volatile retries. Core owns the successful-delivery seen set.

    Each successful game refresh can enqueue all active attacks again, so a
    Core restart can reannounce them even when the collector did not restart.
    """
    def __init__(self, capacity=1000):
        self.capacity = capacity
        self.lock = threading.Lock()
        self.items = OrderedDict()
        self.revision = 0

    @staticmethod
    def key(payload):
        return (payload['server_id'], payload['kingdom_id'], payload['movement_id'])

    @staticmethod
    def expired(payload, now):
        return now - payload['observed_at'] > 120 or (payload['arrival_at'] is not None and payload['arrival_at'] <= now)

    def checkpoint(self):
        with self.lock:
            return self.revision

    def submit(self, payload, now=None, checkpoint=None):
        now = time.time() if now is None else now
        key = self.key(payload)
        with self.lock:
            # A callback received after the snapshot was read is newer than
            # that snapshot, even when both observations share one second.
            if key in self.items and checkpoint is not None and self.items[key].revision > checkpoint:
                return True
            self.revision += 1
            if key in self.items:
                self.items[key].payload = payload
                self.items[key].revision = self.revision
                return True
            # Prune expired work before refusing new alerts. Do not silently
            # evict a live attack: a capacity warning must be observable.
            self._prune(now)
            if len(self.items) >= self.capacity:
                return False
            self.items[key] = Pending(payload, due=now, revision=self.revision)
            return True

    def _prune(self, now):
        for key in list(self.items):
            if self.expired(self.items[key].payload, now):
                del self.items[key]

    def take(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            self._prune(now)
            for key, pending in self.items.items():
                if pending.due <= now:
                    return key, dict(pending.payload)
        return None

    def done(self, key):
        with self.lock:
            self.items.pop(key, None)

    def retry(self, key, retry_after=0, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if key in self.items:
                pending = self.items[key]
                pending.attempts += 1
                pending.due = now + max(retry_after, min(30, 2 ** min(pending.attempts, 5)))

    def retain(self, active_keys, checkpoint=None):
        """Drop removed attacks ONLY after a successful fresh game snapshot."""
        with self.lock:
            for key in list(self.items):
                if key not in active_keys and (checkpoint is None or self.items[key].revision <= checkpoint):
                    del self.items[key]

    def __len__(self):
        with self.lock:
            return len(self.items)
