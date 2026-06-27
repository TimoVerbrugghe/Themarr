# Themarr

<p align="center">
  <img src="static/logo-themarr.svg" alt="Themarr logo" width="420">
</p>

Themarr manages Plex theme music for TV shows and movies. It includes a Flask-based web UI, a CLI batch downloader, Sonarr/Radarr webhook integration, and Pushover notifications.

## Screenshots

Screenshots are kept up-to-date automatically:

- Pushes to `main` that touch `templates/`, `static/`, or `web_app.py`
  (typically after a PR merge) trigger the
  [screenshots workflow](.github/workflows/screenshots.yml), which regenerates
  screenshots and commits them back to `main`.
- Commits/PRs that directly modify `screenshots/**` are auto-sanitized by
  `.github/workflows/sanitize-screenshot-changes.yml` (same-repo branches/PRs).
  For fork PRs, the workflow cannot push fixes and fails with instructions.

### Dark theme

| Poster view | List view |
|---|---|
| ![Poster view dark](screenshots/01_poster_view_dark.png) | ![List view dark](screenshots/02_list_view_dark.png) |

| YouTube downloader | Copy theme from |
|---|---|
| ![YouTube downloader dark](screenshots/03_youtube_downloader_dark.png) | ![Copy theme dark](screenshots/04_copy_theme_dark.png) |

| Plex download |
|---|
| ![Plex download dark](screenshots/05_plex_download_dark.png) |

### Light theme

| Poster view | List view |
|---|---|
| ![Poster view light](screenshots/06_poster_view_light.png) | ![List view light](screenshots/07_list_view_light.png) |

| YouTube downloader | Copy theme from |
|---|---|
| ![YouTube downloader light](screenshots/08_youtube_downloader_light.png) | ![Copy theme light](screenshots/09_copy_theme_light.png) |

| Plex download |
|---|
| ![Plex download light](screenshots/10_plex_download_light.png) |

## Features

- **Web UI** — dark/light theme with in-header toggle (configurable via `DEFAULT_THEME`), poster thumbnails, in-browser audio playback with loading indicator; toggle between **poster (grid)** and **compact list** views (configurable via `DEFAULT_VIEW`); list view includes inline play/pause preview per item
- **Multi-select** — select any number of items and bulk-download their themes in one click
- **Per-item actions** — download from Plex (with preview), download from YouTube via `yt-dlp`, upload custom MP3, delete
- **Settings page** — quick-action buttons (test Plex, refresh libraries, test Pushover, rescan files) and a full environment variable reference table
- **Sonarr/Radarr webhooks** — auto-download themes when a new series or movie is added; staggered retry loop until Plex picks up the new item
- **Pushover notifications** — push notification on every theme download (optional)
- **CLI batch downloader** — process whole TV / movie libraries non-interactively
- **Docker-first** — multi-platform image (`linux/amd64`, `linux/arm64`) published to `ghcr.io`

## Quick start

```bash
cp .env.example .env
# edit .env with your Plex URL, token, and media paths

docker compose up --build
```

Open `http://localhost:8080`.

Or pull the pre-built image:

```bash
docker pull ghcr.io/timoverbrugghe/themarr:latest
```

## Requirements

- Docker / Docker Compose
- Plex Media Server with a TV Shows and/or Movies library
- Writable media folders mounted into the container **at the same path as your Plex container**
- `ffmpeg` (included in the Docker image) for YouTube audio extraction

## Configuration

### Mount paths

Themarr resolves media paths directly from what Plex reports. For this to work, **mount your library folders at the same path inside the Themarr container as they are mounted inside your Plex container**.

For example, if Plex has `/media/tvshows` and `/media/movies` as library locations:

```yaml
volumes:
  - /media/tvshows:/media/tvshows
  - /media/movies:/media/movies
```

No path-mapping environment variables are needed.

### Core

| Variable | Required | Default | Description |
|---|---|---|---|
| `PLEX_URL` | ✅ | — | Plex server URL, e.g. `http://192.168.1.100:32400` |
| `PLEX_TOKEN` | ✅ | — | Plex API authentication token |
| `TV_SHOWS_HOST_PATH` | ✅ | `/mnt/tv` | Host path for TV shows — mounted at the same path in the container |
| `MOVIES_HOST_PATH` | — | `/mnt/movies` | Host path for movies — mounted at the same path in the container |
| `FLASK_DEBUG` | — | `false` | Enable Flask debug mode |
| `DEFAULT_THEME` | — | `dark` | Default UI theme: `dark` or `light` (user can override in-browser) |
| `DEFAULT_VIEW` | — | `list` | Default library view: `list` or `grid` (user can override in-browser) |
| `VERBOSE` | — | `false` | Verbose CLI batch-downloader logging |

