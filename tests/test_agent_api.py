"""Tests for authenticated agent discovery, source options and downloads."""

from unittest.mock import patch

import pytest

from app.cache import set_library_cache_for_section
from tests.helpers import make_agent_library_item as library_item, make_mock_show

PREFIX = '/api/agent/v1'


class TestAgentDiscovery:
    @pytest.mark.parametrize('path,method', [
        ('/capabilities', 'get'),
        ('/libraries/plex/1/items', 'get'),
        ('/items/plex/1/options', 'get'),
        ('/items/plex/1/download', 'post'),
    ])
    def test_should_require_auth_for_all_agent_routes(self, app, monkeypatch, path, method):
        monkeypatch.setenv('DISABLE_AUTH', 'false')
        response = getattr(app.test_client(), method)(PREFIX + path)
        assert response.status_code == 401

    def test_should_describe_agent_api(self, client):
        response = client.get(PREFIX + '/capabilities')
        assert response.status_code == 200
        assert response.json['downloads'] == {'synchronous': True, 'overwrite_default': False}

    def test_should_filter_before_paginating_and_distinguish_local_from_remote(self, client):
        set_library_cache_for_section(1, [
            library_item('1', 'Alpha', local=True),
            library_item('2', 'Beta', remote=True),
            library_item('3', 'BETA movie', media_type='movie'),
            library_item('4', 'Beta series'),
        ])
        response = client.get(
            PREFIX + '/libraries/plex/1/items?q=beta&theme_status=missing&type=show&page_size=1',
        )
        assert response.status_code == 200
        assert [item['id'] for item in response.json['items']] == ['2']
        assert response.json['pagination'] == {'page': 1, 'page_size': 1, 'total': 2, 'has_more': True}
        assert response.json['summary'] == {'library_total': 4, 'local_themes': 1, 'missing_local_themes': 3}
        assert 'local_path' not in response.json['items'][0]
        second_page = client.get(
            PREFIX + '/libraries/plex/1/items?q=beta&theme_status=missing&type=show&page_size=1&page=2',
        )
        assert [item['id'] for item in second_page.json['items']] == ['4']
        assert second_page.json['pagination']['has_more'] is False

    def test_should_return_only_present_themes(self, client):
        set_library_cache_for_section(1, [library_item('1', 'Alpha', local=True), library_item('2', 'Beta')])
        response = client.get(PREFIX + '/libraries/plex/1/items?theme_status=present')
        assert [item['id'] for item in response.json['items']] == ['1']

    def test_should_use_jellyfin_library_cache_and_return_empty_out_of_range_page(self, client):
        item = {**library_item('abc', 'Jellyfin show'), 'provider': 'jellyfin'}
        set_library_cache_for_section('jellyfin:abc', [item])
        response = client.get(PREFIX + '/libraries/jellyfin/abc/items?page=2')
        assert response.json['items'] == []
        assert response.json['pagination']['total'] == 1

    @pytest.mark.parametrize('query', [
        'page=0', 'page=hello', 'page=-1', 'page=1.5', 'page_size=101', 'page_size=0',
        'theme_status=unknown', 'type=episode', 'q=' + 'a' * 201,
    ])
    def test_should_reject_invalid_filters(self, client, query):
        response = client.get(PREFIX + '/libraries/plex/1/items?' + query)
        assert response.status_code == 400
        assert isinstance(response.json['error'], str)

    @pytest.mark.parametrize('provider,identifier', [('unknown', '1'), ('plex', 'abc'), ('jellyfin', '..')])
    def test_should_reject_invalid_identifiers(self, client, provider, identifier):
        response = client.get(f'{PREFIX}/libraries/{provider}/{identifier}/items')
        assert response.status_code == 400

    def test_should_surface_provider_failure(self, client):
        with patch('app.web_app.build_library_items', side_effect=RuntimeError('private connection details')):
            response = client.get(PREFIX + '/libraries/plex/1/items')
        assert response.status_code == 500
        assert response.json == {'error': 'Failed to discover library items'}


class TestAgentOptions:
    @pytest.mark.parametrize('provider', ['plex', 'jellyfin'])
    def test_should_check_fresh_local_state_and_describe_sources(self, client, tmp_path, provider):
        (tmp_path / 'theme.mp3').write_bytes(b'theme')
        item = make_mock_show()
        with (
            patch('app.agent_api.check_themerrdb_availability_for_context', return_value={'available': False}),
            patch('app.agent_api.get_external_ids_for_context', return_value={}),
            patch('app.web_app.get_plex') as get_plex,
            patch('app.web_app.get_jellyfin_item', return_value=(None, None, {'Id': '1', 'Name': 'Test Show'})),
            patch('app.web_app.get_item_local_path', return_value=tmp_path),
            patch('app.web_app.get_jellyfin_item_local_path', return_value=tmp_path),
        ):
            get_plex.return_value.fetchItem.return_value = item
            response = client.get(f'{PREFIX}/items/{provider}/1/options')
        assert response.status_code == 200
        assert response.json['has_local_theme'] is True
        assert response.json['overwrite_required'] is True
        assert response.json['sources']['youtube']['requires_url'] is True
        assert response.json['sources']['plex']['available'] == (provider == 'plex')
        assert response.json['sources']['themerrdb']['playability_verified'] is False

    def test_should_reject_invalid_themerrdb_url(self, client, mock_plex, tmp_path):
        mock_plex.fetchItem.return_value = make_mock_show()
        with (
            patch('app.web_app.get_item_local_path', return_value=tmp_path),
            patch('app.agent_api.check_themerrdb_availability_for_context', return_value={
                'available': True, 'youtube_url': 'https://untrusted.example/audio',
            }),
        ):
            response = client.get(PREFIX + '/items/plex/1/options')
        assert response.status_code == 502


