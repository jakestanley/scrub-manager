# Scrub Manager

A web-based video management tool that generates fast scrub previews for quick video review and organization.

## Features

- **Fast Scrub Previews**: Generates compressed WebM previews where each frame represents 60 seconds of the original video
- **Video Management**: Scan directories, view file sizes, and delete unwanted videos
- **Tagging System**: Tag videos for organization with quick-tag buttons and filtering
- **Background Processing**: Queue-based preview generation with configurable worker count
- **Persistent State**: Queue and processing state survives application restarts
- **Low System Impact**: Uses nice priority and CPU throttling to minimize system load

## Quick Start

1. **Clone and setup**:
   ```bash
   git clone <repo-url>
   cd scrub-manager
   ./run.sh
   ```

2. **Configure**: The script will prompt for:
   - Video directory path (where your videos are stored)
   - Data directory (for cache and state files)
   - Number of workers (concurrent preview generation)

3. **Access**: Open http://localhost:5003 in your browser

## Configuration

Environment variables (stored in `.env`):

- `VIDEO_DIR`: Directory to scan for videos
- `DATA_DIR`: Directory for cache and state files (default: `./data`)
- `MAX_WORKERS`: Number of concurrent preview generators (default: `1`)
- `DEBUG`: Enable debug mode (default: `false`)
- `CACHE_DURATION`: Cache duration in seconds (default: `3600`)

## Requirements

- Python 3.7+
- ffmpeg (for video processing)
- Flask and python-dotenv (installed automatically)

## How It Works

1. **Scan**: Recursively scans the configured video directory for MP4, MKV, AVI, MOV, and M4V files
2. **Queue**: Videos without existing previews are queued for processing
3. **Generate**: Background workers create compressed WebM previews using ffmpeg
4. **Manage**: Web interface displays videos with previews, tagging, and deletion options

## Preview Specifications

- **Frame Rate**: 30fps output
- **Time Compression**: Each frame = 60 seconds of original video
- **Resolution**: 320px width (height auto-scaled)
- **Format**: WebM with VP9 codec
- **Minimum Duration**: 5 seconds
- **File Naming**: `{original_name}_scrub.webm`

## API Endpoints

- `GET /api/scan` - Scan for videos
- `GET /api/files` - Get paginated file list
- `GET /api/video` - Serve video preview
- `GET /api/queue` - Get processing queue status
- `POST /api/tag` - Add/remove video tags
- `POST /api/delete` - Delete video file

