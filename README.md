# Scrub Manager

A web app for quickly reviewing a video library. Every video gets a short, fast-forward "scrub" preview so you can see what's in it at a glance, then tag or delete it.

## Features

- **Background scanning**: the video directory is rescanned on a timer; new and changed videos are queued automatically
- **Uses Jellyfin trickplay when it exists**: if Jellyfin has saved trickplay tiles next to a video, the preview is built from those in under a second instead of decoding the video
- **Fast ffmpeg fallback**: otherwise, only keyframes are decoded and sampled (about 4 seconds for a 30 minute 1080p video)
- **Previews stored separately**: scrubs go in `SCRUB_DIR`, never in your media folders
- **Retries**: failed previews are retried with increasing delays, and can be retried by hand
- **Tagging, filtering (by name, tag, folder), sorting, deletion**
- **Low system impact**: ffmpeg runs at the lowest CPU and IO priority

## Quick start

```bash
./run.sh
```

The first run creates `.env` from `.env.example` and exits so you can edit it. Then run it again and open http://localhost:5003.

Requires Python 3.9+ and ffmpeg/ffprobe (with libvpx-vp9).

## Configuration

Set in `.env` (see `.env.example`):

| Variable | Default | Meaning |
|---|---|---|
| `VIDEO_DIR` | `/var/videos` | Directory scanned recursively for `.mp4 .mkv .avi .mov .m4v` |
| `SCRUB_DIR` | `~/.local/share/trickplay/scrubs` | Where previews are written, mirroring `VIDEO_DIR`'s layout |
| `DATA_DIR` | `./data` | SQLite database (videos, preview status, tags) |
| `MAX_WORKERS` | `1` | Concurrent preview generators |
| `SCAN_INTERVAL` | `900` | Seconds between background scans |
| `MAX_ATTEMPTS` | `5` | Give up on a preview after this many failures |
| `JELLYFIN_TRICKPLAY_INTERVAL` | `10` | Jellyfin's trickplay interval setting, in seconds |
| `PORT` | `5003` | HTTP port |
| `DEBUG` | `false` | Flask debug mode |

## Jellyfin trickplay

Jellyfin (10.9+) stores trickplay as JPEG tile sheets. They are only visible to this app when Jellyfin is set to **save trickplay images next to media** (Library settings → "Save trickplay images next to media"), which produces:

```
Film.mkv
Film.trickplay/320 - 10x10/0.jpg, 1.jpg, ...
```

With that setting off, Jellyfin keeps them in its own metadata folder keyed by internal item IDs, and this app falls back to ffmpeg.

If Jellyfin generates trickplay for a video after an ffmpeg preview was made, the next scan swaps the preview for one built from Jellyfin's tiles.

## Previews

- Each second of preview covers 60 seconds of video (a 2 hour film becomes a 2 minute preview)
- 320px wide, VP9 WebM, no audio
- Stored at `SCRUB_DIR/<path relative to VIDEO_DIR>.webm`, e.g. `Films/Film.mkv.webm`
- Written to a `.part` file first, so an interrupted run never leaves a broken preview behind

## Upgrading from the old version

Tags from `data/video_tags.json` are imported into the database on first start. `video_cache.json` and `queue_state.json` are no longer used.

Old previews were written next to the videos as `*_scrub.webm`. They are ignored now; to remove them:

```bash
find "$VIDEO_DIR" -name '*_scrub.webm' -print     # check first
find "$VIDEO_DIR" -name '*_scrub.webm' -delete
```

## API

- `GET /api/files` - paginated video list (`page`, `per_page`, `sort`, `name`, `tag`, `folder`)
- `GET /api/folders` - folders containing videos, relative to `VIDEO_DIR`
- `GET /api/scrub?path=` - preview video, or `202` with its status if it isn't ready
- `GET /api/original?path=` - original video file
- `GET /api/status` - preview counts, last scan, configuration
- `POST /api/scan` - scan now
- `POST /api/retry` - requeue failed previews
- `GET /api/tags`, `POST /api/tag` - list tags; add/remove a tag (`filepath`, `tag`, `action`)
- `POST /api/delete` - delete a video and its preview (`filepath`)

Paths must be videos the scanner found inside `VIDEO_DIR`.
