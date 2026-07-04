"""Bulk operations on multiple items (Plex and Jellyfin)."""

import logging
import shutil
import tempfile
from pathlib import Path

from flask import request, jsonify

from app.plex_utils import get_plex, plex_session_get, get_validated_plex_local_path, refresh_plex_item_metadata
from app.jellyfin_utils import (
    get_jellyfin_item, get_jellyfin_item_local_path,
    refresh_jellyfin_item_metadata, jellyfin_session_get,
)
from app.media_utils import _theme_file_path, _validate_local_media_path
from app.notifications import send_pushover_notification, TRIGGER_UI
from app.theme_state import has_nonempty_theme_file
from app.cache import sync_cached_item_theme_state, fetch_poster_bytes
from app.theme_audio import (
    apply_theme_id3_tags, build_theme_metadata, normalize_theme_audio,
    has_theme_metadata_tags, is_theme_audio_normalized,
)
from app.themerrdb_service import get_themerrdb_theme_for_item
from app.youtube_utils import is_valid_youtube_url, download_youtube_theme_mp3

logger = logging.getLogger(__name__)

MAX_BULK_ITEMS = 100
_YTDLP_WORKDIR = Path(tempfile.gettempdir()) / 'themarr_yt_dlp_work'
_YTDLP_WORKDIR.mkdir(parents=True, exist_ok=True)


def _parse_bulk_items(data):
    """Parse and validate the items list from a bulk request payload.

    Returns (items, error_tuple) where exactly one of them is None.
    Each validated item is a dict with 'provider' and 'itemId' keys.
    """
    if not data or not isinstance(data.get('items'), list):
        return None, (jsonify({'error': 'items (list) is required'}), 400)
    items = data['items']
    if not items:
        return None, (jsonify({'error': 'items list is empty'}), 400)
    if len(items) > MAX_BULK_ITEMS:
        return None, (jsonify({'error': f'Maximum {MAX_BULK_ITEMS} items per bulk operation'}), 400)
    validated = []
    for entry in items:
        if not isinstance(entry, dict):
            return None, (jsonify({'error': 'Each item must be an object with provider and itemId'}), 400)
        provider = (entry.get('provider') or 'plex').strip().lower()
        if provider not in ('plex', 'jellyfin'):
            return None, (jsonify({'error': 'Provider must be "plex" or "jellyfin"'}), 400)
        item_id = str(entry.get('itemId') or '')
        if not item_id:
            return None, (jsonify({'error': 'Each item must have a non-empty itemId'}), 400)
        if '/' in item_id or '\\' in item_id:
            return None, (jsonify({'error': 'itemId must not contain path separator characters'}), 400)
        validated.append({'provider': provider, 'itemId': item_id})
    return validated, None


def _get_jellyfin_artwork(jellyfin, item_id):
    """Fetch poster image bytes for a Jellyfin item. Returns (bytes, mime) or (None, None)."""
    try:
        response = jellyfin_session_get(jellyfin, f'/Items/{item_id}/Images/Primary')
        if response.status_code == 200:
            content_type = response.headers.get('content-type', 'image/jpeg')
            return response.content, content_type
    except Exception as exc:
        logger.info('Bulk: Jellyfin artwork fetch failed for item %s: %s', item_id, exc)
    return None, None


def bulk_download_themes():
    """Download themes for multiple items in one request (Plex or Jellyfin).

    Plex items: download from the Plex built-in theme URL.
    Jellyfin items: download from ThemerrDB.
    """
    data = request.get_json(silent=True)
    items, err = _parse_bulk_items(data)
    if err:
        return err

    overwrite = data.get('overwrite', False)
    results = {'success': [], 'skipped': [], 'no_theme': [], 'failed': []}

    for entry in items:
        provider = entry['provider']
        item_id = entry['itemId']
        try:
            if provider == 'plex':
                _bulk_download_plex(item_id, overwrite, results)
            else:
                _bulk_download_jellyfin(item_id, overwrite, results)
        except Exception as exc:
            results['failed'].append({'itemId': item_id, 'provider': provider, 'error': str(exc)})

    if results['success']:
        titles = ', '.join(r['title'] for r in results['success'][:5])
        extra = len(results['success']) - 5
        msg = f"{titles}{f' and {extra} more' if extra > 0 else ''}"
        send_pushover_notification(
            title=f"Themes Downloaded ({len(results['success'])})",
            message=msg,
            trigger=TRIGGER_UI,
        )

    return jsonify(results)


