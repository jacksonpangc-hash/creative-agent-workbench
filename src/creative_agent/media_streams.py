"""Process-local opaque media tickets; each stream read must recheck live rights/hash."""
import math
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from time import monotonic


@dataclass(frozen=True, slots=True)
class MediaIdentity:
    project_id: str
    asset_id: str
    sha256: str


class MediaTicketRegistry:
    """Fixed-expiry capabilities containing no paths, scopes or bearer credentials.

    At capacity, the oldest issued ticket is discarded. Reading a ticket neither
    refreshes its expiry nor promotes it ahead of other tickets for eviction.
    Tickets are deliberately lost when the local process exits.
    """

    def __init__(self, *, ttl_seconds: float = 900, max_entries: int = 512,
                 clock: Callable[[], float] = monotonic):
        if (not isinstance(ttl_seconds, (int, float)) or isinstance(ttl_seconds, bool)
                or not math.isfinite(ttl_seconds) or ttl_seconds <= 0):
            raise ValueError('媒体凭证有效期必须为有限正数')
        if type(max_entries) is not int or max_entries < 1:
            raise ValueError('媒体凭证容量必须为正整数')
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: dict[str, tuple[MediaIdentity, float]] = {}
        self._lock = Lock()

    def _prune(self, now: float) -> None:
        # Call only while holding the registry lock.
        for ticket in [key for key, (_, deadline) in self._entries.items() if now >= deadline]:
            del self._entries[ticket]

    def issue(self, project_id: str, asset_id: str, sha256: str) -> str:
        if any(not isinstance(value, str) or not value.strip() for value in (project_id, asset_id, sha256)):
            raise ValueError('媒体凭证需要明确的项目、素材和内容标识')
        identity = MediaIdentity(project_id, asset_id, sha256)
        with self._lock:
            now = self._clock()
            self._prune(now)
            ticket = secrets.token_urlsafe(32)
            while ticket in self._entries:
                ticket = secrets.token_urlsafe(32)
            if len(self._entries) >= self._max_entries:
                del self._entries[next(iter(self._entries))]
            self._entries[ticket] = (identity, now + self._ttl)
            return ticket

    def resolve(self, ticket: str) -> MediaIdentity:
        if not isinstance(ticket, str) or not ticket:
            raise ValueError('媒体访问凭证不存在或已过期')
        with self._lock:
            self._prune(self._clock())
            entry = self._entries.get(ticket)
            if entry is None:
                raise ValueError('媒体访问凭证不存在或已过期')
            return entry[0]
