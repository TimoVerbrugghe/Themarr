"""Theme availability checking across all sources."""

import logging

import yt_dlp

from app.media_utils import _theme_file_path
from app.external_ids import extract_external_ids, extract_jellyfin_external_ids
from app.plex_utils import get_plex, plex_session_get, get_validated_plex_local_path
from app.jellyfin_utils import _normalize_provider, get_jellyfin_item_local_path
from app.youtube_utils import extract_youtube_audio_url, _clean_yt_dlp_error
from app.themerrdb_service import get_themerrdb_data_for_context

logger = logging.getLogger(__name__)


def has_nonempty_theme_file(local_path):
    """Return True when local_path/theme.mp3 exists and is non-empty."""
    if not local_path:
        return False
    theme_path = _theme_file_path(local_path)
    if not theme_path.exists():
        return False
    try:
        return theme_path.stat().st_size > 0
    except OSError:
        return False


def is_plex_theme_source_unverified(item, local_theme_exists=None):
    """Return True when Plex theme may resolve to an existing local theme file."""
    has_plex_theme = bool(getattr(item, 'theme', None))
    if not has_plex_theme:
        return False
    if local_theme_exists is None:
        local_theme_exists = has_nonempty_theme_file(get_validated_plex_local_path(item))
    return bool(local_theme_exists)


def get_external_ids_for_context(context):
    """Return normalized external IDs for a provider item context."""
    if context['provider'] == 'plex':
        return extract_external_ids(context['item'])
    return extract_jellyfin_external_ids(context['item'])


def check_themerrdb_availability_for_context(context, *, validate_preview=False):
    """Return availability metadata for a provider item's ThemerrDB theme."""
    external_ids = get_external_ids_for_context(context)
    if not any(external_ids.values()):
        return {
            'available': False,
            'reason': 'No IMDB/TMDB/TVDB identifiers are available for this item.',
            'external_ids': external_ids,
        }

    themerrdb_data = get_themerrdb_data_for_context(context)
    if not themerrdb_data:
        return {
            'available': False,
            'reason': 'No matching theme was found in ThemerrDB.',
            'external_ids': external_ids,
        }

    youtube_url = themerrdb_data.get('youtube_theme_url')
    if not youtube_url:
        return {
            'available': False,
            'reason': 'ThemerrDB did not provide a YouTube theme URL for this item.',
            'external_ids': external_ids,
        }

    if validate_preview:
        try:
            extract_youtube_audio_url(youtube_url)
        except yt_dlp.utils.DownloadError as exc:
            return {
                'available': False,
                'reason': f'Theme URL found but preview is unavailable: {_clean_yt_dlp_error(exc)}',
                'external_ids': external_ids,
                'youtube_url': youtube_url,
            }

    return {
        'available': True,
        'youtube_url': youtube_url,
        'external_ids': external_ids,
    }


def check_plex_preview_availability(item):
    """Return availability metadata for Plex source theme preview."""
    if not getattr(item, 'theme', None):
        return {'available': False, 'reason': 'No theme is available in Plex for this item.'}

    try:
        plex = get_plex()
        url = plex.url(item.theme, includeToken=True)
        response = plex_session_get(plex, url, stream=True, timeout=15)
        response.raise_for_status()
        response.close()
        source_unverified = is_plex_theme_source_unverified(item)
        payload = {'available': True, 'source_unverified': source_unverified}
        if source_unverified:
            payload['reason'] = (
                'Plex reports a theme, but this item already has a local theme.mp3. '
                'Plex may be streaming that local file instead of a Plex-hosted source.'
            )
        return payload
    except Exception as exc:
        logger.warning('Unable to stream Plex preview for item %s: %s', getattr(item, 'ratingKey', '?'), exc)
        return {'available': False, 'reason': 'Unable to stream the Plex preview right now.'}
