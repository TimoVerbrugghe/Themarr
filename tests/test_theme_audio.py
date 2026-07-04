"""Tests for app/theme_audio.py."""

from pathlib import Path
from unittest.mock import patch

from mutagen.id3 import ID3

from app.theme_audio import (
    apply_theme_id3_tags,
    build_theme_metadata,
    normalize_album_title,
    normalize_theme_audio,
)


class TestThemeAudioNormalization:
    def test_normalize_theme_audio_uses_loudnorm_target(self, tmp_path):
        theme_path = tmp_path / 'theme.mp3'
        normalized_path = tmp_path / 'theme.normalized.mp3'
        theme_path.write_bytes(b'fake_mp3_data')
        normalized_path.write_bytes(b'normalized')

        with patch('app.theme_audio.subprocess.run') as mock_run:
            mock_run.return_value.returncode = 0
            ok = normalize_theme_audio(theme_path)

        assert ok is True
        command = mock_run.call_args.args[0]
        assert 'loudnorm=I=-24:TP=-2:LRA=11' in command
        assert command[0] == 'ffmpeg'
        assert str(theme_path) == command[6]
        assert command[-1] == str(normalized_path)


class TestThemeAudioMetadata:
    def test_normalize_album_title_removes_trailing_year(self):
        assert normalize_album_title('The Office (2005)') == 'The Office'

    def test_build_theme_metadata_for_plex_item(self):
        item = type('PlexItem', (), {'title': 'Breaking Bad (2008)', 'year': 2008, 'genres': [type('Genre', (), {'tag': 'Drama'})()]})()
        metadata = build_theme_metadata('plex', item, 'Fallback')
        assert metadata['title'] == 'Breaking Bad Theme'
        assert metadata['album'] == 'Breaking Bad'
        assert metadata['genre'] == 'Drama'
        assert metadata['year'] == '2008'

    def test_apply_theme_id3_tags_writes_expected_frames(self, tmp_path):
        theme_path = tmp_path / 'theme.mp3'
        theme_path.write_bytes(b'placeholder_audio')

        apply_theme_id3_tags(
            theme_path,
            {
                'title': 'Dexter Theme',
                'album': 'Dexter',
                'genre': 'Soundtrack',
                'year': '2006',
            },
            artwork_bytes=b'fakeimage',
            artwork_mime='image/jpeg',
        )

        tags = ID3(str(theme_path))
        assert str(tags['TIT2']) == 'Dexter Theme'
        assert str(tags['TALB']) == 'Dexter'
        assert str(tags['TCON']) == 'Soundtrack'
        assert str(tags['TDRC']) == '2006'
        assert tags.getall('APIC')
