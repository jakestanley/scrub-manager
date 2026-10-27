#!/usr/bin/env python3
import mimetypes
import os
import signal
import sys

from flask import Flask, jsonify, render_template, request, send_file

import config
import scrubber
import store
import worker

app = Flask(__name__)


def get_file_size(size):
    """Human readable file size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def known_video(path):
    """Return the stored video for path, only if it's one we scanned inside VIDEO_DIR."""
    if not path:
        return None
    root = os.path.realpath(config.VIDEO_DIR)
    if os.path.commonpath([os.path.realpath(path), root]) != root:
        return None
    return store.get_video(path)


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/files')
def get_files():
    """Get paginated file list"""
    page = max(1, int(request.args.get('page', 1)))
    per_page = min(100, max(1, int(request.args.get('per_page', 20))))
    folder = request.args.get('folder', '').strip('/')
    videos, total = store.list_videos(
        page, per_page,
        sort=request.args.get('sort', 'size_desc'),
        name_filter=request.args.get('name', '').strip(),
        tag_filter=request.args.get('tag'),
        folder=os.path.join(config.VIDEO_DIR, folder) if folder else None,
    )
    files = [{
        'filename': v['filename'],
        'full_path': v['path'],
        'path': v['directory'],
        'size': get_file_size(v['size_bytes']),
        'size_bytes': v['size_bytes'],
        'tags': v['tags'] or ['untagged'],
        'status': v['status'],
        'source': v['source'],
        'last_error': v['last_error'],
        'bit_rate': v['bit_rate'],
    } for v in videos]
    return jsonify({
        'files': files,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': (total + per_page - 1) // per_page,
    })


@app.route('/api/scrub')
def serve_scrub():
    """Serve a video's scrub preview"""
    video = known_video(request.args.get('path'))
    if video is None:
        return jsonify({'error': 'Unknown video'}), 404
    scrub_path = scrubber.scrub_path_for(video['path'])
    if video['status'] == 'ready' and os.path.exists(scrub_path):
        return send_file(scrub_path, mimetype='video/webm')
    return jsonify({'status': video['status'], 'error': video['last_error']}), 202


@app.route('/api/original')
def serve_original():
    """Serve the original source video file"""
    video = known_video(request.args.get('path'))
    if video is None or not os.path.exists(video['path']):
        return jsonify({'error': 'File not found'}), 404
    mime_type, _ = mimetypes.guess_type(video['path'])
    download = request.args.get('download') == '1'
    return send_file(video['path'], mimetype=mime_type or 'application/octet-stream', as_attachment=download)


@app.route('/api/status')
def status():
    """Queue counts, last scan, and configuration"""
    return jsonify({
        'counts': store.status_counts(),
        'last_scan': worker.last_scan,
        'video_dir': config.VIDEO_DIR,
        'scrub_dir': config.SCRUB_DIR,
        'max_workers': config.MAX_WORKERS,
        'scan_interval': config.SCAN_INTERVAL,
    })


@app.route('/api/scan', methods=['POST'])
def scan():
    """Ask the background scanner to run now"""
    worker.request_scan()
    return jsonify({'success': True}), 202


@app.route('/api/retry', methods=['POST'])
def retry():
    """Requeue every video whose scrub failed"""
    return jsonify({'success': True, 'requeued': store.retry_failed()})


@app.route('/api/folders')
def get_folders():
    """Folders containing videos, relative to VIDEO_DIR, including their parent folders"""
    folders = set()
    for directory in store.list_directories():
        rel = os.path.relpath(directory, config.VIDEO_DIR)
        while rel not in ('', '.'):
            folders.add(rel)
            rel = os.path.dirname(rel)
    return jsonify(sorted(folders, key=str.lower))


@app.route('/api/tags')
def get_all_tags():
    return jsonify(store.all_tags())


@app.route('/api/tag', methods=['POST'])
def tag_file():
    """Add/remove tags for a video file"""
    video = known_video(request.json.get('filepath'))
    tag = (request.json.get('tag') or '').strip()
    action = request.json.get('action', 'add')
    if video is None or not tag or tag == 'untagged':
        return jsonify({'error': 'Missing or invalid filepath or tag'}), 400

    if action == 'add':
        store.add_tag(video['path'], tag)
    elif action == 'remove':
        store.remove_tag(video['path'], tag)
    return jsonify({'tags': store.tags_for(video['path']) or ['untagged']})


@app.route('/api/delete', methods=['POST'])
def delete_file():
    """Delete a video file and its scrub"""
    video = known_video(request.json.get('filepath'))
    if video is None or not os.path.exists(video['path']):
        return jsonify({'error': 'File not found'}), 404
    try:
        worker.delete_video(video['path'])
    except OSError as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'success': True})


def shutdown(signum, frame):
    print(f"Received signal {signum}, shutting down...")
    worker.stop()
    sys.exit(0)


if __name__ == '__main__':
    print("Starting Scrub Manager")
    print(f"Video directory: {config.VIDEO_DIR}")
    print(f"Scrub directory: {config.SCRUB_DIR}")
    print(f"Data directory: {config.DATA_DIR}")
    print(f"Workers: {config.MAX_WORKERS}, scanning every {config.SCAN_INTERVAL}s")

    os.makedirs(config.SCRUB_DIR, exist_ok=True)
    store.init()
    worker.start()
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    app.run(debug=config.DEBUG, host='0.0.0.0', port=config.PORT, use_reloader=False)
