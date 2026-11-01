"""Agent-facing discovery and download API, sharing the web UI's media pipeline."""

import re

from flask import Blueprint, jsonify, request

from app.errors import error_response
from app.theme_state import (
    check_themerrdb_availability_for_context, get_external_ids_for_context,
    has_nonempty_theme_file, is_plex_theme_source_unverified,
)
from app.youtube_utils import is_valid_youtube_url, normalize_youtube_trim_window

API_PREFIX = '/api/agent/v1'
ITEM_FIELDS = (
    'id', 'provider', 'library_id', 'title', 'year', 'type', 'external_ids',
    'has_local_theme', 'has_plex_theme', 'has_themerrdb_theme',
    'plex_theme_source_unverified', 'theme_size',
)


def validate_identifier(provider, identifier):
    """Reject ambiguous identifiers and URL path fragments before provider access."""
    if provider not in ('plex', 'jellyfin'):
        raise ValueError('Provider must be plex or jellyfin')
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', identifier):
        raise ValueError('Invalid library or item identifier')
    if provider == 'plex' and not identifier.isdigit():
        raise ValueError('Plex identifier must be an integer')


def _page_number(name, default, maximum=None):
    value = request.args.get(name, str(default))
    if not re.fullmatch(r'[0-9]{1,9}', value):
        raise ValueError(f'{name} must be a positive integer')
    number = int(value)
    if number < 1 or (maximum is not None and number > maximum):
        raise ValueError(f'{name} must be between 1 and {maximum}' if maximum else f'{name} must be positive')
    return number


def create_agent_api(*, load_items, get_context, download_handlers):
    """Create routes; the application's global API auth middleware protects them."""
    blueprint = Blueprint('agent_api', __name__, url_prefix=API_PREFIX)

    @blueprint.get('/capabilities')
    def capabilities():
        return jsonify({
            'version': '1',
            'providers': ['plex', 'jellyfin'],
            'sources': ['plex', 'themerrdb', 'youtube'],
            'theme_status_definition': 'present/missing refers to a non-empty local theme.mp3, not a remote source',
            'libraries_url': '/api/libraries',
            'items_url': f'{API_PREFIX}/libraries/{{provider}}/{{library_id}}/items',
            'options_url': f'{API_PREFIX}/items/{{provider}}/{{item_id}}/options',
            'download_url': f'{API_PREFIX}/items/{{provider}}/{{item_id}}/download',
            'youtube_search_url': '/api/youtube/search',
            'cache_status_url': '/api/cache/status',
            'max_page_size': 100,
            'downloads': {'synchronous': True, 'overwrite_default': False},
        })

    @blueprint.get('/libraries/<provider>/<library_id>/items')
    def list_items(provider, library_id):
        try:
            validate_identifier(provider, library_id)
            page = _page_number('page', 1)
            page_size = _page_number('page_size', 50, 100)
            theme_status = request.args.get('theme_status', 'all')
            if theme_status not in ('all', 'present', 'missing'):
                raise ValueError('theme_status must be all, present or missing')
            media_type = request.args.get('type', 'all')
            if media_type not in ('all', 'show', 'movie'):
                raise ValueError('type must be all, show or movie')
            query = request.args.get('q', '').strip()
            if len(query) > 200:
                raise ValueError('Search query is too long')
        except ValueError as exc:
            return error_response(str(exc), status_code=400)

        try:
            snapshot = load_items(provider, library_id)
            filtered = [
                item for item in snapshot
                if (not query or query.casefold() in item['title'].casefold())
                and (media_type == 'all' or item['type'] == media_type)
                and (theme_status == 'all' or bool(item['has_local_theme']) == (theme_status == 'present'))
            ]
            start = (page - 1) * page_size
            return jsonify({
                'items': [{key: item.get(key) for key in ITEM_FIELDS} for item in filtered[start:start + page_size]],
                'pagination': {
                    'page': page, 'page_size': page_size, 'total': len(filtered),
                    'has_more': start + page_size < len(filtered),
                },
                'summary': {
                    'library_total': len(snapshot),
                    'local_themes': sum(bool(item['has_local_theme']) for item in snapshot),
                    'missing_local_themes': sum(not item['has_local_theme'] for item in snapshot),
                },
                'theme_state': 'cached; use item options for a fresh local file check',
            })
        except Exception as exc:
            return error_response('Failed to discover library items', exc=exc)

    @blueprint.get('/items/<provider>/<item_id>/options')
    def theme_options(provider, item_id):
        try:
            validate_identifier(provider, item_id)
        except ValueError as exc:
            return error_response(str(exc), status_code=400)
        try:
            context = get_context(provider, item_id)
            local_exists = has_nonempty_theme_file(context['local_path'])
            themerrdb = check_themerrdb_availability_for_context(context)
            if themerrdb.get('available') and not is_valid_youtube_url(themerrdb.get('youtube_url')):
                return error_response('ThemerrDB returned an invalid theme URL', status_code=502)
            return jsonify({
                'id': context['item_id'], 'provider': provider, 'title': context['title'],
                'external_ids': get_external_ids_for_context(context),
                'has_local_theme': local_exists,
                'local_path_available': context['local_path'] is not None,
                'sources': {
                    'plex': {
                        'available': context['has_plex_theme'],
                        'source_unverified': (
                            is_plex_theme_source_unverified(context['item'], local_exists)
                            if provider == 'plex' else False
                        ),
                        'reason': 'Plex only; a reported theme may be the existing local file. Downloadability is checked on download.',
                    },
                    'themerrdb': {**themerrdb, 'playability_verified': False},
                    'youtube': {
                        'available': True, 'requires_url': True,
                        'suggested_query': f"{context['title']} theme song",
                        'supports_trim': True,
                    },
                },
                'download_url': f'{API_PREFIX}/items/{provider}/{item_id}/download',
                'overwrite_required': local_exists,
            })
        except ValueError as exc:
            return error_response('Invalid provider or item identifier', status_code=400, exc=exc)
        except Exception as exc:
            return error_response('Failed to discover theme options', exc=exc)

    @blueprint.post('/items/<provider>/<item_id>/download')
    def download_theme(provider, item_id):
        try:
            validate_identifier(provider, item_id)
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                raise ValueError('A JSON object is required')
            source = data.get('source')
            if not isinstance(source, str) or source not in download_handlers:
                raise ValueError('source must be plex, themerrdb or youtube')
            if not isinstance(data.get('overwrite', False), bool):
                raise ValueError('overwrite must be a boolean')
            if source == 'plex' and provider != 'plex':
                raise ValueError('The Plex source is only supported for Plex items')
            if source == 'youtube':
                if not isinstance(data.get('url'), str) or not is_valid_youtube_url(data['url']):
                    raise ValueError('A valid YouTube URL is required')
                normalize_youtube_trim_window(data.get('start_time'), data.get('end_time'))
            elif any(key in data for key in ('url', 'start_time', 'end_time')):
                raise ValueError('url and trim times are only supported for the YouTube source')
        except (ValueError, TypeError) as exc:
            return error_response(str(exc), status_code=400)
        return download_handlers[source](provider, item_id)

    return blueprint
