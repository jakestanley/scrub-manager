"""Background threads: a periodic scanner and the scrub generation workers."""
import os
import threading
import time

import config
import scrubber
import store

shutdown_flag = threading.Event()
scan_requested = threading.Event()
first_scan_done = threading.Event()
_threads = []
last_scan = {'started_at': None, 'finished_at': None, 'found': 0, 'error': None}


def scan():
    """Walk VIDEO_DIR, record what's there, and queue anything that needs a scrub."""
    last_scan['started_at'] = time.time()
    if not os.path.isdir(config.VIDEO_DIR):
        # Most likely an unmounted share; don't forget every video because of it
        last_scan['error'] = f'Video directory not found: {config.VIDEO_DIR}'
        print(last_scan['error'])
        return

    found = []
    for root, dirs, files in os.walk(config.VIDEO_DIR):
        # Jellyfin's trickplay folders only hold images
        dirs[:] = [d for d in dirs if not d.endswith('.trickplay')]
        for filename in files:
            if filename.startswith('._') or not filename.lower().endswith(config.VIDEO_EXTENSIONS):
                continue
            path = os.path.join(root, filename)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            found.append({
                'path': path,
                'filename': filename,
                'directory': root,
                'size_bytes': stat.st_size,
                'mtime': stat.st_mtime,
            })

    if not found:
        last_scan['error'] = f'No videos found in {config.VIDEO_DIR}; not removing anything'
        print(last_scan['error'])
        return

    added, changed = store.sync_scanned(found)
    removed = store.remove_missing({v['path'] for v in found})
    for path in removed:
        _remove_scrub(path)

    # Regenerate scrubs that went missing, and swap ffmpeg scrubs for Jellyfin's once Jellyfin has made them
    requeued = 0
    for video in store.ready_videos():
        missing = not os.path.exists(scrubber.scrub_path_for(video['path']))
        jellyfin_now_available = video['source'] == 'ffmpeg' and scrubber.find_jellyfin_trickplay(video['path'])
        if missing or jellyfin_now_available:
            store.mark_pending(video['path'])
            requeued += 1

    last_scan.update(finished_at=time.time(), found=len(found), error=None)
    print(f'Scan complete: {len(found)} videos, {added} new, {changed} changed, '
          f'{len(removed)} removed, {requeued} requeued')


def _remove_scrub(video_path):
    scrub_path = scrubber.scrub_path_for(video_path)
    if os.path.exists(scrub_path):
        os.remove(scrub_path)


def delete_video(video_path):
    os.remove(video_path)
    _remove_scrub(video_path)
    store.remove_video(video_path)


def probe_missing_metadata():
    """Read duration and bit rate for videos that don't have them yet."""
    paths = store.videos_missing_metadata()
    if paths:
        print(f'Reading metadata for {len(paths)} videos')
    for path in paths:
        # Let a requested scan or shutdown go first; the rest is picked up next time round
        if shutdown_flag.is_set() or scan_requested.is_set():
            return
        if not os.path.exists(path):
            # Probably an unmounted share; try again later rather than recording "unknown"
            continue
        try:
            duration, bit_rate = scrubber.probe(path)
        except Exception as e:
            print(f'Could not read metadata for {path}: {e}')
            duration, bit_rate = None, 0
        store.set_metadata(path, duration, bit_rate)


def _scanner_loop():
    while not shutdown_flag.is_set():
        try:
            scan()
        except Exception as e:
            last_scan['error'] = str(e)
            print(f'Scan failed: {e}')
        first_scan_done.set()
        try:
            probe_missing_metadata()
        except Exception as e:
            print(f'Reading metadata failed: {e}')
        scan_requested.wait(timeout=config.SCAN_INTERVAL)
        scan_requested.clear()


def _worker_loop(worker_id):
    # Let the first scan forget videos deleted while we were stopped, so we don't try to process them
    while not first_scan_done.wait(timeout=1):
        if shutdown_flag.is_set():
            return
    while not shutdown_flag.is_set():
        video_path = store.claim_next()
        if video_path is None:
            shutdown_flag.wait(timeout=5)
            continue

        print(f'Worker {worker_id}: generating {video_path}')
        started = time.time()
        try:
            source = scrubber.generate(video_path)
        except Exception as e:
            if shutdown_flag.is_set():
                # Interrupted, not broken; pick it up again next start
                store.mark_pending(video_path)
            else:
                print(f'Worker {worker_id}: failed {video_path}: {e}')
                store.mark_error(video_path, str(e))
            continue
        store.mark_ready(video_path, source)
        print(f'Worker {worker_id}: done in {time.time() - started:.1f}s from {source}: {video_path}')


def request_scan():
    scan_requested.set()


def start():
    _threads.append(threading.Thread(target=_scanner_loop, name='scanner', daemon=True))
    for i in range(config.MAX_WORKERS):
        _threads.append(threading.Thread(target=_worker_loop, args=(i + 1,), name=f'worker-{i + 1}', daemon=True))
    for thread in _threads:
        thread.start()


def stop():
    shutdown_flag.set()
    scan_requested.set()
    scrubber.stop_all()
    for thread in _threads:
        thread.join(timeout=10)
