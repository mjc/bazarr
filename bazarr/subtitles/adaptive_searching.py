# coding=utf-8
# fmt: off

import ast
import logging

from datetime import datetime, timedelta

from app.config import settings


def _get_attempts(attempt_string):
    if isinstance(attempt_string, list):
        attempts = attempt_string
    else:
        attempts = ast.literal_eval(attempt_string)

    if type(attempts) is not list:
        raise ValueError

    return attempts


def _get_attempt_windows(attempts):
    attempt_windows = {}

    for attempt in attempts:
        if not isinstance(attempt, (list, tuple)) or len(attempt) < 2:
            raise ValueError

        desired_language, timestamp = attempt[0], attempt[1]
        initial_timestamp, latest_timestamp = attempt_windows.get(desired_language, (timestamp, timestamp))
        if timestamp < initial_timestamp:
            initial_timestamp = timestamp
        if timestamp > latest_timestamp:
            latest_timestamp = timestamp
        attempt_windows[desired_language] = (initial_timestamp, latest_timestamp)

    return attempt_windows


def _get_adaptive_timedelta(setting_name, setting_value):
    try:
        value = int(setting_value[:-1])
    except (TypeError, ValueError):
        logging.debug(f"Adaptive searching: cannot parse {setting_name} from config file: {setting_value}")
        return None

    if setting_value.endswith('d'):
        return timedelta(days=value)
    elif setting_value.endswith('w'):
        return timedelta(weeks=value)

    logging.debug(f"Adaptive searching: cannot parse {setting_name} from config file: {setting_value}")
    return None


def get_adaptive_search_policy():
    if not settings.general.adaptive_searching:
        logging.debug("adaptive searching is disabled, search will run.")
        return None

    extended_search_delay = _get_adaptive_timedelta(
        'adaptive_searching_delay',
        settings.general.adaptive_searching_delay,
    )
    if extended_search_delay is None:
        return None

    extended_search_delta = _get_adaptive_timedelta(
        'adaptive_searching_delta',
        settings.general.adaptive_searching_delta,
    )
    if extended_search_delta is None:
        return None

    return {
        "delay": extended_search_delay,
        "delta": extended_search_delta,
        "now": datetime.now(),
    }


def get_active_search_languages(desired_languages, attempt_string, adaptive_search_policy=None):
    if isinstance(desired_languages, str):
        desired_languages = [desired_languages]
    else:
        desired_languages = list(desired_languages)

    if not desired_languages:
        return []

    if adaptive_search_policy is None:
        adaptive_search_policy = get_adaptive_search_policy()

    if adaptive_search_policy is None:
        return desired_languages

    logging.debug("Adaptive searching is enable, we'll see if it's time to search again...")
    try:
        attempts = _get_attempts(attempt_string)
    except (SyntaxError, ValueError, TypeError):
        logging.debug("Adaptive searching: attempts is malformed. As a failsafe, search will run.")
        return desired_languages

    if not len(attempts):
        logging.debug("Adaptive searching: attempts list is empty, search will run.")
        return desired_languages

    try:
        attempt_windows = _get_attempt_windows(attempts)
    except ValueError:
        logging.debug("Adaptive searching: attempts is malformed. As a failsafe, search will run.")
        return desired_languages

    active_languages = []
    for desired_language in desired_languages:
        if desired_language not in attempt_windows:
            logging.debug("Adaptive searching: there's no attempts matching desired language, search will run.")
            active_languages.append(desired_language)
            continue

        initial_attempt_timestamp, latest_attempt_timestamp = attempt_windows[desired_language]
        try:
            initial_search_timestamp = datetime.fromtimestamp(initial_attempt_timestamp)
            latest_search_timestamp = datetime.fromtimestamp(latest_attempt_timestamp)
        except (OverflowError, ValueError, OSError):
            logging.debug("Adaptive searching: unable to parse initial and latest search timestamps, search will run.")
            active_languages.append(desired_language)
            continue

        logging.debug(f"Adaptive searching: initial search date for {desired_language} is {initial_search_timestamp}")
        logging.debug(f"Adaptive searching: latest search date for {desired_language} is {latest_search_timestamp}")
        logging.debug(f"Adaptive searching: delay after initial search value: {adaptive_search_policy['delay']}")
        logging.debug(f"Adaptive searching: delta between latest search and now value: {adaptive_search_policy['delta']}")

        if initial_search_timestamp + adaptive_search_policy["delay"] > adaptive_search_policy["now"]:
            logging.debug(f"Adaptive searching: it's been less than {settings.general.adaptive_searching_delay} since "
                          f"initial search, search will run.")
            active_languages.append(desired_language)
        elif latest_search_timestamp + adaptive_search_policy["delta"] <= adaptive_search_policy["now"]:
            logging.debug(
                f"Adaptive searching: it's been more than {settings.general.adaptive_searching_delta} since "
                f"latest search, search will run.")
            active_languages.append(desired_language)
        else:
            logging.debug(
                f"Adaptive searching: it's been less than {settings.general.adaptive_searching_delta} since "
                f"latest search, we're not ready to search yet.")

    return active_languages


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


def update_failed_attempts(desired_languages, attempt_string):
    if isinstance(desired_languages, str):
        desired_languages = [desired_languages]
    else:
        desired_languages = list(dict.fromkeys(desired_languages))

    if not desired_languages:
        return str([])

    desired_languages_set = set(desired_languages)

    try:
        attempts = _get_attempts(attempt_string)
        logging.debug(f"Adaptive searching: current attempts value is {attempts}")
    except (SyntaxError, ValueError, TypeError):
        logging.debug("Adaptive searching: failed to parse attempts value, we'll use an empty list.")
        attempts = []

    initial_attempts = {}
    filtered_attempts = []
    for attempt in attempts:
        if not isinstance(attempt, (list, tuple)) or len(attempt) < 2:
            continue

        desired_language, timestamp = attempt[0], attempt[1]
        if desired_language in desired_languages_set:
            previous_attempt = initial_attempts.get(desired_language)
            if previous_attempt is None or timestamp < previous_attempt[1]:
                initial_attempts[desired_language] = [desired_language, timestamp]
        else:
            filtered_attempts.append([desired_language, timestamp])

    for desired_language in desired_languages:
        initial_attempt = initial_attempts.get(desired_language)
        if initial_attempt is not None:
            filtered_attempts.append(initial_attempt)

    current_timestamp = datetime.timestamp(datetime.now())
    for desired_language in desired_languages:
        filtered_attempts.append([desired_language, current_timestamp])

    updated_attempts = sorted(filtered_attempts, key=lambda x: x[0])
    logging.debug(f"Adaptive searching: updated attempts that will be saved to database is {updated_attempts}")

    return str(updated_attempts)


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

    return update_failed_attempts([desired_language], attempt_string)
