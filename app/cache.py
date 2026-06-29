"""Library and poster caching, cache hydration."""

import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app.plex_utils import get_plex, plex_session_get, get_section_base_paths
from app.media_utils import scan_local_theme_dirs, _is_video_file_path

logger = logging.getLogger(__name__)

POSTER_CACHE_MAX_ITEMS_DEFAULT = 500
BACKGROUND_WORKER_COUNT_DEFAULT = 4

_library_cache = {}
_library_cache_lock = threading.Lock()
_section_build_locks = {}
_section_build_locks_lock = threading.Lock()
_poster_cache = OrderedDict()
_poster_cache_lock = threading.Lock()
_theme_hydration_status = {
    'running': False,
    'ready': True,
    'sections_total': 0,
    'sections_completed': 0,
}
_theme_hydration_status_lock = threading.Lock()
_jellyfin_user_id_cache = {'value': None}
_jellyfin_user_id_lock = threading.Lock()
_background_executor = None
_background_job_lock = threading.Lock()
_cache_warmup_future = None
_poster_warmup_future = None
_startup_warmup_started = False
_startup_warmup_lock = threading.Lock()
_background_worker_count = BACKGROUND_WORKER_COUNT_DEFAULT
_poster_cache_max_items = POSTER_CACHE_MAX_ITEMS_DEFAULT


def init_cache(worker_count=BACKGROUND_WORKER_COUNT_DEFAULT, max_poster_items=POSTER_CACHE_MAX_ITEMS_DEFAULT):
    """Initialize cache pools and settings."""
    global _background_executor, _background_worker_count, _poster_cache_max_items
    _background_worker_count = worker_count
    _poster_cache_max_items = max_poster_items
    _background_executor = ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix='themarr-bg',
    )


def invalidate_library_cache():
    """Drop all cached libraries/posters so the next fetch re-queries Plex."""
    with _library_cache_lock:
        _library_cache.clear()
    with _poster_cache_lock:
        _poster_cache.clear()
    with _jellyfin_user_id_lock:
        _jellyfin_user_id_cache['value'] = None
    with _theme_hydration_status_lock:
        _theme_hydration_status.update({
            'running': True,
            'ready': False,
            'sections_total': 0,
            'sections_completed': 0,
        })


def get_section_build_lock(section_id):
    """Return a per-section lock to avoid duplicate cache builds under load."""
    section_id = str(section_id)
    with _section_build_locks_lock:
        section_lock = _section_build_locks.get(section_id)
        if section_lock is None:
            section_lock = threading.Lock()
            _section_build_locks[section_id] = section_lock
    return section_lock


def set_theme_hydration_total(sections_total):
    with _theme_hydration_status_lock:
        _theme_hydration_status.update({
            'running': True,
            'ready': False,
            'sections_total': sections_total,
            'sections_completed': 0,
        })


def advance_theme_hydration_progress():
    with _theme_hydration_status_lock:
        completed = min(
            _theme_hydration_status.get('sections_total', 0),
            _theme_hydration_status.get('sections_completed', 0) + 1,
        )
        _theme_hydration_status['sections_completed'] = completed
        total = _theme_hydration_status.get('sections_total', 0)
        if total > 0 and completed >= total:
            _theme_hydration_status['running'] = False
            _theme_hydration_status['ready'] = True


def mark_theme_hydration_finished():
    with _theme_hydration_status_lock:
        _theme_hydration_status['running'] = False
        _theme_hydration_status['ready'] = True
        _theme_hydration_status['sections_completed'] = _theme_hydration_status.get('sections_total', 0)


def get_theme_hydration_status():
    with _theme_hydration_status_lock:
        return dict(_theme_hydration_status)


def get_cached_item(rating_key, provider=None):
    """Return a cached item dict by ratingKey and optional provider, or None."""
    target = str(rating_key)
    provider = (provider or '').strip().lower() or None
    with _library_cache_lock:
        for section_items in _library_cache.values():
            for cached_item in section_items:
                if str(cached_item.get('ratingKey')) != target:
                    continue
                if provider and (cached_item.get('provider') or 'plex') != provider:
                    continue
                return cached_item
    return None


def get_cached_poster(rating_key, provider='plex'):
    """Return cached poster payload dict for *(provider, rating_key)*, or None."""
    cache_key = f'{provider}:{rating_key}'
    with _poster_cache_lock:
        cached = _poster_cache.get(cache_key)
        if cached is None:
            return None
        _poster_cache.move_to_end(cache_key)
        return cached


