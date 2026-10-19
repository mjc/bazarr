# coding=utf-8

import os
import logging
import hashlib
import hmac
from collections.abc import Mapping
from typing import Protocol, TypeVar

from flask import Request

from charset_normalizer import detect
from bs4 import UnicodeDammit

from app.config import settings
from utilities.subtitle_paths import subtitle_directories


bool_map = {"True": True, "False": False, "true": True, "false": False}

_FieldValue = TypeVar('_FieldValue', covariant=True)


class _FieldLookup(Protocol[_FieldValue]):
    def __getitem__(self, key: str, /) -> _FieldValue: ...


def has_changed_fields(current: _FieldLookup[_FieldValue], incoming: Mapping[str, _FieldValue]) -> bool:
    for key in incoming:
        try:
            previous = current[key]
        except KeyError:
            return True
        if previous != incoming[key]:
            return True
    return False


def check_credentials(user: str, pw: str, request: Request, log_success: bool = True) -> bool:
    """
    Validate submitted credentials against the configured username and
    password.

    The client IP address is resolved from the ``HTTP_X_FORWARDED_FOR``
    or ``HTTP_X_REAL_IP`` request headers, falling back to the remote
    address of the request. The submitted password is hashed with MD5
    and compared in constant time against the configured password hash
    using :func:`hmac.compare_digest`. Both successful and failed
    authentication attempts are logged together with the resolved IP
    address, with logging of successful attempts being optional.

    :param user: Username submitted by the client.
    :param pw: Plain-text password submitted by the client.
    :param request: Incoming request used to determine the client IP
        address for logging purposes.
    :param log_success: Whether a successful authentication attempt
        should be written to the log.
    :return: ``True`` if both the username and the hashed password match
        the configured credentials, ``False`` otherwise.
    """
    forwarded_for_ip_addr = request.environ.get('HTTP_X_FORWARDED_FOR')
    real_ip_addr = request.environ.get('HTTP_X_REAL_IP')
    ip_addr = forwarded_for_ip_addr or real_ip_addr or request.remote_addr
    username = settings.auth.username
    password = settings.auth.password
    submitted_hash = hashlib.md5(f"{pw}".encode('utf-8')).hexdigest()
    if hmac.compare_digest(submitted_hash, str(password or '')) and user == username:
        if log_success:
            logging.info(f'Successful authentication from {ip_addr} for user {user}')
        return True
    else:
        logging.info(f'Failed authentication from {ip_addr} for user {user}')
        return False


def get_subtitle_destination_folder() -> str | None:
    """
    Determine the custom destination folder configured for subtitle files.

    Reads the application settings and returns the trimmed custom subfolder
    path when a custom subfolder has been configured and the subfolder mode
    is not set to ``'current'``. In any other case no custom destination is
    considered to be defined.

    :return: The stripped custom subfolder path, or ``None`` when no custom
        subfolder is configured or the subfolder mode is ``'current'``.
    """
    fld_custom = str(settings.general.subfolder_custom).strip() if (settings.general.subfolder_custom and
                                                                    settings.general.subfolder != 'current') else None
    return fld_custom


def get_target_folder(file_path: str) -> str | None:
    """
    Determine the directory in which subtitles for the given media file should
    be stored, based on the configured subfolder settings.

    When the subfolder setting is ``"current"`` or no custom subfolder is
    configured, no dedicated target folder is used and ``None`` is returned.
    Otherwise, the target folder is derived from the custom subfolder value:
    for the ``"absolute"`` mode the custom value is used as-is, and for the
    ``"relative"`` mode it is joined with the directory containing the media
    file. Any other mode yields ``None``. The resulting path is normalized to
    unicode and created on disk if it does not already exist; if creation
    fails, the error is logged and ``None`` is returned.

    :param file_path: Path to the media file for which the subtitle target
        folder should be resolved.
    :return: The resolved (and existing) target directory, or ``None`` if no
        dedicated subfolder applies, the subfolder mode is unknown, or the
        directory could not be created.
    """
    directories = subtitle_directories(file_path)
    if len(directories) < 2:
        return None
    folder = directories[-1]
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        logging.error('BAZARR is unable to create directory to save subtitles: %s', folder)
        return None
    return folder


def force_unicode(s: str | bytes) -> str:
    """
    Ensure a string is unicode, not encoded; used for enforcing file paths to be unicode upon saving a subtitle,
    to prevent encoding issues when saving a subtitle to a non-ascii path.
    :param s: string
    :return: unicode string
    """
    if isinstance(s, str):
        return s

    try:
        return s.decode("utf-8")
    except UnicodeDecodeError:
        encoding = detect(s).get('encoding') or 'utf-8'
        try:
            return s.decode(encoding)
        except UnicodeDecodeError:
            return UnicodeDammit(s).unicode_markup or s.decode(encoding, errors='replace')
