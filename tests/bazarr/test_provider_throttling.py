from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
import requests

from bazarr.app import get_providers
from app import provider_throttling as policies
from subliminal.exceptions import AuthenticationError, DownloadLimitExceeded, ServiceUnavailable, Unauthorized
from subliminal_patch.exceptions import APIThrottled, MustGetBlacklisted, TooManyRequests


@pytest.fixture
def effects(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Mock, Mock]]:
    persisted, tracker = Mock(), Mock()
    monkeypatch.setattr(get_providers, 'tp', {})
    monkeypatch.setattr(get_providers, 'throttle_count', {})
    monkeypatch.setattr(get_providers, 'set_throttled_providers', persisted)
    monkeypatch.setattr(get_providers, 'event_tracker', tracker)
    monkeypatch.setattr(get_providers, 'update_throttled_provider', lambda: None)
    monkeypatch.setattr(get_providers.time, 'sleep', lambda seconds: None)
    yield persisted, tracker


@pytest.mark.parametrize('error_type', [
    requests.exceptions.Timeout, requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout,
    TimeoutError, TooManyRequests, ServiceUnavailable, APIThrottled,
])
@pytest.mark.parametrize('subclass', [False, True])
def test_transient_failures_count_before_disabling(
    error_type: type[Exception], subclass: bool, effects: tuple[Mock, Mock],
) -> None:
    failure_type = type('ProviderFailure', (error_type,), {}) if subclass else error_type
    persisted, tracker = effects
    for count in range(1, 5):
        get_providers.provider_throttle('provider', failure_type('Temporary failure'))
        assert 'provider' not in get_providers.tp
        assert get_providers.throttle_count['provider']['count'] == count
        persisted.assert_not_called()
        tracker.track_throttling.assert_not_called()
    get_providers.provider_throttle('provider', failure_type('Temporary failure'))
    assert get_providers.tp['provider'][0] == failure_type.__name__
    assert 'provider' not in get_providers.throttle_count
    persisted.assert_called_once()
    tracker.track_throttling.assert_called_once()


@pytest.mark.parametrize('provider,error_type,description,failures', [
    ('opensubtitlescom', TooManyRequests, '1 minute', 5),
    ('addic7ed', DownloadLimitExceeded, '3 hours', 1),
    ('unknown', ServiceUnavailable, '20 minutes', 5),
    ('opensubtitlescom', Unauthorized, '12 hours', 1),
    ('subsource', AuthenticationError, '1 hour', 1),
    ('unknown', ValueError, '10 minutes', 1),
])
def test_subclass_resolves_most_specific_policy(
    provider: str, error_type: type[Exception], description: str, failures: int,
) -> None:
    class Failure(error_type):
        pass
    policy = policies.resolve_throttle_policy(provider, Failure('failure'))
    assert policy.description == description
    assert policy.failures == failures


def test_authentication_failure_is_immediate(effects: tuple[Mock, Mock]) -> None:
    get_providers.provider_throttle('provider', AuthenticationError('credentials'))
    assert get_providers.tp['provider'][0] == 'AuthenticationError'
    assert not get_providers.throttle_count
    effects[0].assert_called_once()


def test_retry_does_not_resolve_a_deadline_before_the_limit(
    monkeypatch: pytest.MonkeyPatch, effects: tuple[Mock, Mock],
) -> None:
    deadline = Mock(side_effect=AssertionError('A retry does not need a throttle deadline'))
    monkeypatch.setattr(policies.ThrottlePolicy, 'deadline', deadline)
    for _ in range(4):
        get_providers.provider_throttle('provider', TooManyRequests())
    deadline.assert_not_called()


@pytest.mark.parametrize('provider,error_type,description,failures', [
    ('opensubtitlescom', TooManyRequests, '1 minute', 5),
    ('addic7ed', DownloadLimitExceeded, '3 hours', 1),
    ('unknown', ServiceUnavailable, '20 minutes', 5),
])
def test_callback_uses_subclass_policy_and_keeps_original_reason(
    provider: str, error_type: type[Exception], description: str, failures: int, effects: tuple[Mock, Mock],
) -> None:
    class ProviderFailure(error_type):
        pass
    for _ in range(failures):
        get_providers.provider_throttle(provider, ProviderFailure('limited'))
    reason, until, actual = get_providers.tp[provider]
    assert reason == 'ProviderFailure'
    assert actual == description
    assert until > datetime.now()


def test_policy_resolution_does_not_compute_unrelated_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_timezone(name: str) -> ZoneInfo:
        raise AssertionError(f'Unrelated timezone lookup: {name}')
    monkeypatch.setattr(policies, 'ZoneInfo', unexpected_timezone)
    policy = policies.resolve_throttle_policy('unknown', AuthenticationError())
    now = datetime(2026, 10, 19, 12)
    assert policy.deadline(now) == (now + timedelta(hours=12), '12 hours')


