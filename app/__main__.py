#!/usr/bin/env python3
"""Module entry point for `python -m app`."""

import os
from app.web_app import app

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8080'))
    debug = os.getenv('FLASK_DEBUG', 'false').lower() == 'true'
    app.run(host='0.0.0.0', port=port, debug=debug)
