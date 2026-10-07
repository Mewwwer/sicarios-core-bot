from dataclasses import dataclass, field
from urllib.parse import urlparse
import os
import re


@dataclass(frozen=True)
class Settings:
    server_id: str
    game_url: str
    game_zone: str
    alliance_id: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    core_url: str
    secret: str = field(repr=False)
    poll_seconds: int = 30
    heartbeat_seconds: int = 15
    http_timeout: int = 8
    health_port: int = 8081
    max_pending: int = 1000
    client_version: str | None = None

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env

        def required(name):
            value = env.get(name, '')
            if not value:
                raise ValueError(f'Missing {name}')
            return value

        def number(name, fallback, minimum, maximum):
            try:
                value = int(env.get(name, fallback))
            except (ValueError, TypeError):
                raise ValueError(f'Invalid {name}') from None
            if not minimum <= value <= maximum:
                raise ValueError(f'Invalid {name}')
            return value

        server_id = required('GGE_SERVER_ID')
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,80}', server_id):
            raise ValueError('Invalid GGE_SERVER_ID')
        game_url = required('GGE_GAME_URL')
        game = urlparse(game_url)
        if game.scheme != 'wss' or not game.hostname or game.username or game.password or game.query or game.fragment:
            raise ValueError('GGE_GAME_URL must be a wss game endpoint')
        core_url = required('ATTACK_CORE_URL').rstrip('/')
        core = urlparse(core_url)
        if core.scheme not in ('http', 'https') or not core.hostname or core.username or core.password or core.path not in ('', '/') or core.query or core.fragment:
            raise ValueError('ATTACK_CORE_URL must be the receiver origin')
        secret = required('ATTACK_SHARED_SECRET')
        if len(secret) < 32:
            raise ValueError('ATTACK_SHARED_SECRET must have at least 32 characters')
        return cls(
            server_id=server_id, game_url=game_url, game_zone=required('GGE_GAME_ZONE'),
            alliance_id=number('GGE_ALLIANCE_ID', None, 1, 2**53 - 1),
            username=required('GGE_USERNAME'), password=required('GGE_PASSWORD'),
            core_url=core_url, secret=secret,
            poll_seconds=number('GGE_POLL_SECONDS', 30, 10, 60),
            heartbeat_seconds=number('ATTACK_HEARTBEAT_SECONDS', 15, 5, 30),
            http_timeout=number('ATTACK_HTTP_TIMEOUT', 8, 1, 30),
            health_port=number('COLLECTOR_HEALTH_PORT', 8081, 1, 65535),
            max_pending=number('ATTACK_MAX_PENDING', 1000, 10, 10_000),
            client_version=env.get('GGE_CLIENT_VERSION') or None,
        )