def _bulk_download_plex(rating_key, overwrite, results):
    """Download Plex built-in theme for one item; appends to results in-place."""
    plex = get_plex()
    item = plex.fetchItem(int(rating_key))

    if not getattr(item, 'theme', None):
        results['no_theme'].append({'itemId': rating_key, 'title': item.title})
        return

    local_path = get_validated_plex_local_path(item)
    if not local_path:
        results['failed'].append({
            'itemId': rating_key,
            'title': getattr(item, 'title', '?'),
            'error': 'Cannot determine local path',
        })
        return

    theme_path = _theme_file_path(local_path)
    if has_nonempty_theme_file(local_path) and not overwrite:
        results['skipped'].append({'itemId': rating_key, 'title': item.title})
        return

    url = plex.url(item.theme, includeToken=True)
    response = plex_session_get(plex, url, stream=True, timeout=30)
    response.raise_for_status()

    local_path.mkdir(parents=True, exist_ok=True)
    with open(theme_path, 'wb') as fh:
        for chunk in response.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)
    normalize_theme_audio(theme_path)
    metadata = build_theme_metadata('plex', item, item.title)
    artwork_bytes, artwork_mime = None, None
    try:
        if getattr(item, 'thumb', None):
            artwork_bytes, artwork_mime = fetch_poster_bytes(plex, item.thumb, timeout=10)
    except Exception as exc:
        logger.info('Bulk: Plex artwork fetch failed for %s: %s', item.title, exc)
    apply_theme_id3_tags(theme_path, metadata, artwork_bytes=artwork_bytes, artwork_mime=artwork_mime)

    results['success'].append({'itemId': rating_key, 'title': item.title})
    sync_cached_item_theme_state('plex', str(item.ratingKey))
    refresh_plex_item_metadata(item)
    logger.info('Bulk: downloaded Plex theme for %s', item.title)


def _bulk_download_jellyfin(item_id, overwrite, results):
    """Download ThemerrDB theme for one Jellyfin item; appends to results in-place."""
    jellyfin, _, item = get_jellyfin_item(item_id)
    title = item.get('Name') or 'Unknown'

    raw_path = get_jellyfin_item_local_path(item)
    local_path = _validate_local_media_path(raw_path) if raw_path else None
    if not local_path:
        results['failed'].append({'itemId': item_id, 'title': title, 'error': 'Cannot determine local path'})
        return

    theme_path = _theme_file_path(local_path)
    if has_nonempty_theme_file(local_path) and not overwrite:
        results['skipped'].append({'itemId': item_id, 'title': title})
        return

    themerrdb_data = get_themerrdb_theme_for_item('jellyfin', item)
    if not themerrdb_data or not themerrdb_data.get('youtube_theme_url'):
        results['no_theme'].append({'itemId': item_id, 'title': title})
        return

    youtube_url = themerrdb_data['youtube_theme_url']
    if not is_valid_youtube_url(youtube_url):
        logger.warning('Bulk: ThemerrDB returned an invalid YouTube URL for Jellyfin item %s', item_id)
        results['failed'].append({
            'itemId': item_id,
            'title': title,
            'error': 'ThemerrDB returned an invalid theme URL',
        })
        return

    with tempfile.TemporaryDirectory(dir=_YTDLP_WORKDIR) as tmpdir:
        mp3_path = download_youtube_theme_mp3(youtube_url, tmpdir)
        local_path.mkdir(parents=True, exist_ok=True)
        shutil.move(str(mp3_path), str(theme_path))

    normalize_theme_audio(theme_path)
    metadata = build_theme_metadata('jellyfin', item, title)
    artwork_bytes, artwork_mime = _get_jellyfin_artwork(jellyfin, item_id)
    apply_theme_id3_tags(theme_path, metadata, artwork_bytes=artwork_bytes, artwork_mime=artwork_mime)

    results['success'].append({'itemId': item_id, 'title': title})
    sync_cached_item_theme_state('jellyfin', item_id)
    refresh_jellyfin_item_metadata(item_id)
    logger.info('Bulk: downloaded ThemerrDB theme for Jellyfin item %s', item_id)


def bulk_postprocess_themes():
    """Normalize and tag existing local themes for multiple items (Plex or Jellyfin)."""
    data = request.get_json(silent=True)
    items, err = _parse_bulk_items(data)
    if err:
        return err

    results = {'processed': [], 'skipped': [], 'no_theme': [], 'failed': []}

    for entry in items:
        provider = entry['provider']
        item_id = entry['itemId']
        try:
            if provider == 'plex':
                _bulk_postprocess_plex(item_id, results)
            else:
                _bulk_postprocess_jellyfin(item_id, results)
        except Exception as exc:
            logger.warning('Bulk post-process: unexpected error for item %s (%s): %s', item_id, provider, exc)
            results['failed'].append({'error': 'Unexpected error processing item'})

    return jsonify(results)


