"""Error response helpers."""

import logging
from flask import jsonify

logger = logging.getLogger(__name__)


def error_response(message, status_code=500, exc=None):
    """Return a safe JSON error response while logging internal details."""
    if exc is None:
        logger.error(message)
    else:
        logger.error('%s: %s', message, exc)
    return jsonify({'error': message}), status_code
