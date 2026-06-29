"""Plex webhook event processing."""

import hmac
import logging

from flask import jsonify

from app.plex_utils import get_plex, get_validated_plex_local_path
from app.media_utils import _theme_file_path
from app.notifications import send_pushover_notification
from app.theme_state import has_nonempty_theme_file
from app.cache import sync_cached_item

logger = logging.getLogger(__name__)


def check_webhook_server_uuid(payload):
    """Validate webhook server UUID against the configured Plex server."""
    server_info = payload.get('Server') or payload.get('server') or {}
    if not isinstance(server_info, dict):
        return jsonify({'error': 'Invalid webhook payload server metadata'}), 400
    webhook_server_uuid = str(server_info.get('uuid') or '').strip()
    if not webhook_server_uuid:
        return jsonify({'error': 'Missing webhook server UUID'}), 400

    try:
        plex = get_plex()
    except Exception as exc:
        logger.warning('Plex webhook: failed to load configured Plex server for UUID check: %s', exc)
        return jsonify({'error': 'Unable to validate webhook source server'}), 503

    configured_uuid = str(getattr(plex, 'machineIdentifier', '') or '').strip()
    if not configured_uuid:
        logger.warning('Plex webhook: configured Plex server did not expose machineIdentifier')
        return jsonify({'error': 'Unable to validate webhook source server'}), 503

    if not hmac.compare_digest(webhook_server_uuid, configured_uuid):
        logger.warning(
            'Plex webhook rejected: server UUID mismatch (received=%s configured=%s)',
            webhook_server_uuid,
            configured_uuid,
        )
        return jsonify({'error': 'Webhook server UUID mismatch'}), 403
    return None


def process_plex_library_new(rating_key, download_plex_theme_fn):
    """Process a Plex library.new webhook event by downloading theme if needed.
    
    Retrieves the item from Plex by rating key, checks if theme.mp3 already exists,
    and downloads the Plex theme if available.
    """
    try:
        plex = get_plex()
        item = plex.library.fetchItem(int(rating_key))
        
        logger.info("Plex webhook: processing new item '%s' (ratingKey=%s)", item.title, rating_key)
        
        if not getattr(item, 'theme', None):
            logger.info("Plex webhook: '%s' has no theme in Plex — nothing to download", item.title)
            return
        
        local_path = get_validated_plex_local_path(item)
        if not local_path:
            logger.warning("Plex webhook: cannot determine local path for '%s'", item.title)
            return
        
        theme_path = _theme_file_path(local_path)
        if has_nonempty_theme_file(local_path):
            logger.info("Plex webhook: '%s' already has a theme file", item.title)
            return
        
        download_plex_theme_fn(plex, item, theme_path)
        sync_cached_item(item)
        send_pushover_notification(
            title='Theme Downloaded',
            message=f'{item.title} theme auto-downloaded via Plex webhook',
        )
    except Exception as exc:
        logger.error("Plex webhook: failed to process item %s: %s", rating_key, exc)
        send_pushover_notification(
            title='Theme Download Failed',
            message=f'Failed to process Plex webhook for item {rating_key}',
        )