def _bulk_postprocess_plex(rating_key, results):
    """Normalize and tag existing theme for one Plex item; appends to results in-place."""
    plex = get_plex()
    item = plex.fetchItem(int(rating_key))
    item_key = str(item.ratingKey)  # use server-returned key, not user input
    local_path = get_validated_plex_local_path(item)
    if not local_path:
        results['failed'].append({
            'itemId': item_key,
            'title': getattr(item, 'title', '?'),
            'error': 'Cannot determine local path',
        })
        return

    theme_path = _theme_file_path(local_path)
    if not has_nonempty_theme_file(local_path):
        results['no_theme'].append({'itemId': item_key, 'title': item.title})
        return

    already_normalized = is_theme_audio_normalized(theme_path)
    already_tagged = has_theme_metadata_tags(theme_path)
    normalized = False
    tagged = False

    if not already_normalized:
        normalized = normalize_theme_audio(theme_path)

    if not already_tagged:
        metadata = build_theme_metadata('plex', item, item.title)
        artwork_bytes, artwork_mime = None, None
        try:
            if getattr(item, 'thumb', None):
                artwork_bytes, artwork_mime = fetch_poster_bytes(plex, item.thumb, timeout=10)
        except Exception as exc:
            logger.info('Bulk post-process: Plex artwork fetch failed for %s: %s', item.title, exc)
        tagged = apply_theme_id3_tags(
            theme_path,
            metadata,
            artwork_bytes=artwork_bytes,
            artwork_mime=artwork_mime,
            preserve_existing=True,
        )

    if normalized or tagged:
        results['processed'].append({
            'itemId': item_key,
            'title': item.title,
            'normalized': normalized,
            'tagged': tagged,
        })
        sync_cached_item_theme_state('plex', item_key)
        refresh_plex_item_metadata(item)
        logger.info('Bulk post-process: updated Plex theme for %s', item.title)
    else:
        reasons = []
        if already_normalized:
            reasons.append('already_normalized')
        if already_tagged:
            reasons.append('already_tagged')
        results['skipped'].append({
            'itemId': item_key,
            'title': item.title,
            'reason': ','.join(reasons) or 'no_changes_needed',
        })


def _bulk_postprocess_jellyfin(item_id, results):
    """Normalize and tag existing theme for one Jellyfin item; appends to results in-place."""
    jellyfin, _, item = get_jellyfin_item(item_id)
    item_key = item.get('Id')
    if not item_key:
        logger.warning('Bulk post-process: Jellyfin item %s returned without an Id', item_id)
        raise ValueError('Jellyfin item returned without an Id')
    title = item.get('Name') or 'Unknown'

    raw_path = get_jellyfin_item_local_path(item)
    local_path = _validate_local_media_path(raw_path) if raw_path else None
    if not local_path:
        results['failed'].append({'itemId': item_key, 'title': title, 'error': 'Cannot determine local path'})
        return

    theme_path = _theme_file_path(local_path)
    if not has_nonempty_theme_file(local_path):
        results['no_theme'].append({'itemId': item_key, 'title': title})
        return

    already_normalized = is_theme_audio_normalized(theme_path)
    already_tagged = has_theme_metadata_tags(theme_path)
    normalized = False
    tagged = False

    if not already_normalized:
        normalized = normalize_theme_audio(theme_path)

    if not already_tagged:
        metadata = build_theme_metadata('jellyfin', item, title)
        artwork_bytes, artwork_mime = _get_jellyfin_artwork(jellyfin, item_key)
        tagged = apply_theme_id3_tags(
            theme_path,
            metadata,
            artwork_bytes=artwork_bytes,
            artwork_mime=artwork_mime,
            preserve_existing=True,
        )

    if normalized or tagged:
        results['processed'].append({
            'itemId': item_key,
            'title': title,
            'normalized': normalized,
            'tagged': tagged,
        })
        sync_cached_item_theme_state('jellyfin', item_key)
        refresh_jellyfin_item_metadata(item_key)
        logger.info('Bulk post-process: updated Jellyfin theme for item %s', item_id)
    else:
        reasons = []
        if already_normalized:
            reasons.append('already_normalized')
        if already_tagged:
            reasons.append('already_tagged')
        results['skipped'].append({
            'itemId': item_key,
            'title': title,
            'reason': ','.join(reasons) or 'no_changes_needed',
        })
