"""Tests for app/jellyfin_utils.py — connection helpers and auth headers."""
from unittest.mock import patch

from app.jellyfin_utils import jellyfin_session_get, jellyfin_session_post


JELLYFIN = {'url': 'http://jellyfin.example.com', 'api_key': 'testkey123', 'user_id': None}


class TestJellyfinAuthHeaders:
    """Jellyfin 12.x rejects the legacy X-Emby-Token header for API-key auth
    and requires ``Authorization: MediaBrowser Token="..."`` instead. Both
    headers must be sent so older Jellyfin servers keep working too.
    """

    def test_session_get_sends_both_auth_headers(self):
        with patch('app.jellyfin_utils.http_requests.get') as mock_get:
            jellyfin_session_get(JELLYFIN, '/Users')

        _, kwargs = mock_get.call_args
        headers = kwargs['headers']
        assert headers['X-Emby-Token'] == 'testkey123'
        assert 'Authorization' in headers
        assert 'MediaBrowser' in headers['Authorization']
        assert 'Token="testkey123"' in headers['Authorization']

    def test_session_post_sends_both_auth_headers(self):
        with patch('app.jellyfin_utils.http_requests.post') as mock_post:
            jellyfin_session_post(JELLYFIN, '/Items')

        _, kwargs = mock_post.call_args
        headers = kwargs['headers']
        assert headers['X-Emby-Token'] == 'testkey123'
        assert 'Authorization' in headers
        assert 'Token="testkey123"' in headers['Authorization']

    def test_session_get_preserves_caller_supplied_headers(self):
        with patch('app.jellyfin_utils.http_requests.get') as mock_get:
            jellyfin_session_get(JELLYFIN, '/Users', headers={'Accept': 'application/json'})

        _, kwargs = mock_get.call_args
        headers = kwargs['headers']
        assert headers['Accept'] == 'application/json'
        assert headers['X-Emby-Token'] == 'testkey123'
        assert 'Authorization' in headers

    def test_session_get_uses_full_url_and_timeout(self):
        with patch('app.jellyfin_utils.http_requests.get') as mock_get:
            jellyfin_session_get(JELLYFIN, '/Users')

        args, kwargs = mock_get.call_args
        assert args[0] == 'http://jellyfin.example.com/Users'
        assert kwargs['timeout'] == 30
