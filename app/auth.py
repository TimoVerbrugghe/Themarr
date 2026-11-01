"""Authentication and authorization helpers."""

import logging
import os
import hmac
from flask import request, session, jsonify

logger = logging.getLogger(__name__)


def _auth_disabled():
    """Return True when DISABLE_AUTH=true (authentication bypass mode)."""
    return (os.getenv('DISABLE_AUTH') or '').strip().lower() in {'true', '1', 'yes'}


def _get_ui_credentials():
    """Return (username, password) from AUTH_USERNAME / AUTH_PASSWORD env vars."""
    username = (os.getenv('AUTH_USERNAME') or '').strip()
    password = (os.getenv('AUTH_PASSWORD') or '').strip()
    return username, password


def _credentials_auth_configured():
    """Return True when both AUTH_USERNAME and AUTH_PASSWORD are set."""
    username, password = _get_ui_credentials()
    return bool(username and password)


def _ui_auth_misconfigured():
    """Return True when UI auth is enabled but credentials are missing."""
    return not _auth_disabled() and not _credentials_auth_configured()


def _ui_auth_warning_message():
    """Return warning text shown when UI auth credentials are not configured."""
    return (
        'Web UI authentication is enabled but AUTH_USERNAME/AUTH_PASSWORD are not both set. '
        'Set both variables, or set DISABLE_AUTH=true only when a trusted reverse proxy already enforces authentication.'
    )


def _get_auth_mode():
    """Return the active authentication mode string.

    Returns:
        'disabled'      – DISABLE_AUTH=true; no credentials required.
        'credentials'   – AUTH_USERNAME + AUTH_PASSWORD are both set; login form shown.
        'misconfigured' – UI auth enabled but credentials are missing.
    """
    if _auth_disabled():
        return 'disabled'
    if _credentials_auth_configured():
        return 'credentials'
    return 'misconfigured'


def _parse_api_key():
    """Extract API key from supported auth headers."""
    header_key = (request.headers.get('X-Themarr-Api-Key') or '').strip()
    if header_key:
        return header_key
    auth_header = (request.headers.get('Authorization') or '').strip()
    if auth_header.startswith('Bearer '):
        return auth_header[7:].strip()
    return ''


def _get_api_key():
    """Return only the explicitly configured API key."""
    return (os.getenv('API_KEY') or '').strip()


def _check_api_request_auth():
    """Validate API key auth for protected API routes.

    Accepts either a valid Flask session (established via POST /api/auth/login)
    or the API key supplied in the X-Themarr-Api-Key / Authorization header.
    Browser workflows retain session/reverse-proxy auth. Agent routes and
    requests supplying API credentials always require a configured key.
    """
    expected_key = _get_api_key()
    provided_key = _parse_api_key()
    programmatic = request.path.startswith('/api/agent/') or bool(
        request.headers.get('Authorization') or request.headers.get('X-Themarr-Api-Key')
    )
    if programmatic:
        if not expected_key:
            logger.warning('Rejected API request: API_KEY is not configured')
            return jsonify({'error': 'API access is disabled; configure API_KEY to enable it'}), 503
        if provided_key and hmac.compare_digest(provided_key.encode('utf-8'), expected_key.encode('utf-8')):
            return None
        return jsonify({'error': 'Unauthorized API request'}), 401
    if _auth_disabled():
        return None
    if session.get('authenticated'):
        return None
    if not expected_key:
        logger.warning('Rejected API request: API_KEY is not configured')
        return jsonify({'error': 'API access is disabled; configure API_KEY to enable it'}), 503
    return jsonify({'error': 'Unauthorized API request'}), 401


def _check_webhook_basic_auth():
    """Validate optional webhook Basic Auth credentials."""
    expected_username = (os.getenv('WEBHOOK_USERNAME') or '').strip()
    expected_password = (os.getenv('WEBHOOK_PASSWORD') or '').strip()
    if not expected_username and not expected_password:
        return None
    if not expected_username or not expected_password:
        logger.error(
            'Webhook Basic Auth is partially configured (only one of '
            'WEBHOOK_USERNAME / WEBHOOK_PASSWORD is set); rejecting all '
            'webhook requests until both are set or both are cleared.'
        )
        return jsonify({'error': 'Webhook authentication misconfigured on server'}), 503
    auth = request.authorization
    if not auth:
        return jsonify({'error': 'Webhook authentication required'}), 401
    username_ok = hmac.compare_digest(auth.username or '', expected_username)
    password_ok = hmac.compare_digest(auth.password or '', expected_password)
    if username_ok and password_ok:
        return None
    return jsonify({'error': 'Invalid webhook credentials'}), 401


def _get_settings_env_values():
    """Return current raw environment values shown in the Settings page."""
    settings_vars = (
        'PLEX_URL',
        'PLEX_TOKEN',
        'JELLYFIN_URL',
        'JELLYFIN_API_KEY',
        'JELLYFIN_USER_ID',
        'DEFAULT_THEME',
        'DEFAULT_VIEW',
        'FLASK_DEBUG',
        'API_KEY',
        'AUTH_USERNAME',
        'AUTH_PASSWORD',
        'DISABLE_AUTH',
        'PUSHOVER_APP_TOKEN',
        'PUSHOVER_USER_KEY',
        'NOTIFY_ON_WEBHOOK_DOWNLOAD',
        'NOTIFY_ON_WEBHOOK_FAILURE',
        'NOTIFY_ON_UI_DOWNLOAD',
        'WEBHOOK_USERNAME',
        'WEBHOOK_PASSWORD',
        'PLEX_RETRY_ATTEMPTS',
        'PLEX_RETRY_DELAY',
    )
    defaults_when_unset = {
        'JELLYFIN_USER_ID': 'first user',
        'API_KEY': 'disabled',
        'NOTIFY_ON_WEBHOOK_DOWNLOAD': 'true',
        'NOTIFY_ON_WEBHOOK_FAILURE': 'true',
        'NOTIFY_ON_UI_DOWNLOAD': 'true',
    }
    values = {}
    for key in settings_vars:
        raw_value = (os.getenv(key) or '').strip()
        if not raw_value and key in defaults_when_unset:
            values[key] = defaults_when_unset[key]
        else:
            values[key] = raw_value
    return values
