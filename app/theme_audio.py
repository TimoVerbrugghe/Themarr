"""Audio post-processing helpers for theme.mp3 files."""

import logging
import subprocess
from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TXXX

logger = logging.getLogger(__name__)

LOUDNORM_TARGET_LUFS = -24
NORMALIZED_MARKER_DESC = 'ThemarrNormalizedLUFS'
NORMALIZED_MARKER_VALUE = str(LOUDNORM_TARGET_LUFS)


def normalize_album_title(value):
    """Return a display title without a trailing year suffix like ``(2020)``."""
    title = str(value or '').strip()
    if not title:
        return 'Unknown'
    if not title.endswith(')'):
        return title
    open_paren = title.rfind('(')
    if open_paren <= 0:
        return title
    if title[open_paren - 1] != ' ':
        return title
    year = title[open_paren + 1:-1]
    if len(year) == 4 and year.isdigit() and year[:2] in {'19', '20'}:
        normalized = title[:open_paren - 1].strip()
        if normalized:
            return normalized
    return title


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
    normalized_path = source_path.with_name(f'{source_path.stem}.normalized.mp3')
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
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    except OSError as exc:
        logger.warning('Audio normalization skipped for %s: %s', source_path, exc)
        normalized_path.unlink(missing_ok=True)
        return False
    except subprocess.TimeoutExpired:
        logger.warning('Audio normalization timed out for %s', source_path)
        normalized_path.unlink(missing_ok=True)
        return False
    if result.returncode != 0:
        logger.warning('Audio normalization failed for %s: %s', source_path, result.stderr.strip())
        normalized_path.unlink(missing_ok=True)
        return False

    normalized_path.replace(source_path)
    _mark_theme_audio_normalized(source_path)
    return True


def is_theme_audio_normalized(theme_path):
    """Return True when a theme has already been normalized by Themarr."""
    audio_path = Path(theme_path)
    try:
        tags = ID3(str(audio_path))
    except (ID3NoHeaderError, Exception):
        return False

    for frame in tags.getall('TXXX'):
        if getattr(frame, 'desc', None) != NORMALIZED_MARKER_DESC:
            continue
        values = getattr(frame, 'text', None) or []
        if NORMALIZED_MARKER_VALUE in [str(value) for value in values]:
            return True
    return False


def has_theme_metadata_tags(theme_path):
    """Return True when core theme metadata tags are already present."""
    audio_path = Path(theme_path)
    try:
        tags = ID3(str(audio_path))
    except (ID3NoHeaderError, Exception):
        return False

    return bool(tags.getall('TIT2') and tags.getall('TALB') and tags.getall('TCON'))


def _mark_theme_audio_normalized(audio_path):
    """Persist a marker so future bulk operations can skip re-normalization."""
    try:
        try:
            tags = ID3(str(audio_path))
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall('TXXX:' + NORMALIZED_MARKER_DESC)
        tags.add(TXXX(encoding=3, desc=NORMALIZED_MARKER_DESC, text=[NORMALIZED_MARKER_VALUE]))
        tags.save(str(audio_path), v2_version=3)
        return True
    except Exception as exc:
        logger.warning('Failed to mark normalized audio for %s: %s', audio_path, exc)
        return False


def apply_theme_id3_tags(theme_path, metadata, artwork_bytes=None, artwork_mime=None, preserve_existing=False):
    """Write ID3 tags (and optional cover art) onto a theme MP3."""
    audio_path = Path(theme_path)
    try:
        try:
            tags = ID3(str(audio_path))
        except ID3NoHeaderError:
            tags = ID3()

        if preserve_existing:
            if not tags.getall('TIT2'):
                tags.add(TIT2(encoding=3, text=metadata.get('title') or 'Unknown Theme'))
            if not tags.getall('TALB'):
                tags.add(TALB(encoding=3, text=metadata.get('album') or 'Unknown'))
            if not tags.getall('TCON'):
                tags.add(TCON(encoding=3, text=metadata.get('genre') or 'Soundtrack'))
        else:
            tags.delall('TIT2')
            tags.delall('TALB')
            tags.delall('TCON')
            tags.delall('TDRC')
            tags.delall('APIC')
            tags.add(TIT2(encoding=3, text=metadata.get('title') or 'Unknown Theme'))
            tags.add(TALB(encoding=3, text=metadata.get('album') or 'Unknown'))
            tags.add(TCON(encoding=3, text=metadata.get('genre') or 'Soundtrack'))
        year = metadata.get('year')
        if year and (not preserve_existing or not tags.getall('TDRC')):
            tags.add(TDRC(encoding=3, text=year))

        if artwork_bytes and (not preserve_existing or not tags.getall('APIC')):
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
