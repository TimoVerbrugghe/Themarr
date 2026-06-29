"""YouTube URL validation and yt-dlp option helpers."""
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
import yt_dlp

from app.media_utils import MAX_UPLOAD_BYTES

logger = logging.getLogger(__name__)

ALLOWED_YOUTUBE_HOSTS = {
    'youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com', 'youtu.be',
}
MAX_YOUTUBE_DURATION_SECONDS = 15 * 60


def is_valid_youtube_url(url):
    """Validate that a URL points to a supported YouTube host."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in {'http', 'https'}:
        return False

    hostname = (parsed.hostname or '').lower()
    return hostname in ALLOWED_YOUTUBE_HOSTS


def youtube_match_filter(info_dict, *, incomplete):
    """Reject overly long videos before downloading."""
    duration = info_dict.get('duration')
    if duration and duration > MAX_YOUTUBE_DURATION_SECONDS:
        return f'Video exceeds {MAX_YOUTUBE_DURATION_SECONDS} seconds'
    return None


def _youtube_retry_profiles():
    """Yield yt-dlp retry profiles for videos with client-specific availability."""
    return [
        ('default', {}),
        ('android', {'extractor_args': {'youtube': {'player_client': ['android']}}}),
    ]


def _youtube_preview_ydl_opts(profile_overrides=None):
    """Build yt-dlp options for extracting a preview audio stream URL.

    NOTE: Do NOT add 'remote_components' here.  That option instructs yt-dlp
    to fetch and execute JavaScript from an external source (e.g. GitHub) at
    runtime, which is a supply-chain remote-code-execution risk in a server
    process.  The bundled yt-dlp extractor is sufficient for standard YouTube
    URLs.
    """
    opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'skip_download': True,
        'socket_timeout': 30,
    }
    if profile_overrides:
        opts.update(profile_overrides)
    return opts


def _youtube_download_ydl_opts(tmpdir, profile_overrides=None):
    """Build yt-dlp options for downloading and converting a theme MP3."""
    opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(tmpdir, 'theme.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'match_filter': youtube_match_filter,
        'max_filesize': MAX_UPLOAD_BYTES,
    }
    if profile_overrides:
        opts.update(profile_overrides)
    return opts


def _clean_yt_dlp_error(exc):
    """Normalize yt-dlp error messages for user-facing responses."""
    return str(exc).removeprefix('ERROR: ').strip()


def _stream_http_response_chunks(response, *, chunk_size=8192):
    """Yield streamed HTTP response chunks while handling client disconnects."""
    try:
        for chunk in response.iter_content(chunk_size=chunk_size):
            if chunk:
                yield chunk
    except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError, BrokenPipeError, ConnectionResetError) as exc:
        logger.info('Stream interrupted while sending preview audio: %s', exc)
    finally:
        response.close()


_ALLOWED_AUDIO_STREAM_HOSTS = {'googlevideo.com', 'youtube.com', 'googleusercontent.com'}


def extract_youtube_audio_url(youtube_url):
    """Resolve a direct audio stream URL for a YouTube video with retries."""
    errors = []
    for profile_name, overrides in _youtube_retry_profiles():
        try:
            with yt_dlp.YoutubeDL(_youtube_preview_ydl_opts(overrides)) as ydl:
                info = ydl.extract_info(youtube_url, download=False)
            audio_url = info.get('url')
            if audio_url:
                return audio_url
            errors.append(f'{profile_name}: Could not extract audio from YouTube')
        except yt_dlp.utils.DownloadError as exc:
            errors.append(f'{profile_name}: {_clean_yt_dlp_error(exc)}')
    raise yt_dlp.utils.DownloadError(' | '.join(errors))


def is_valid_audio_stream_url(url):
    """Return True when a yt-dlp resolved stream URL is from an allowed CDN host.

    This guards against SSRF: yt-dlp should only return googlevideo.com (or
    similar Google CDN) URLs for YouTube content.  Any other host is rejected.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {'https', 'http'}:
            return False
        hostname = (parsed.hostname or '').lower()
        return any(
            hostname == h or hostname.endswith('.' + h)
            for h in _ALLOWED_AUDIO_STREAM_HOSTS
        )
    except ValueError:
        return False


def download_youtube_theme_mp3(youtube_url, tmpdir):
    """Download YouTube audio as MP3 with client-profile fallback retries."""
    errors = []
    for profile_name, overrides in _youtube_retry_profiles():
        try:
            with yt_dlp.YoutubeDL(_youtube_download_ydl_opts(tmpdir, overrides)) as ydl:
                ydl.download([youtube_url])
            mp3_files = list(Path(tmpdir).glob('*.mp3'))
            if mp3_files:
                return mp3_files[0]
            errors.append(f'{profile_name}: Download failed: no MP3 file produced')
        except yt_dlp.utils.DownloadError as exc:
            errors.append(f'{profile_name}: {_clean_yt_dlp_error(exc)}')
    raise yt_dlp.utils.DownloadError(' | '.join(errors))
