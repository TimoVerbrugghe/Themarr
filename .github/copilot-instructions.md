# Copilot Instructions for Themarr

## What this repo does

Themarr manages theme music (`theme.mp3`) for Plex and Jellyfin libraries.
It includes a Flask-based **web UI** (`web_app.py`) with download sources from
Plex, ThemerrDB, and YouTube, plus Sonarr/Radarr webhook support and optional
Pushover notifications.

## Project structure

The application is split across the following layers:

### App modules (`app/`)

| File | Purpose |
|---|---|
| `app/web_app.py` | Flask app entry point — routes, request handling |
| `app/auth.py` | Authentication helpers: API key, session, DISABLE_AUTH |
| `app/bulk_operations.py` | Bulk theme download logic |
| `app/cache.py` | Library cache, poster cache, theme hydration status |
| `app/errors.py` | Shared error response helpers |
| `app/external_ids.py` | IMDB/TMDB/TVDB ID extraction for Plex and Jellyfin items |
| `app/jellyfin_utils.py` | Jellyfin connection, library helpers, and item serialization |
| `app/media_utils.py` | Filesystem path validation, upload constraints, theme scanning |
| `app/notifications.py` | Pushover notification helpers |
| `app/plex_utils.py` | Plex server connection and library path helpers |
| `app/theme_state.py` | Per-item theme state queries (local, Plex, ThemerrDB) |
| `app/themerrdb_service.py` | ThemerrDB HTTP queries and cache |
| `app/webhook_handlers.py` | Plex webhook processing |
| `app/youtube_utils.py` | YouTube URL validation and yt-dlp option builders |

### Frontend

| File/Directory | Purpose |
|---|---|
| `templates/index.html` | Single-page app shell |
| `static/css/style.css` | Sonarr-inspired dark/light theme CSS |
| `static/js/app.js` | Frontend logic (library browser, modals, multi-select, settings) |

### Tests (`tests/`)

Tests mirror the app module structure. Each test file covers the corresponding app module.

| File | Tests for |
|---|---|
| `tests/conftest.py` | Shared pytest fixtures: `app`, `client`, `mock_plex` |
| `tests/helpers.py` | Shared test helpers: `make_mock_show`, `make_mock_movie` |
| `tests/test_web_app.py` | Core routes: status, libraries, items, theme operations, settings |
| `tests/test_auth.py` | `app/auth.py` — login, logout, sessions, API key, DISABLE_AUTH |
| `tests/test_cache.py` | `app/cache.py`, `app/theme_state.py` — cache status, theme sync, refresh |
| `tests/test_external_ids.py` | `app/external_ids.py` — external ID extraction |
| `tests/test_media_utils.py` | `app/media_utils.py` — path validation, MP3 magic bytes |
| `tests/test_notifications.py` | `app/notifications.py` — Pushover notifications |
| `tests/test_themerrdb.py` | `app/themerrdb_service.py` — ThemerrDB queries and cache |
| `tests/test_bulk_operations.py` | `app/bulk_operations.py` — bulk theme download |
| `tests/test_webhooks.py` | `app/webhook_handlers.py` — Plex webhook processing |
| `tests/test_youtube.py` | `app/youtube_utils.py` — YouTube search, download, option hygiene |

When adding tests for a new feature, place them in the file that matches the app module being tested. Add shared fixtures to `tests/conftest.py` and factory helpers to `tests/helpers.py`.

## Local validation before finishing a task

Always run **all** of these before pushing changes:

```bash
# Syntax check
python3 -m py_compile app/*.py

# Unit tests (must all pass)
python3 -m pytest tests/ -v

# Validate Docker Compose config
docker compose config

# Build container image
docker build -t themarr:test .
```

## Configuration

Primary environment variables are defined in `.env.example`:

- `PLEX_URL`, `PLEX_TOKEN` — Plex server credentials
- `JELLYFIN_URL`, `JELLYFIN_API_KEY`, `JELLYFIN_USER_ID` — Jellyfin server credentials/user context
- `FLASK_DEBUG` — Flask debug mode (never enable in production)
- `DEFAULT_THEME` — default UI theme: `dark` or `light`
- `DEFAULT_VIEW` — default library view: `list` or `grid`
- `API_KEY` — API key protecting mutating API endpoints; auto-generated and logged at startup when not set
- `PUSHOVER_APP_TOKEN`, `PUSHOVER_USER_KEY` — optional Pushover notifications
- `WEBHOOK_USERNAME`, `WEBHOOK_PASSWORD` — optional webhook Basic Auth (both must be set)
- `PLEX_RETRY_ATTEMPTS`, `PLEX_RETRY_DELAY` — webhook retry tuning

When changing configuration, always update:

- `.env.example`
- `docker-compose.yml`
- `Dockerfile`
- `README.md`

When adding a new environment variable that appears in the Settings "Environment Variables" table:

