"""Build scrub previews, from Jellyfin trickplay tiles when available, otherwise with ffmpeg."""
import math
import os
import re
import shutil
import subprocess
import threading

import config

OUTPUT_FPS = config.SCRUB_SPEEDUP / config.SCRUB_SAMPLE_SECONDS
TRICKPLAY_RESOLUTION_DIR = re.compile(r'(\d+) - (\d+)x(\d+)')

VP9_ARGS = [
    '-an', '-c:v', 'libvpx-vp9', '-crf', '40', '-b:v', '0',
    '-deadline', 'realtime', '-cpu-used', '8', '-row-mt', '1',
]

# Running ffmpeg processes, so shutdown can stop them
_active = set()
_active_lock = threading.Lock()


class ScrubError(Exception):
    pass


def scrub_path_for(video_path):
    """Scrubs mirror VIDEO_DIR's layout under SCRUB_DIR, e.g. Films/A.mkv -> Films/A.mkv.webm"""
    rel = os.path.relpath(video_path, config.VIDEO_DIR)
    return os.path.join(config.SCRUB_DIR, rel + '.webm')


def probe_duration(video_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', video_path],
        capture_output=True, text=True, timeout=60)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return None


def find_jellyfin_trickplay(video_path):
    """Find trickplay tiles Jellyfin saved next to the media ("Save trickplay images next to media").

    Jellyfin writes <video name>.trickplay/<width> - <cols>x<rows>/0.jpg, 1.jpg, ...
    Returns (tile_dir, width, cols, rows) or None.
    """
    trickplay_dir = os.path.splitext(video_path)[0] + '.trickplay'
    if not os.path.isdir(trickplay_dir):
        return None

    candidates = []
    for name in os.listdir(trickplay_dir):
        match = TRICKPLAY_RESOLUTION_DIR.fullmatch(name)
        tile_dir = os.path.join(trickplay_dir, name)
        if match and os.path.isfile(os.path.join(tile_dir, '0.jpg')):
            width, cols, rows = (int(g) for g in match.groups())
            candidates.append((width, tile_dir, cols, rows))
    if not candidates:
        return None

    # Prefer the smallest set at least as wide as our scrubs, otherwise the widest available
    wide_enough = [c for c in candidates if c[0] >= config.SCRUB_WIDTH]
    width, tile_dir, cols, rows = min(wide_enough) if wide_enough else max(candidates)
    return tile_dir, width, cols, rows


def generate(video_path):
    """Write the scrub for video_path into SCRUB_DIR. Returns the source used ('jellyfin' or 'ffmpeg')."""
    output_path = scrub_path_for(video_path)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    partial_path = output_path + '.part'

    duration = probe_duration(video_path)
    trickplay = find_jellyfin_trickplay(video_path)
    if trickplay:
        source = 'jellyfin'
        cmd = _jellyfin_command(trickplay, duration, partial_path)
    else:
        source = 'ffmpeg'
        cmd = _ffmpeg_command(video_path, partial_path)

    # Sampling runs at roughly 400x realtime; allow plenty of headroom
    timeout = 300 + (duration or 0) / 4
    try:
        _run(cmd, timeout)
        os.replace(partial_path, output_path)
    finally:
        if os.path.exists(partial_path):
            os.remove(partial_path)
    return source


def _ffmpeg_command(video_path, output_path):
    # Decode keyframes only and keep one frame every SCRUB_SAMPLE_SECONDS. 0:V:0 skips embedded cover art.
    return [
        'ffmpeg', '-nostdin', '-y', '-loglevel', 'error',
        '-skip_frame', 'nokey', '-i', video_path,
        '-map', '0:V:0',
        '-vf', f'fps=1/{config.SCRUB_SAMPLE_SECONDS},scale={config.SCRUB_WIDTH}:-2,setpts=N/{OUTPUT_FPS}/TB',
        '-r', str(OUTPUT_FPS),
        *VP9_ARGS, '-f', 'webm', output_path,
    ]


def _jellyfin_command(trickplay, duration, output_path):
    tile_dir, width, cols, rows = trickplay
    # Keep the same speed-up as ffmpeg scrubs: one thumbnail covers JELLYFIN_TRICKPLAY_INTERVAL seconds
    fps = config.SCRUB_SPEEDUP / config.JELLYFIN_TRICKPLAY_INTERVAL
    cmd = [
        'ffmpeg', '-nostdin', '-y', '-loglevel', 'error',
        '-framerate', '1', '-start_number', '0', '-i', os.path.join(tile_dir, '%d.jpg'),
        '-vf', f'untile={cols}x{rows},scale={config.SCRUB_WIDTH}:-2,setpts=N/{fps}/TB',
        '-r', str(fps),
    ]
    if duration:
        # The last sheet is padded with blank cells; stop after the real thumbnails
        thumbnails = max(1, math.floor(duration / config.JELLYFIN_TRICKPLAY_INTERVAL))
        cmd += ['-frames:v', str(thumbnails)]
    return cmd + [*VP9_ARGS, '-f', 'webm', output_path]


def _run(cmd, timeout):
    # Lowest CPU and IO priority so playback on the same machine isn't affected
    prefix = ['nice', '-n', '19']
    if shutil.which('ionice'):
        prefix += ['ionice', '-c', '3']
    proc = subprocess.Popen(prefix + cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    with _active_lock:
        _active.add(proc)
    try:
        _, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise ScrubError(f'ffmpeg timed out after {timeout:.0f}s')
    finally:
        with _active_lock:
            _active.discard(proc)
    if proc.returncode != 0:
        raise ScrubError(f'ffmpeg exited {proc.returncode}: {stderr.strip()}')


def stop_all():
    with _active_lock:
        for proc in _active:
            proc.terminate()
