"""Audio post-processing helpers for theme.mp3 files."""

import logging
import re
import subprocess
from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2

logger = logging.getLogger(__name__)

LOUDNORM_TARGET_LUFS = -24
_YEAR_SUFFIX_PATTERN = re.compile(r'\s*\((?:19|20)\d{2}\)\s*$')


def normalize_album_title(value):
    """Return a display title without a trailing year suffix like ``(2020)``."""
    title = str(value or '').strip()
    if not title:
        return 'Unknown'
    return _YEAR_SUFFIX_PATTERN.sub('', title).strip() or title


def extract_genre(provider, item):
    """Extract a preferred genre string from provider-specific item metadata."""
    if provider == 'plex' and item is not None:
        genres = getattr(item, 'genres', None) or []
        for genre in genres:
            tag = getattr(genre, 'tag', None)
            if tag:
                return str(tag)
    if provider == 'jellyfin' and isinstance(item, dict):
        genres = item.get('Genres') or []
        if isinstance(genres, list):
            for genre in genres:
                if genre:
                    return str(genre)
    return 'Soundtrack'


def build_theme_metadata(provider, item, title_fallback):
    """Build normalized ID3 metadata for a theme track."""
    if provider == 'plex' and item is not None:
        raw_title = getattr(item, 'title', None) or title_fallback
        year = getattr(item, 'year', None)
    elif provider == 'jellyfin' and isinstance(item, dict):
        raw_title = item.get('Name') or title_fallback
        year = item.get('ProductionYear')
    else:
        raw_title = title_fallback
        year = None

    album_title = normalize_album_title(raw_title)
    return {
        'title': f'{album_title} Theme',
        'album': album_title,
        'genre': extract_genre(provider, item),
        'year': str(year) if year else None,
    }


def normalize_theme_audio(theme_path):
    """Normalize a theme MP3 to broadcast loudness (target -24 LUFS)."""
    source_path = Path(theme_path)
    normalized_path = source_path.with_suffix('.normalized.mp3')
    command = [
        'ffmpeg',
        '-y',
        '-hide_banner',
        '-loglevel',
        'error',
        '-i',
        str(source_path),
        '-vn',
        '-af',
        f'loudnorm=I={LOUDNORM_TARGET_LUFS}:TP=-2:LRA=11',
        '-codec:a',
        'libmp3lame',
        '-b:a',
        '192k',
        str(normalized_path),
    ]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        logger.warning('Audio normalization skipped for %s: %s', source_path, exc)
        normalized_path.unlink(missing_ok=True)
        return False
    if result.returncode != 0:
        logger.warning('Audio normalization failed for %s: %s', source_path, result.stderr.strip())
        normalized_path.unlink(missing_ok=True)
        return False

    normalized_path.replace(source_path)
    return True


def apply_theme_id3_tags(theme_path, metadata, artwork_bytes=None, artwork_mime=None):
    """Write ID3 tags (and optional cover art) onto a theme MP3."""
    audio_path = Path(theme_path)
    try:
        try:
            tags = ID3(str(audio_path))
        except ID3NoHeaderError:
            tags = ID3()

        tags.delall('TIT2')
        tags.delall('TALB')
        tags.delall('TCON')
        tags.delall('TDRC')
        tags.delall('APIC')

        tags.add(TIT2(encoding=3, text=metadata.get('title') or 'Unknown Theme'))
        tags.add(TALB(encoding=3, text=metadata.get('album') or 'Unknown'))
        tags.add(TCON(encoding=3, text=metadata.get('genre') or 'Soundtrack'))
        year = metadata.get('year')
        if year:
            tags.add(TDRC(encoding=3, text=year))

        if artwork_bytes:
            tags.add(APIC(
                encoding=3,
                mime=(artwork_mime or 'image/jpeg'),
                type=3,
                desc='Cover',
                data=artwork_bytes,
            ))

        tags.save(str(audio_path), v2_version=3)
        return True
    except Exception as exc:
        logger.warning('ID3 tag injection failed for %s: %s', audio_path, exc)
        return False