class TestAgentDownload:
    def test_should_not_expose_validation_exception_details(self, client):
        with patch(
            'app.agent_api.normalize_youtube_trim_window',
            side_effect=ValueError('private internal validation details'),
        ):
            response = client.post(PREFIX + '/items/plex/1/download', json={
                'source': 'youtube', 'url': 'https://youtu.be/abc', 'start_time': '10',
            })
        assert response.status_code == 400
        assert response.json == {'error': 'Invalid theme download parameters'}

    @pytest.mark.parametrize('body', [
        [], None, {'source': []}, {'source': 'unknown'},
        {'source': 'youtube', 'url': 'https://untrusted.example'},
        {'source': 'youtube', 'url': 123},
        {'source': 'youtube', 'url': 'https://youtu.be/abc', 'start_time': 'invalid'},
        {'source': 'themerrdb', 'overwrite': 'false'},
        {'source': 'themerrdb', 'url': 'https://youtu.be/abc'},
    ])
    def test_should_reject_invalid_download_requests(self, client, body):
        response = client.post(PREFIX + '/items/plex/1/download', json=body)
        assert response.status_code == 400
        assert isinstance(response.json['error'], str)

    def test_should_reject_plex_source_for_jellyfin(self, client):
        response = client.post(PREFIX + '/items/jellyfin/abc/download', json={'source': 'plex'})
        assert response.status_code == 400

    @pytest.mark.parametrize('provider', ['plex', 'jellyfin'])
    def test_should_download_youtube_with_shared_pipeline(self, client, mock_plex, tmp_path, provider):
        media = tmp_path / 'media'
        media.mkdir()
        downloaded = tmp_path / 'downloaded.mp3'
        downloaded.write_bytes(b'ID3theme')
        item = make_mock_show()
        mock_plex.fetchItem.return_value = item
        with (
            patch('app.web_app.get_jellyfin_item', return_value=(None, None, {'Id': '1', 'Name': 'Test Show'})),
            patch('app.web_app.get_item_local_path', return_value=media),
            patch('app.web_app.get_jellyfin_item_local_path', return_value=media),
            patch('app.web_app.download_youtube_theme_mp3', return_value=downloaded) as download,
            patch('app.web_app._postprocess_theme_audio') as postprocess,
            patch('app.web_app._trigger_metadata_refresh') as refresh,
            patch('app.web_app.send_pushover_notification'),
            patch('app.web_app.sync_cached_item_theme_state', return_value=({'id': '1', 'has_local_theme': True}, True)),
        ):
            response = client.post(f'{PREFIX}/items/{provider}/1/download', json={
                'source': 'youtube', 'url': 'https://youtu.be/abc', 'start_time': '0:10', 'end_time': '0:40',
            })
        assert response.status_code == 200
        assert response.json['success'] is True
        assert (media / 'theme.mp3').read_bytes() == b'ID3theme'
        assert download.call_args.kwargs == {'start_seconds': 10, 'end_seconds': 40}
        assert postprocess.call_args.kwargs['provider'] == provider
        assert refresh.call_args.args[0] == provider

    @pytest.mark.parametrize('source', ['plex', 'themerrdb', 'youtube'])
    def test_should_preserve_existing_theme_without_explicit_overwrite(self, client, mock_plex, tmp_path, source):
        (tmp_path / 'theme.mp3').write_bytes(b'original')
        mock_plex.fetchItem.return_value = make_mock_show()
        with (
            patch('app.web_app.get_item_local_path', return_value=tmp_path),
            patch('app.web_app.get_validated_plex_local_path', return_value=tmp_path),
            patch('app.web_app.download_youtube_theme_mp3') as download,
        ):
            body = {'source': source}
            if source == 'youtube':
                body['url'] = 'https://youtu.be/abc'
            response = client.post(PREFIX + '/items/plex/1/download', json=body)
        assert response.status_code == 409
        assert (tmp_path / 'theme.mp3').read_bytes() == b'original'
        download.assert_not_called()

    @pytest.mark.parametrize('source', ['plex', 'themerrdb', 'youtube'])
    def test_should_replace_theme_only_when_overwrite_is_true(self, client, mock_plex, tmp_path, source):
        from unittest.mock import MagicMock

        (tmp_path / 'theme.mp3').write_bytes(b'original')
        downloaded = tmp_path / 'new.mp3'
        downloaded.write_bytes(b'ID3replacement')
        mock_plex.fetchItem.return_value = make_mock_show()
        plex_response = MagicMock()
        plex_response.iter_content.return_value = [b'ID3replacement']
        with (
            patch('app.web_app.get_item_local_path', return_value=tmp_path),
            patch('app.web_app.get_validated_plex_local_path', return_value=tmp_path),
            patch('app.web_app.plex_session_get', return_value=plex_response),
            patch('app.web_app.download_youtube_theme_mp3', return_value=downloaded),
            patch('app.web_app.get_themerrdb_data_for_context', return_value={
                'youtube_theme_url': 'https://youtu.be/abc',
            }),
            patch('app.web_app._postprocess_theme_audio'),
            patch('app.web_app._trigger_metadata_refresh'),
            patch('app.web_app.send_pushover_notification'),
            patch('app.web_app.sync_cached_item', return_value=({'id': '1'}, True)),
            patch('app.web_app.sync_cached_item_theme_state', return_value=({'id': '1'}, True)),
        ):
            body = {'source': source, 'overwrite': True}
            if source == 'youtube':
                body['url'] = 'https://youtu.be/abc'
            response = client.post(PREFIX + '/items/plex/1/download', json=body)
        assert response.status_code == 200
        assert response.json['success'] is True
        assert (tmp_path / 'theme.mp3').read_bytes() == b'ID3replacement'
