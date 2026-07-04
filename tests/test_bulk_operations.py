"""Tests for app/bulk_operations.py — bulk theme download."""
from unittest.mock import MagicMock, patch

from mutagen.id3 import ID3, TALB, TCON, TIT2, TXXX

from tests.helpers import make_mock_show


class TestBulkDownload:
    def test_bulk_download_success(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'audio_data']
        mock_plex._session.get.return_value = mock_resp

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['success']) == 1
        assert data['success'][0]['ratingKey'] == 1

    def test_bulk_download_missing_rating_keys(self, client, mock_plex):
        resp = client.post('/api/bulk/theme/download', json={})
        assert resp.status_code == 400

    def test_bulk_download_empty_list(self, client, mock_plex):
        resp = client.post('/api/bulk/theme/download', json={'ratingKeys': []})
        assert resp.status_code == 400

    def test_bulk_download_too_many_items(self, client, mock_plex):
        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': list(range(101))})
        assert resp.status_code == 400

    def test_bulk_download_skips_existing(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'existing')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        data = resp.get_json()
        assert len(data['skipped']) == 1

    def test_bulk_download_no_plex_theme(self, client, mock_plex, tmp_path):
        show = make_mock_show(has_theme=False)
        mock_plex.fetchItem.return_value = show

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        data = resp.get_json()
        assert len(data['no_theme']) == 1

    def test_bulk_download_overwrite(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        (show_dir / 'theme.mp3').write_bytes(b'old')
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'new_audio']
        mock_plex._session.get.return_value = mock_resp

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': True})
        data = resp.get_json()
        assert len(data['success']) == 1

    def test_bulk_download_updates_cached_item_state(self, client, mock_plex, tmp_path):
        from app import web_app

        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show
        mock_plex.url.return_value = 'http://plex/theme.mp3'
        mock_resp = MagicMock()
        mock_resp.iter_content.return_value = [b'new_audio']
        mock_plex._session.get.return_value = mock_resp

        web_app._library_cache[1] = [{
            'ratingKey': 1,
            'title': 'Test Show',
            'has_local_theme': False,
            'has_plex_theme': True,
        }]

        resp = client.post('/api/bulk/theme/download',
                           json={'ratingKeys': [1], 'overwrite': False})
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['success']) == 1
        assert web_app._library_cache[1][0]['has_local_theme'] is True


class TestBulkPostprocess:
    def test_bulk_postprocess_success(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        theme_path = show_dir / 'theme.mp3'
        theme_path.write_bytes(b'audio_data')
        show = make_mock_show(location=str(show_dir))
        show.thumb = None
        mock_plex.fetchItem.return_value = show

        with patch('app.bulk_operations.normalize_theme_audio', return_value=True):
            resp = client.post('/api/bulk/theme/postprocess', json={'ratingKeys': [1]})

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['processed']) == 1
        assert data['processed'][0]['normalized'] is True
        assert data['processed'][0]['tagged'] is True

    def test_bulk_postprocess_skips_when_already_normalized_and_tagged(self, client, mock_plex, tmp_path):
        show_dir = tmp_path / 'Test Show (2020)'
        show_dir.mkdir()
        theme_path = show_dir / 'theme.mp3'
        theme_path.write_bytes(b'audio_data')
        tags = ID3()
        tags.add(TIT2(encoding=3, text='Existing Theme'))
        tags.add(TALB(encoding=3, text='Existing Album'))
        tags.add(TCON(encoding=3, text='Soundtrack'))
        tags.add(TXXX(encoding=3, desc='ThemarrNormalizedLUFS', text=['-24']))
        tags.save(str(theme_path), v2_version=3)

        show = make_mock_show(location=str(show_dir))
        mock_plex.fetchItem.return_value = show

        with patch('app.bulk_operations.normalize_theme_audio') as mock_normalize:
            resp = client.post('/api/bulk/theme/postprocess', json={'ratingKeys': [1]})

        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data['skipped']) == 1
        assert data['skipped'][0]['reason'] == 'already_normalized,already_tagged'
        mock_normalize.assert_not_called()
