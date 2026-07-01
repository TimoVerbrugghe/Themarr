"""Tests for app/auth.py — authentication, sessions, API key, and init endpoint."""
import os
from unittest.mock import patch

import pytest

from tests.helpers import make_mock_show


class TestAuthLogin:
    def test_login_with_missing_credentials_mode_returns_503(self, app):
        with app.test_client() as c:
            resp = c.post('/api/auth/login', json={})
        assert resp.status_code == 503

    def test_login_with_missing_body_returns_503(self, app):
        with app.test_client() as c:
            resp = c.post('/api/auth/login')
        assert resp.status_code == 503

    def test_login_sets_session_that_authenticates_runtime_endpoint(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                resp = c.get('/api/settings/runtime')
        assert resp.status_code == 200


class TestAuthLogout:
    def test_logout_clears_session(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'API_KEY': 'logout-key'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                # Confirm authenticated
                assert c.get('/api/settings/runtime').status_code == 200
                # Logout
                logout_resp = c.post('/api/auth/logout')
                assert logout_resp.status_code == 200
                # Should now be unauthenticated
                assert c.get('/api/settings/runtime').status_code == 401


class TestSettingsRuntime:
    def test_generated_api_key_warning_does_not_log_secret(self):
        from app import web_app, auth
        with patch.object(auth.logger, 'warning') as mock_warning:
            web_app._log_generated_api_key_warning()

        mock_warning.assert_called_once_with(
            'API_KEY is not set; a one-time startup API key was generated. '
            'Open the Settings page in the web app after signing in to view it, '
            'or set API_KEY to a stable value to avoid rotation on restart.',
        )

    def test_runtime_settings_requires_authentication(self, app):
        unauthenticated_client = app.test_client()
        resp = unauthenticated_client.get('/api/settings/runtime')
        assert resp.status_code == 401

    def test_runtime_settings_returns_generated_key_when_env_missing(self, client):
        with patch.dict(os.environ, {'API_KEY': ''}, clear=False):
            resp = client.get('/api/settings/runtime')
        assert resp.status_code == 200
        data = resp.get_json()
        assert 'api_key' in data
        assert data['api_key_configured'] is False
        assert data['api_key_generated'] is True
        assert data['background_worker_count'] == 4
        assert data['library_page_size'] == 200
        assert data['library_page_size_max'] == 500
        assert data['poster_cache_max_items'] == 500

    def test_runtime_settings_prefers_configured_key(self, app):
        with patch.dict(os.environ, {'API_KEY': 'configured-key'}, clear=False):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime', headers={'X-Themarr-Api-Key': 'configured-key'})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['api_key'] == 'configured-key'
        assert data['api_key_configured'] is True
        assert data['api_key_generated'] is False

    def test_runtime_settings_includes_current_environment_values(self, app):
        with patch.dict(
            os.environ,
            {
                'API_KEY': 'configured-key',
                'DEFAULT_THEME': 'light',
                'DISABLE_AUTH': 'false',
                'NOTIFY_ON_WEBHOOK_DOWNLOAD': 'false',
                'NOTIFY_ON_WEBHOOK_FAILURE': 'true',
                'NOTIFY_ON_UI_DOWNLOAD': 'false',
            },
            clear=False,
        ):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime', headers={'X-Themarr-Api-Key': 'configured-key'})

        assert resp.status_code == 200
        data = resp.get_json()
        env_values = data['env_values']
        assert env_values['DEFAULT_THEME'] == 'light'
        assert env_values['DISABLE_AUTH'] == 'false'
        assert env_values['NOTIFY_ON_WEBHOOK_DOWNLOAD'] == 'false'
        assert env_values['NOTIFY_ON_WEBHOOK_FAILURE'] == 'true'
        assert env_values['NOTIFY_ON_UI_DOWNLOAD'] == 'false'

    def test_runtime_settings_accessible_via_session(self, app):
        with patch.dict(os.environ, {'API_KEY': 'sess-key', 'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as session_client:
                login_resp = session_client.post(
                    '/api/auth/login',
                    json={'username': 'admin', 'password': 'secret'},
                    content_type='application/json',
                )
                assert login_resp.status_code == 200
                resp = session_client.get('/api/settings/runtime')
        assert resp.status_code == 200
        assert resp.get_json()['api_key'] == 'sess-key'

    def test_runtime_settings_notification_env_defaults_when_unset(self, app):
        with patch.dict(
            os.environ,
            {
                'API_KEY': 'configured-key',
                'NOTIFY_ON_WEBHOOK_DOWNLOAD': '',
                'NOTIFY_ON_WEBHOOK_FAILURE': '',
                'NOTIFY_ON_UI_DOWNLOAD': '',
            },
            clear=False,
        ):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime', headers={'X-Themarr-Api-Key': 'configured-key'})

        assert resp.status_code == 200
        env_values = resp.get_json()['env_values']
        assert env_values['NOTIFY_ON_WEBHOOK_DOWNLOAD'] == 'true'
        assert env_values['NOTIFY_ON_WEBHOOK_FAILURE'] == 'true'
        assert env_values['NOTIFY_ON_UI_DOWNLOAD'] == 'true'

    def test_runtime_settings_jellyfin_user_id_default_when_unset(self, app):
        with patch.dict(os.environ, {'API_KEY': 'configured-key', 'JELLYFIN_USER_ID': ''}, clear=False):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime', headers={'X-Themarr-Api-Key': 'configured-key'})

        assert resp.status_code == 200
        env_values = resp.get_json()['env_values']
        assert env_values['JELLYFIN_USER_ID'] == 'first user'

    def test_runtime_settings_api_key_current_uses_generated_value_when_unset(self, client):
        with patch.dict(os.environ, {'API_KEY': ''}, clear=False):
            resp = client.get('/api/settings/runtime')

        assert resp.status_code == 200
        data = resp.get_json()
        env_values = data['env_values']
        assert data['api_key_generated'] is True
        assert env_values['API_KEY'] == data['api_key']


class TestApiAuth:
    def test_mutating_endpoint_requires_api_key_when_configured(self, app):
        unauthenticated_client = app.test_client()
        with patch.dict(os.environ, {'API_KEY': 'secret-key'}):
            resp = unauthenticated_client.post('/api/settings/refresh-cache')
        assert resp.status_code == 401

    def test_mutating_endpoint_accepts_valid_api_key_header(self, client):
        with patch.dict(os.environ, {'API_KEY': 'secret-key'}), \
             patch('app.web_app.kick_off_cache_warmup', return_value=True):
            resp = client.post('/api/settings/refresh-cache', headers={'X-Themarr-Api-Key': 'secret-key'})
        assert resp.status_code == 200

    def test_settings_runtime_requires_auth(self, app):
        unauthenticated_client = app.test_client()
        resp = unauthenticated_client.get('/api/settings/runtime')
        assert resp.status_code == 401


class TestApiInit:
    def test_init_returns_200_without_auth(self, app):
        """GET /api/init is always public."""
        with app.test_client() as c:
            resp = c.get('/api/init')
        assert resp.status_code == 200

    def test_init_misconfigured_mode_unauthenticated(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': '', 'AUTH_PASSWORD': ''}):
            with app.test_client() as c:
                data = c.get('/api/init').get_json()
        assert data['auth_required'] is False
        assert data['authenticated'] is False
        assert data['auth_mode'] == 'misconfigured'
        assert data['auth_misconfigured'] is True

    def test_init_credentials_mode_unauthenticated(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as c:
                data = c.get('/api/init').get_json()
        assert data['auth_required'] is True
        assert data['authenticated'] is False
        assert data['auth_mode'] == 'credentials'

    def test_init_disable_auth_mode(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true', 'AUTH_USERNAME': '', 'AUTH_PASSWORD': ''}):
            with app.test_client() as c:
                data = c.get('/api/init').get_json()
        assert data['auth_required'] is False
        assert data['authenticated'] is True
        assert data['auth_mode'] == 'disabled'

    def test_init_authenticated_after_session_login(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                data = c.get('/api/init').get_json()
        assert data['authenticated'] is True


class TestCredentialsLogin:
    def test_credentials_login_success(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
        assert resp.status_code == 200
        assert resp.get_json()['ok'] is True
        assert resp.get_json()['auth_mode'] == 'credentials'

    def test_credentials_login_wrong_password(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'})
        assert resp.status_code == 401

    def test_credentials_login_wrong_username(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': 'hacker', 'password': 'secret'})
        assert resp.status_code == 401

    def test_credentials_login_empty_fields(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'username': '', 'password': ''})
        assert resp.status_code == 401

    def test_credentials_login_sets_session(self, app):
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret',
                                     'DISABLE_AUTH': '', 'API_KEY': 'key'}):
            with app.test_client() as c:
                c.post('/api/auth/login', json={'username': 'admin', 'password': 'secret'})
                resp = c.get('/api/settings/runtime')
        assert resp.status_code == 200

    def test_api_key_login_rejected_in_credentials_mode(self, app):
        """Submitting only an API key in credentials mode should fail because username/password are required."""
        with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret',
                                     'DISABLE_AUTH': '', 'API_KEY': 'key'}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={'api_key': 'key'})
        assert resp.status_code == 401


class TestDisableAuth:
    def test_disable_auth_allows_mutating_requests(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true'}):
            with app.test_client() as c:
                with patch('app.web_app.kick_off_cache_warmup', return_value=True):
                    resp = c.post('/api/settings/refresh-cache')
        assert resp.status_code == 200

    def test_disable_auth_allows_settings_runtime(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true', 'API_KEY': 'key'}):
            with app.test_client() as c:
                resp = c.get('/api/settings/runtime')
        assert resp.status_code == 200

    def test_disable_auth_login_establishes_session(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': 'true', 'AUTH_USERNAME': '', 'AUTH_PASSWORD': ''}):
            with app.test_client() as c:
                resp = c.post('/api/auth/login', json={})
        assert resp.status_code == 200
        assert resp.get_json()['auth_mode'] == 'disabled'


class TestGetEndpointsRequireAuth:
    """FINDING-02 — All GET /api/ endpoints except the public allowlist require auth."""

    def test_get_libraries_requires_auth(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/libraries')
        assert resp.status_code == 401

    def test_get_status_requires_auth(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/status')
        assert resp.status_code == 401

    def test_get_youtube_search_requires_auth(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/youtube/search?q=test')
        assert resp.status_code == 401

    def test_cache_status_is_public(self, app):
        """cache/status must remain unauthenticated for the startup overlay."""
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/cache/status')
        assert resp.status_code == 200

    def test_init_is_public(self, app):
        with patch.dict(os.environ, {'DISABLE_AUTH': '', 'AUTH_USERNAME': 'u', 'AUTH_PASSWORD': 'p'}):
            with app.test_client() as c:
                resp = c.get('/api/init')
        assert resp.status_code == 200


class TestLoginRateLimit:
    """SECURITY — /api/auth/login must enforce a per-IP rate limit to prevent brute-force attacks."""

    def _fill_rate_limit(self, remote_addr):
        """Pre-fill the rate-limit counter so the next request is blocked."""
        import time
        from app.auth import _login_attempts, _login_attempt_lock, _LOGIN_RATE_LIMIT_MAX
        now = time.time()
        with _login_attempt_lock:
            _login_attempts[remote_addr] = [now] * _LOGIN_RATE_LIMIT_MAX

    def _clear_rate_limit(self, remote_addr):
        from app.auth import _login_attempts, _login_attempt_lock
        with _login_attempt_lock:
            _login_attempts.pop(remote_addr, None)

    def test_rate_limit_disabled_in_testing_mode(self, app):
        """_is_login_rate_limited returns False when TESTING=True (normal test mode)."""
        from app.auth import _is_login_rate_limited, _LOGIN_RATE_LIMIT_MAX
        import time
        remote_addr = '_test_rl_testing_mode'
        self._fill_rate_limit(remote_addr)
        try:
            with app.app_context():
                # TESTING=True is set by the fixture — rate limiter must be off.
                assert app.config.get('TESTING') is True
                result = _is_login_rate_limited(remote_addr)
            assert result is False
        finally:
            self._clear_rate_limit(remote_addr)

    def test_rate_limit_allows_initial_attempts(self, app):
        """_is_login_rate_limited returns False for the first attempt."""
        from app.auth import _is_login_rate_limited
        remote_addr = '_test_rl_allow_initial'
        self._clear_rate_limit(remote_addr)
        try:
            app.config['TESTING'] = False
            with app.app_context():
                result = _is_login_rate_limited(remote_addr)
            assert result is False
        finally:
            app.config['TESTING'] = True
            self._clear_rate_limit(remote_addr)

    def test_rate_limit_blocks_after_max_attempts(self, app):
        """_is_login_rate_limited returns True once the threshold is reached."""
        from app.auth import _is_login_rate_limited
        remote_addr = '_test_rl_block'
        self._fill_rate_limit(remote_addr)
        try:
            app.config['TESTING'] = False
            with app.app_context():
                result = _is_login_rate_limited(remote_addr)
            assert result is True
        finally:
            app.config['TESTING'] = True
            self._clear_rate_limit(remote_addr)

    def test_login_endpoint_returns_429_when_rate_limited(self, app):
        """POST /api/auth/login returns 429 when the caller is rate-limited."""
        remote_addr = '192.0.2.1'
        self._fill_rate_limit(remote_addr)
        try:
            app.config['TESTING'] = False
            with patch.dict(os.environ, {'AUTH_USERNAME': 'admin', 'AUTH_PASSWORD': 'secret', 'DISABLE_AUTH': ''}):
                with app.test_client() as c:
                    resp = c.post(
                        '/api/auth/login',
                        json={'username': 'admin', 'password': 'secret'},
                        environ_base={'REMOTE_ADDR': remote_addr},
                    )
            assert resp.status_code == 429
            data = resp.get_json()
            assert 'Too many' in data['error']
        finally:
            app.config['TESTING'] = True
            self._clear_rate_limit(remote_addr)

    def test_disable_auth_mode_bypasses_rate_limit(self, app):
        """DISABLE_AUTH=true login is never rate-limited (no credentials to brute-force)."""
        remote_addr = '192.0.2.2'
        self._fill_rate_limit(remote_addr)
        try:
            app.config['TESTING'] = False
            with patch.dict(os.environ, {'DISABLE_AUTH': 'true', 'AUTH_USERNAME': '', 'AUTH_PASSWORD': ''}):
                with app.test_client() as c:
                    resp = c.post(
                        '/api/auth/login',
                        json={},
                        environ_base={'REMOTE_ADDR': remote_addr},
                    )
            # DISABLE_AUTH short-circuits before the rate-limit check.
            assert resp.status_code == 200
        finally:
            app.config['TESTING'] = True
            self._clear_rate_limit(remote_addr)

