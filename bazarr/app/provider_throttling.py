from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Lock
from typing import Literal, TypedDict
from zoneinfo import ZoneInfo

import requests

from subliminal.exceptions import AuthenticationError, ConfigurationError, DownloadLimitExceeded, ServiceUnavailable
from subliminal_patch.exceptions import (
    APIThrottled, ForbiddenError, IPAddressBlocked, ParseResponseError, ProviderError, SearchLimitReached,
    TooManyRequests,
)


@dataclass(frozen=True, slots=True)
class ThrottlePolicy:
    delay: timedelta
    description: str
    failures: int = 1
    window: timedelta = timedelta(seconds=120)
    reset_timezone: str | None = None
    reset_reason: Literal['AuthenticationError', 'ConfigurationError'] | None = None

    def deadline(self, now: datetime) -> tuple[datetime, str]:
        if self.reset_timezone is None:
            return now + self.delay, self.description
        local_now = now.astimezone(ZoneInfo(self.reset_timezone))
        midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        delay = midnight - local_now + self.delay
        return now + delay, f'{delay.seconds // 3600 + 1} hours'


_ONE_HOUR_RETRY = ThrottlePolicy(timedelta(hours=1), '1 hour', failures=5)
_DAILY_PRAGUE = ThrottlePolicy(timedelta(), '', reset_timezone='Europe/Prague')
_DAILY_LISBON = ThrottlePolicy(timedelta(hours=1), '', reset_timezone='Europe/Lisbon')
_DAILY_GMT = ThrottlePolicy(timedelta(minutes=15), '', reset_timezone='Etc/GMT')
_FALLBACK = ThrottlePolicy(timedelta(minutes=10), '10 minutes')

_DEFAULT_POLICIES: dict[type[BaseException], ThrottlePolicy] = {
    TooManyRequests: _ONE_HOUR_RETRY,
    DownloadLimitExceeded: ThrottlePolicy(timedelta(hours=3), '3 hours'),
    ServiceUnavailable: ThrottlePolicy(timedelta(minutes=20), '20 minutes', failures=5),
    APIThrottled: ThrottlePolicy(timedelta(minutes=10), '10 minutes', failures=5),
    ParseResponseError: ThrottlePolicy(timedelta(hours=6), '6 hours'),
    requests.exceptions.Timeout: _ONE_HOUR_RETRY,
    TimeoutError: _ONE_HOUR_RETRY,
    ConfigurationError: ThrottlePolicy(timedelta(hours=12), '12 hours', reset_reason='ConfigurationError'),
    PermissionError: ThrottlePolicy(timedelta(hours=12), '12 hours'),
    requests.exceptions.ProxyError: ThrottlePolicy(timedelta(hours=1), '1 hour'),
    AuthenticationError: ThrottlePolicy(timedelta(hours=12), '12 hours', reset_reason='AuthenticationError'),
}

_PROVIDER_POLICIES: dict[str, dict[type[BaseException], ThrottlePolicy]] = {
    'opensubtitlescom': {
        TooManyRequests: ThrottlePolicy(timedelta(minutes=1), '1 minute', failures=5),
        DownloadLimitExceeded: ThrottlePolicy(timedelta(hours=6), '6 hours'),
        ProviderError: ThrottlePolicy(timedelta(minutes=1), '1 minute'),
    },
    'addic7ed': {
        DownloadLimitExceeded: ThrottlePolicy(timedelta(hours=3), '3 hours'),
        TooManyRequests: ThrottlePolicy(timedelta(minutes=5), '5 minutes', failures=5),
        IPAddressBlocked: ThrottlePolicy(timedelta(hours=1), '1 hours'),
    },
    'titlovi': {TooManyRequests: ThrottlePolicy(timedelta(minutes=5), '5 minutes', failures=5)},
    'titrari': {TooManyRequests: ThrottlePolicy(timedelta(minutes=10), '10 minutes', failures=5)},
    'titulky': {
        TooManyRequests: ThrottlePolicy(timedelta(minutes=1), '1 minute', failures=5),
        DownloadLimitExceeded: _DAILY_PRAGUE,
    },
    'legendasdivx': {
        TooManyRequests: ThrottlePolicy(timedelta(hours=3), '3 hours', failures=5),
        DownloadLimitExceeded: _DAILY_LISBON,
        IPAddressBlocked: _DAILY_LISBON,
        SearchLimitReached: _DAILY_LISBON,
    },
    'whisperai': {requests.exceptions.ConnectionError: ThrottlePolicy(timedelta(minutes=5), '5 minutes')},
    'regielive': {
        APIThrottled: ThrottlePolicy(timedelta(hours=1), '1 hour', failures=5),
        TooManyRequests: ThrottlePolicy(timedelta(minutes=5), '5 minutes', failures=5),
        ProviderError: ThrottlePolicy(timedelta(minutes=10), '10 minutes'),
    },
    'subsource': {
        APIThrottled: ThrottlePolicy(timedelta(minutes=10), '10 minutes', failures=5),
        AuthenticationError: ThrottlePolicy(timedelta(hours=1), '1 hour', reset_reason='AuthenticationError'),
        ForbiddenError: ThrottlePolicy(timedelta(minutes=15), '15 minutes'),
        TooManyRequests: _ONE_HOUR_RETRY,
    },
    'subdl': {
        ProviderError: ThrottlePolicy(timedelta(hours=1), '1 hour'),
        DownloadLimitExceeded: _DAILY_GMT,
        APIThrottled: ThrottlePolicy(timedelta(minutes=15), '15 minutes', failures=5),
    },
}


def resolve_throttle_policy(provider: str, error: Exception) -> ThrottlePolicy:
    overrides = _PROVIDER_POLICIES.get(provider)
    for error_class in type(error).__mro__:
        if overrides is not None:
            override = overrides.get(error_class)
            if override is not None:
                return override
        default = _DEFAULT_POLICIES.get(error_class)
        if default is not None:
            return default
    return _FALLBACK


class ThrottleCounter(TypedDict):
    count: int
    time: datetime


_COUNTER_LOCK = Lock()


def record_failure(counters: dict[str, ThrottleCounter], name: str, policy: ThrottlePolicy,
                   now: datetime | None = None) -> int:
    with _COUNTER_LOCK:
        now = datetime.now() if now is None else now
        counter = counters.get(name)
        if counter is None or counter['time'] <= now:
            counter = ThrottleCounter(count=1, time=now + policy.window)
            counters[name] = counter
        else:
            counter['count'] += 1
        count = counter['count']
        if count >= policy.failures:
            counters.pop(name, None)
        return count


def reset_failure_counts(counters: dict[str, ThrottleCounter], names: list[str] | None = None) -> None:
    with _COUNTER_LOCK:
        if names is None:
            counters.clear()
        else:
            for name in names:
                counters.pop(name, None)