def set_cached_poster(rating_key, content, content_type, provider='plex'):
    """Store poster bytes in the in-memory poster cache."""
    cache_key = f'{provider}:{rating_key}'
    with _poster_cache_lock:
        _poster_cache[cache_key] = {
            'content': content,
            'content_type': content_type,
        }
        _poster_cache.move_to_end(cache_key)
        while len(_poster_cache) > _poster_cache_max_items:
            _poster_cache.popitem(last=False)


def fetch_poster_bytes(plex, thumb, timeout=10):
    """Fetch poster bytes for a Plex thumb path."""
    url = plex.url(thumb, includeToken=True)
    response = plex_session_get(plex, url, stream=True, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get('content-type', 'image/jpeg')
    return response.content, content_type


def submit_background_job(name, fn, *args):
    """Submit work to shared background executor with graceful rejection logging."""
    global _background_executor
    if _background_executor is None:
        init_cache()
    try:
        return _background_executor.submit(fn, *args)
    except RuntimeError as exc:
        logger.warning('Failed to queue background job %s: %s', name, exc)
        return None


def get_jellyfin_user_id_cached():
    """Return cached Jellyfin user ID or None."""
    with _jellyfin_user_id_lock:
        return _jellyfin_user_id_cache.get('value')


def set_jellyfin_user_id_cached(user_id):
    """Cache Jellyfin user ID."""
    with _jellyfin_user_id_lock:
        _jellyfin_user_id_cache['value'] = user_id


def sync_cached_item(item_to_dict_fn, item):
    """Update an item's cached entry in-place after local theme state changes."""
    updated = False
    updated_item = None
    with _library_cache_lock:
        for section_items in _library_cache.values():
            for index, cached_item in enumerate(section_items):
                if str(cached_item.get('ratingKey')) == str(item.ratingKey):
                    existing_library_id = cached_item.get('library_id')
                    existing_provider = cached_item.get('provider') or 'plex'
                    updated_item = item_to_dict_fn(
                        item,
                        provider=existing_provider,
                        library_id=existing_library_id,
                    )
                    section_items[index] = updated_item
                    updated = True
                    break
            if updated:
                break
    if updated_item is None:
        updated_item = item_to_dict_fn(item)
    return updated_item, updated


def sync_cached_item_theme_state(sync_fn, provider, item_id):
    """Refresh has_local_theme/theme_size for a cached item by provider/id."""
    target_id = str(item_id)
    with _library_cache_lock:
        for section_key, section_items in _library_cache.items():
            for idx, cached_item in enumerate(section_items):
                cached_provider = cached_item.get('provider') or 'plex'
                if cached_provider != provider or str(cached_item.get('id') or cached_item.get('ratingKey')) != target_id:
                    continue
                updated = sync_fn(cached_item)
                if updated:
                    section_items[idx] = updated
                    _library_cache[section_key] = section_items
                    return updated, True
    return None, False


def get_library_cache_for_section(section_id):
    """Retrieve cached library items for a section, or None if not cached."""
    with _library_cache_lock:
        return _library_cache.get(section_id)


def set_library_cache_for_section(section_id, items):
    """Cache library items for a section."""
    with _library_cache_lock:
        _library_cache[section_id] = items


def background_warm_poster_cache(plex, item_to_dict_fn):
    """Background thread: pre-load poster images for already-cached library items."""
    logger.info('Poster cache warmup starting…')
    
    with _library_cache_lock:
        all_items = [
            item
            for section_items in _library_cache.values()
            for item in section_items
            if item.get('thumb') and item.get('ratingKey') is not None
        ]

    unique_items = {}
    for item in all_items:
        unique_items[int(item['ratingKey'])] = item
    items_to_warm = list(unique_items.values())

    if not items_to_warm:
        logger.info('Poster cache warmup skipped: no cached items found.')
        return

    warmed = 0

    def warm_item(item):
        rating_key = int(item['ratingKey'])
        if get_cached_poster(rating_key) is not None:
            return True
        try:
            content, content_type = fetch_poster_bytes(plex, item['thumb'], timeout=10)
            set_cached_poster(rating_key, content, content_type)
            return True
        except Exception as exc:
            logger.debug('Poster cache warmup failed for ratingKey %s: %s', rating_key, exc)
            return False

    max_workers = min(6, max(1, len(items_to_warm)))
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='poster-cache-warm') as executor:
        futures = [executor.submit(warm_item, item) for item in items_to_warm]
        for future in as_completed(futures):
            if future.result():
                warmed += 1

    logger.info('Poster cache warmup complete: %d/%d posters cached', warmed, len(items_to_warm))
