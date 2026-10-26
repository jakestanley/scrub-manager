"""Application configuration, read from environment variables (and .env)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _path(name, default):
    return os.path.abspath(os.path.expanduser(os.environ.get(name, default)))


def _int(name, default):
    return int(os.environ.get(name, default))


def _float(name, default):
    return float(os.environ.get(name, default))


# Where the source videos live (scanned recursively)
VIDEO_DIR = _path('VIDEO_DIR', '/var/videos')
# Where the SQLite database lives
DATA_DIR = _path('DATA_DIR', './data')
# Where generated scrub previews are written (mirrors VIDEO_DIR's structure)
SCRUB_DIR = _path('SCRUB_DIR', '~/.local/share/trickplay/scrubs')

PORT = _int('PORT', 5003)
DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'

# Number of concurrent preview generators
MAX_WORKERS = _int('MAX_WORKERS', 1)
# Seconds between automatic background scans of VIDEO_DIR
SCAN_INTERVAL = _int('SCAN_INTERVAL', 900)
# Give up on a video after this many failed attempts (retry with "Retry failed")
MAX_ATTEMPTS = _int('MAX_ATTEMPTS', 5)

# Preview shape: each second of preview covers SCRUB_SPEEDUP seconds of source
SCRUB_SPEEDUP = 60
SCRUB_WIDTH = 320
# When generating with ffmpeg, take one frame every N seconds of source
SCRUB_SAMPLE_SECONDS = 2
# Jellyfin's "Interval" trickplay setting (seconds between thumbnails). Jellyfin's default is 10.
JELLYFIN_TRICKPLAY_INTERVAL = _float('JELLYFIN_TRICKPLAY_INTERVAL', 10)

VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.m4v')
