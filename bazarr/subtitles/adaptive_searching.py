# coding=utf-8
# fmt: off

import logging

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import settings
from .serialization import dump_text_list, parse_text_list


@dataclass(frozen=True)
class AdaptiveSearchPolicy:
    enabled: bool
    delay_setting: str = ""
    delta_setting: str = ""
    extended_search_delay: timedelta | None = None
    extended_search_delta: timedelta | None = None


def _parse_adaptive_interval(interval_value, setting_name):
    if interval_value.endswith('d'):
        return timedelta(days=int(interval_value[:1]))
    if interval_value.endswith('w'):
        return timedelta(weeks=int(interval_value[:1]))

    logging.debug(f"Adaptive searching: cannot parse {setting_name} from config file: {interval_value}")
    return None


def get_adaptive_search_policy():
    if not settings.general.adaptive_searching:
        return AdaptiveSearchPolicy(enabled=False)

    delay_setting = settings.general.adaptive_searching_delay
    delta_setting = settings.general.adaptive_searching_delta

    return AdaptiveSearchPolicy(
        enabled=True,
        delay_setting=delay_setting,
        delta_setting=delta_setting,
        extended_search_delay=_parse_adaptive_interval(delay_setting, "adaptive_searching_delay"),
        extended_search_delta=_parse_adaptive_interval(delta_setting, "adaptive_searching_delta"),
    )


def get_active_search_languages(desired_languages, attempt_string, adaptive_search_policy=None):
    policy = adaptive_search_policy

    if policy is not None and not policy.enabled:
        logging.debug("adaptive searching is disabled, search will run.")
        return list(desired_languages)
    if policy is None and not settings.general.adaptive_searching:
        logging.debug("adaptive searching is disabled, search will run.")
        return list(desired_languages)

    logging.debug("Adaptive searching is enable, we'll see if it's time to search again...")
    try:
        attempts = parse_text_list(attempt_string)
    except ValueError:
        logging.debug("Adaptive searching: attempts is malformed. As a failsafe, search will run.")
        return list(desired_languages)

    if not attempts:
        logging.debug("Adaptive searching: attempts list is empty, search will run.")
        return list(desired_languages)

    if policy is None:
        policy = get_adaptive_search_policy()

    delay_setting = policy.delay_setting
    delta_setting = policy.delta_setting
    extended_search_delay = policy.extended_search_delay
    if extended_search_delay is None:
        return list(desired_languages)

    extended_search_delta = policy.extended_search_delta
    if extended_search_delta is None:
        return list(desired_languages)

    logging.debug(f"Adaptive searching: delay after initial search value: {extended_search_delay}")
    logging.debug(f"Adaptive searching: delta between latest search and now value: {extended_search_delta}")

    attempts_by_language = {}
    for attempt in attempts:
        if not isinstance(attempt, (list, tuple)) or len(attempt) < 2:
            logging.debug("Adaptive searching: attempts is malformed. As a failsafe, search will run.")
            return list(desired_languages)
        attempts_by_language.setdefault(attempt[0], []).append(attempt[1])

    now = datetime.now()
    due_languages = []
    for desired_language in desired_languages:
        matching_attempts = attempts_by_language.get(desired_language, [])

        if not matching_attempts:
            logging.debug("Adaptive searching: there's no attempts matching desired language, search will run.")
            due_languages.append(desired_language)
            continue

        initial_search_attempt = min(matching_attempts)
        latest_search_attempt = max(matching_attempts)

        try:
            initial_search_timestamp = datetime.fromtimestamp(initial_search_attempt)
            latest_search_timestamp = datetime.fromtimestamp(latest_search_attempt)
        except (OverflowError, ValueError, OSError):
            logging.debug("Adaptive searching: unable to parse initial and latest search timestamps, search will run.")
            due_languages.append(desired_language)
            continue

        logging.debug(f"Adaptive searching: initial search date for {desired_language} is "
                      f"{initial_search_timestamp}")
        logging.debug(f"Adaptive searching: latest search date for {desired_language} is {latest_search_timestamp}")

        if initial_search_timestamp + extended_search_delay > now:
            logging.debug(f"Adaptive searching: it's been less than {delay_setting} since initial search, search "
                          "will run.")
            due_languages.append(desired_language)
            continue

        logging.debug(f"Adaptive searching: it's been more than {delay_setting} since initial search, let's check if "
                      "it's time to search again.")
        if latest_search_timestamp + extended_search_delta <= now:
            logging.debug(f"Adaptive searching: it's been more than {delta_setting} since latest search, search will "
                          "run.")
            due_languages.append(desired_language)
        else:
            logging.debug(f"Adaptive searching: it's been less than {delta_setting} since latest search, we're not "
                          "ready to search yet.")

    return due_languages


def is_search_active(desired_language, attempt_string, adaptive_search_policy=None):
    """
    Function to test if it's time to search again after a previous attempt matching the desired language. For 3 weeks,
    we search on a scheduled basis but after 3 weeks we start searching only once a week.

    @param desired_language: 2 letters language to search for in attempts
    @type desired_language: str
    @param attempt_string: string representation of a list of lists from database column failedAttempts
    @type attempt_string: str

    @return: return True if it's time to search again and False if not
    @rtype: bool
    """

    return desired_language in get_active_search_languages(
        [desired_language],
        attempt_string,
        adaptive_search_policy=adaptive_search_policy,
    )


def updateFailedAttempts(desired_language, attempt_string):
    """
    Function to parse attempts and make sure we only keep initial and latest search timestamp for each language.

    @param desired_language: 2 letters language to search for in attempts
    @type desired_language: str
    @param attempt_string: string representation of a list of lists from database column failedAttempts
    @type attempt_string: str

    @return: return a string representation of a list of lists like [str(language_code), str(attempts)]
    @rtype: str
    """

    try:
        attempts = parse_text_list(attempt_string)
        logging.debug(f"Adaptive searching: current attempts value is {attempts}")
    except ValueError:
        logging.debug("Adaptive searching: failed to parse attempts value, we'll use an empty list.")
        attempts = []

    matching_attempts = sorted([x for x in attempts if x[0] == desired_language], key=lambda x: x[1])
    logging.debug(f"Adaptive searching: attempts matching language {desired_language}: {matching_attempts}")

    filtered_attempts = sorted([x for x in attempts if x[0] != desired_language], key=lambda x: x[1])
    logging.debug(f"Adaptive searching: attempts not matching language {desired_language}: {filtered_attempts}")

    # get the initial search from attempts if there's one
    if len(matching_attempts):
        filtered_attempts.append(matching_attempts[0])

    # append current attempt with language and timestamp to attempts
    filtered_attempts.append([desired_language, datetime.timestamp(datetime.now())])

    updated_attempts = sorted(filtered_attempts, key=lambda x: x[0])
    logging.debug(f"Adaptive searching: updated attempts that will be saved to database is {updated_attempts}")

    return dump_text_list(updated_attempts)
