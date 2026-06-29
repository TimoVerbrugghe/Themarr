"""ThemerrDB theme metadata querying and caching."""

import logging
import re
import threading
import time

import requests

from app.external_ids import extract_external_ids, extract_jellyfin_external_ids

logger = logging.getLogger(__name__)

THEMERRDB_API_BASE = 'https://app.lizardbyte.dev/ThemerrDB'
THEMERRDB_CACHE_TTL = 24 * 3600  # 24 hours
_EXTERNAL_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')

_themerrdb_cache: dict = {}      # {cache_key: {'data': dict, 'timestamp': float}}
_themerrdb_cache_lock = threading.Lock()

# Use session to reuse connections and respect HTTP pooling
http_requests = requests.Session()


def get_themerrdb_theme_for_external_ids(item_type, external_ids):
    """Resolve ThemerrDB theme metadata from external IDs for a media type."""
    themerr_item_type = 'tv_shows' if item_type == 'show' else 'movies'
    tmdb_id = external_ids.get('tmdb')
    cache_ids = {
        external_ids.get('imdb'),
        external_ids.get('tmdb'),
        external_ids.get('tvdb'),
    }
    for database, external_id in [('imdb', external_ids.get('imdb')), ('themoviedb', tmdb_id)]:
        if external_id:
            theme_data = query_themerrdb(themerr_item_type, database, external_id)
            if theme_data:
                for cache_external_id in cache_ids:
                    if not cache_external_id:
                        continue
                    _set_cached_themerrdb(cache_external_id, theme_data, themerr_item_type)
                return theme_data
    return None


def get_themerrdb_theme_for_item(provider, item):
    """Get ThemerrDB theme metadata for a provider item (Plex or Jellyfin)."""
    if provider == 'plex':
        item_type = 'show' if getattr(item, 'type', None) == 'show' else 'movie'
        external_ids = extract_external_ids(item)
    else:
        item_type = 'show' if (item.get('Type') or '').lower() == 'series' else 'movie'
        external_ids = extract_jellyfin_external_ids(item)
    return get_themerrdb_theme_for_external_ids(item_type, external_ids)


def _get_themerrdb_cache_key(external_id, item_type=None):
    """Generate cache key for ThemerrDB query, scoped to item type to avoid cross-type collisions."""
    if item_type:
        return f'themerrdb_{item_type}_{external_id}'
    return f'themerrdb_{external_id}'


def _get_cached_themerrdb(external_id, item_type=None):
    """Return (cache_hit, data) for a ThemerrDB cache lookup."""
    cache_key = _get_themerrdb_cache_key(external_id, item_type)
    with _themerrdb_cache_lock:
        cached = _themerrdb_cache.get(cache_key)
        if cached and time.time() - cached['timestamp'] < THEMERRDB_CACHE_TTL:
            return True, cached['data']
    return False, None


def _set_cached_themerrdb(external_id, data, item_type=None):
    """Cache ThemerrDB response."""
    cache_key = _get_themerrdb_cache_key(external_id, item_type)
    with _themerrdb_cache_lock:
        _themerrdb_cache[cache_key] = {
            'data': data,
            'timestamp': time.time(),
        }


def query_themerrdb(item_type, database, external_id):
    """Query ThemerrDB API for theme availability.
    
    Args:
        item_type: 'movies' or 'tv_shows'
        database: 'imdb' or 'themoviedb'
        external_id: the IMDB/TVDB ID
    
    Returns:
        Theme metadata dict (with 'youtube_theme_url' key) or None if not found.
    """
    if not external_id:
        return None

    # Validate the external ID before interpolating it into a URL to prevent
    # path traversal or header injection via a compromised upstream metadata source.
    if not _EXTERNAL_ID_RE.match(str(external_id)):
        logger.warning('Rejecting malformed ThemerrDB external_id (value redacted)')
        return None

    # Check cache first
    cache_hit, cached = _get_cached_themerrdb(external_id, item_type)
    if cache_hit:
        return cached
    
    try:
        url = f'{THEMERRDB_API_BASE}/{item_type}/{database}/{external_id}.json'
        logger.debug('Querying ThemerrDB for theme availability')
        response = http_requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            _set_cached_themerrdb(external_id, data, item_type)
            return data
        elif response.status_code == 404:
            logger.debug('Theme not found in ThemerrDB')
            _set_cached_themerrdb(external_id, None, item_type)
            return None
        else:
            logger.warning('ThemerrDB query failed with status %s', response.status_code)
            return None
    except Exception as exc:
        logger.error('Error querying ThemerrDB: %s', type(exc).__name__)
        return None


def get_themerrdb_theme(item):
    """Get ThemerrDB theme data for a Plex item if available.
    
    Returns theme metadata dict or None if not available.
    """
    return get_themerrdb_theme_for_item('plex', item)


def get_themerrdb_data_for_context(context):
    """Resolve ThemerrDB metadata for a provider item context."""
    return get_themerrdb_theme_for_item(context['provider'], context['item'])