### Pushover notifications

Set both variables to enable push notifications on theme downloads.

| Variable | Description |
|---|---|
| `PUSHOVER_APP_TOKEN` | Pushover application token |
| `PUSHOVER_USER_KEY` | Pushover user or group key |

### Webhooks (Sonarr / Radarr)

Point Sonarr's webhook connection to `POST http://<themarr>:8080/api/webhooks/sonarr` and Radarr's to `/api/webhooks/radarr`.

| Variable | Description |
|---|---|
| `WEBHOOK_USERNAME` | HTTP Basic Auth username (leave blank to disable auth) |
| `WEBHOOK_PASSWORD` | HTTP Basic Auth password |
| `PLEX_RETRY_ATTEMPTS` | Max Plex polling attempts after add event (default: `10`) |
| `PLEX_RETRY_DELAY` | Base delay in seconds between retries — staggered linearly (default: `30`) |

On a `SeriesAdd` or `MovieAdded` event, Themarr starts a background thread that polls Plex for the new item and downloads its theme as soon as it appears. Delays are `30 s, 60 s, 90 s, …` up to `PLEX_RETRY_ATTEMPTS * PLEX_RETRY_DELAY` total wait time.

Supported webhook event types:

| Source | Handled events |
|---|---|
| Sonarr | `SeriesAdd`, `SeriesDelete` (logged, no action), `Test` |
| Radarr | `MovieAdded`, `MovieDeleted` (logged, no action), `Test` |

## Web app API

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Plex connection health |
| `GET` | `/api/libraries` | List TV/Movie libraries |
| `GET` | `/api/libraries/<id>/items` | Items in a library with theme status |
| `GET` | `/api/poster/<key>` | Proxy Plex poster image |
| `GET` | `/api/items/<key>/theme` | Stream local `theme.mp3` |
| `GET` | `/api/items/<key>/theme/preview` | Stream Plex theme without saving |
| `POST` | `/api/items/<key>/theme/download` | Download theme from Plex |
| `POST` | `/api/items/<key>/theme/upload` | Upload a custom MP3 |
| `POST` | `/api/items/<key>/theme/youtube` | Download from YouTube URL |
| `DELETE` | `/api/items/<key>/theme` | Delete local `theme.mp3` |
| `POST` | `/api/bulk/theme/download` | Bulk-download themes (`ratingKeys` list, max 100) |
| `POST` | `/api/webhooks/sonarr` | Sonarr webhook receiver |
| `POST` | `/api/webhooks/radarr` | Radarr webhook receiver |
| `POST` | `/api/settings/test-pushover` | Send a test Pushover notification |
| `POST` | `/api/settings/rescan` | Rescan local files and return theme counts |

## CLI batch downloader

```bash
# Run once for TV + Movies
docker compose run --rm themarr python plex_theme_downloader.py
```

Or locally:

```bash
pip install -r requirements.txt
python3 plex_theme_downloader.py
```

## Docker image

The image is automatically built and pushed to GitHub Container Registry on every push to `main` and on semver tags.

```
ghcr.io/timoverbrugghe/themarr:latest   ← latest main build
ghcr.io/timoverbrugghe/themarr:main     ← explicit main tag
ghcr.io/timoverbrugghe/themarr:1.2.3   ← tagged release
```

Multi-platform: `linux/amd64` and `linux/arm64`.

## Validation

```bash
python3 -m py_compile plex_theme_downloader.py
python3 -m py_compile web_app.py
docker compose config
docker build -t themarr:test .
python3 -m pytest tests/ -v
```

### Regenerating screenshots

If you change `templates/index.html`, `static/css/style.css`, or
`static/js/app.js`, CI will regenerate screenshots automatically.
The `.github/workflows/screenshots.yml` CI workflow does this automatically
after merge on `main` (auto-commit).
If `screenshots/**` changes are committed in branch work, CI sanitizes them for
same-repo branches/PRs.
