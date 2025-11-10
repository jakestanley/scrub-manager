# Jellyfin Trickplay Manager

A web application to scan, review, and manage Jellyfin trickplay files (.webm videos in .trickplay directories).

## Features

- Recursively scans for .trickplay directories
- Displays webm files with video previews
- Shows file path, name, and size for each video
- Paginated results for large collections
- Caching to avoid re-scanning on every request
- Delete functionality with confirmation
- Responsive web interface

## Quick Start

```bash
# Install and run
./run.sh

# Or manually:
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 in your browser.

## Usage

1. Enter the path to scan (default: `/var/lib/jellyfin/metadata`)
2. Click "Scan" to find all trickplay files
3. Review the video previews in the grid layout
4. Delete unwanted files using the delete button
5. Use "Force Refresh" to clear cache and re-scan

## Configuration

- **Cache Duration**: 1 hour (modify `CACHE_DURATION` in app.py)
- **Default Path**: `/var/lib/jellyfin/metadata` (change in the web UI)
- **Port**: 5000 (modify in app.py)
- **Items per page**: 10/20/50 (selectable in web UI)