@pytest.mark.parametrize('provider,timezone,grace', [
    ('titulky', 'Europe/Prague', timedelta()),
    ('legendasdivx', 'Europe/Lisbon', timedelta(hours=1)),
    ('subdl', 'Etc/GMT', timedelta(minutes=15)),
])
def test_only_selected_daily_rule_is_evaluated_once(
    monkeypatch: pytest.MonkeyPatch, provider: str, timezone: str, grace: timedelta,
) -> None:
    timezone_lookup = Mock(side_effect=ZoneInfo)
    monkeypatch.setattr(policies, 'ZoneInfo', timezone_lookup)
    policy = policies.resolve_throttle_policy(provider, DownloadLimitExceeded())
    timezone_lookup.assert_not_called()
    now = datetime(2026, 10, 19, 12, tzinfo=ZoneInfo('UTC'))
    local_now = now.astimezone(ZoneInfo(timezone))
    duration = local_now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1) - local_now + grace
    assert policy.deadline(now) == (now + duration, f'{duration.seconds // 3600 + 1} hours')
    timezone_lookup.assert_called_once_with(timezone)
    tomorrow = now + timedelta(days=1, hours=1)
    assert policy.deadline(tomorrow)[0] > tomorrow
    assert timezone_lookup.call_count == 2


@pytest.mark.parametrize('elapsed,expected_count', [(119, 2), (120, 1), (121, 1)])
def test_counter_window_boundary(elapsed: int, expected_count: int) -> None:
    counters: dict[str, policies.ThrottleCounter] = {}
    policy = policies.resolve_throttle_policy('provider', TooManyRequests())
    now = datetime(2026, 10, 19, 12)
    assert policies.record_failure(counters, 'provider', policy, now) == 1
    assert policies.record_failure(counters, 'provider', policy, now + timedelta(seconds=elapsed)) == expected_count


def test_threshold_consumes_counter_and_providers_are_independent() -> None:
    counters: dict[str, policies.ThrottleCounter] = {}
    policy = policies.resolve_throttle_policy('provider', TooManyRequests())
    for count in range(1, 6):
        assert policies.record_failure(counters, 'first', policy) == count
    assert 'first' not in counters
    assert policies.record_failure(counters, 'first', policy) == 1
    assert policies.record_failure(counters, 'second', policy) == 1


def test_concurrent_failures_do_not_lose_updates() -> None:
    counters: dict[str, policies.ThrottleCounter] = {}
    policy = policies.resolve_throttle_policy('provider', TooManyRequests())
    def report_failure(index: int) -> int:
        return policies.record_failure(counters, 'provider', policy)
    with ThreadPoolExecutor(max_workers=12) as pool:
        counts = list(pool.map(report_failure, range(100)))
    assert counts.count(5) == 20
    assert not counters


def test_reset_clears_pending_failures_even_without_a_persisted_throttle(effects: tuple[Mock, Mock]) -> None:
    get_providers.throttled_count('provider')
    get_providers.reset_throttled_providers()
    assert not get_providers.throttle_count


def test_selective_reset_preserves_unrelated_failures(effects: tuple[Mock, Mock]) -> None:
    now = datetime.now()
    get_providers.tp.update({'auth': ('AuthenticationError', now, '12 hours'),
                             'network': ('Timeout', now, '1 hour')})
    for name in ['auth', 'network', 'pending']:
        get_providers.throttled_count(name)
    get_providers.reset_throttled_providers(only_auth_or_conf_error=True)
    assert set(get_providers.throttle_count) == {'network', 'pending'}
    assert set(get_providers.tp) == {'network'}


def test_incompatible_pickle_cleanup_preserves_no_throttle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, effects: tuple[Mock, Mock],
) -> None:
    backend = get_providers.SZFileBackend({'appname': 'provider-test', 'app_cache_dir': str(tmp_path)})
    backend.set('cached', 'content')
    backend.sync()
    filenames = list(backend.all_filenames)
    assert filenames
    monkeypatch.setattr(get_providers, 'subliminal_cache_region', SimpleNamespace(backend=backend))
    get_providers.provider_throttle('provider', ValueError('unsupported pickle protocol: 99'))
    assert all(not get_providers.os.path.exists(filename) for filename in filenames)
    assert 'provider' not in get_providers.tp
    effects[0].assert_not_called()


def test_blacklisting_keeps_original_context(monkeypatch: pytest.MonkeyPatch, effects: tuple[Mock, Mock]) -> None:
    blacklist = Mock()
    monkeypatch.setattr(get_providers, 'blacklist_subtitle', blacklist)
    language = get_providers.Language('eng')
    ids = {'sonarrSeriesId': 1, 'sonarrEpisodeId': 2}
    get_providers.provider_throttle('provider', MustGetBlacklisted('subtitle-id', 'series'), ids, language)
    blacklist.assert_called_once_with('provider', 'subtitle-id', 'series', ids, language)
    assert not get_providers.tp
    effects[0].assert_not_called()