- Include it in the backend `env_values` payload.
- Ensure the Settings "Current" column reports the **effective runtime value** when unset (fallback to the documented default), not `—`.

## Web UI screenshots — skill managed

Screenshots are refreshed on demand by running the repository skill:

```bash
pip install playwright Pillow
python3 -m playwright install chromium
```

For UI changes in:

- `templates/index.html`
- `static/css/style.css`
- `static/js/app.js`

invoke the `ui-screenshots` skill to refresh `screenshots/` and commit updated PNG files.
The skill is agent-driven: it can choose the screenshot scenes/count, but dark
and light sets must match exactly. The agent should also rewrite the entire
README screenshots section to match the generated files.

## Implementation constraints

- Keep behavior Docker-compatible (Python 3.14-slim base image).
- Keep changes minimal and directly related to the user request.
- Keep environment variable names stable unless explicitly asked to migrate them.
- Do not hardcode credentials, server URLs, or filesystem paths.
- Favor explicit logging and clear error messages.
- Update README when setup/behavior/config changes.
- Keep `README.md` focused on user-facing features, setup, and operational usage.
  Put backend implementation rationale, container hardening details, and
  agent/developer workflow guidance in this instruction file or other
  repository instructions instead of expanding the README with internal
  engineering detail unless users must act on it directly.
- Re-run validation commands after edits.
- When modifying `app/` modules, check that all imports in `web_app.py` still resolve correctly.
- **Keep CSP-compatible frontend code.** Do not add inline JavaScript in templates:
  no `onclick`/`onchange`/`onsubmit`/etc. attributes and no inline `<script>` blocks.
  Wire UI behavior in `static/js/app.js` via `addEventListener` instead.
- **Keep style CSP strict.** Do not add inline styles (`style="..."`) or JS style writes
  (`element.style...`) for UI behavior; prefer CSS classes toggled from JS.
  Keep CSP free of `'unsafe-inline'` for both scripts and styles.
- **When writing tests for CSP directives**, never use `urlparse` + the `in` operator on
  URL-derived data — CodeQL flags it as `py/incomplete-url-substring-sanitization`.
  Use exact `==` equality via `any()` instead:
  ```python
  # CORRECT
  assert any(source == 'https://i.ytimg.com' for source in directives.get('img-src', []))
  # WRONG (all flagged by CodeQL regardless of structure)
  assert 'https://i.ytimg.com' in csp_string
  assert 'i.ytimg.com' in img_src_hosts
  ```
  A CSP origin source like `https://i.ytimg.com` allows **all paths** under that origin,
  so asserting the exact token is present is sufficient.
- **Sessions must NOT survive container restarts.** `SECRET_KEY` is always generated
  fresh with `secrets.token_hex(32)` at startup and must never be read from an
  environment variable or made configurable. Do not add `SECRET_KEY` to `.env.example`,
  `docker-compose.yml`, or any documentation as a user-settable option.
- **CodeQL is enabled via GitHub repository settings** (Settings → Security → Code
  scanning). Do not add or modify `.github/workflows/codeql.yml` — a custom workflow
  file would duplicate or conflict with the managed configuration.

## Security notes

- **API key**: `GET /api/settings/runtime` is an **authenticated** endpoint — it requires a valid session cookie or API key header and returns the actual key in the response. The key is never written to `localStorage`. Users log in via the Settings page; the server sets an httpOnly session cookie (`POST /api/auth/login`). The key is kept in JS memory (`apiKey`) for the lifetime of the tab. When `API_KEY` is not set, the auto-generated startup API key is printed to the container log at startup.
- **yt-dlp**: `remote_components` must NOT be enabled (supply-chain risk — fetches and executes JS from GitHub at runtime).
- **ThemerrDB URLs**: always validate with `is_valid_youtube_url()` before passing to yt-dlp.

## Container hardening notes

- The Docker runtime baseline is Python 3.14 slim unless the user explicitly
  requests a different runtime line and the full validation set has been rerun.
- Prefer multi-stage builds that keep build tooling out of the final image.
- Preserve YouTube support while minimizing runtime package exposure:
  keep only the Node runtime needed by yt-dlp and only the OS packages required
  at runtime.
- Prefer official runtime stages over adding third-party apt repositories when
  the required runtime can be copied from an upstream image.
- Keep the deployment posture hardened: non-root container user, read-only root
  filesystem compatibility, dropped Linux capabilities, `no-new-privileges`,
  small writable tmpfs mounts, and an init process for child-process reaping.
- Prefer exposing Themarr through a trusted reverse proxy or private network
  segment so only intended clients can reach the Web UI and webhook endpoints.

## Key web UI files

| File | Purpose |
|---|---|
| `templates/index.html` | Single-page app shell |
| `static/css/style.css` | Sonarr-inspired dark/light theme CSS |
| `static/js/app.js` | Frontend logic (library browser, modals, multi-select, settings) |
| `.github/skills/ui-screenshots/SKILL.md` | Skill guide for capturing README screenshots |
