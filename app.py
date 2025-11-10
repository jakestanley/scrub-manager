#!/usr/bin/env python3
import os
import json
import time
import subprocess
import tempfile
import shutil
import threading
import queue
import shlex
import signal
import sys
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file
from werkzeug.utils import safe_join

app = Flask(__name__)
app.config['CACHE_FILE'] = 'video_cache.json'
app.config['TAGS_FILE'] = 'video_tags.json'
app.config['CACHE_DURATION'] = 3600  # 1 hour

# Preview configuration
SAMPLE_INTERVAL = 60  # seconds between samples
OUTPUT_FPS = 30       # output video framerate
OUTPUT_WIDTH = 320    # output video width
OUTPUT_HEIGHT = 240   # output video height
BITRATE = '200k'      # output video bitrate
MIN_PREVIEW_DURATION = 5  # minimum preview duration in seconds

# Queue configuration
MAX_WORKERS = 1       # number of concurrent workers

# Queue for background webm generation
webm_queue = queue.Queue()
processing_status = {}
queue_stats = {'processed': 0, 'total': 0}
shutdown_flag = threading.Event()
worker_threads = []

def get_file_size(filepath):
    """Get human readable file size"""
    size = os.path.getsize(filepath)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"

def scan_video_files(root_path):
    """Recursively scan for video files"""
    video_items = []
    
    print(f"Starting scan of: {root_path}")
    
    for root, dirs, files in os.walk(root_path):
        for file in files:
            if file.lower().endswith(('.mp4', '.mkv', '.avi', '.mov', '.m4v')):
                video_path = os.path.join(root, file)
                video_size = os.path.getsize(video_path)
                
                print(f"Found video: {video_path} ({get_file_size(video_path)})")
                
                video_items.append({
                    'path': root,
                    'filename': file,
                    'full_path': video_path,
                    'size': get_file_size(video_path),
                    'size_bytes': video_size
                })
    
    print(f"Scan complete: {len(video_items)} video files")
    return sorted(video_items, key=lambda x: x['size_bytes'], reverse=True)



def generate_scrub_webm(video_path):
    """Generate scrub webm from video file using ffmpeg"""
    video_dir = os.path.dirname(video_path)
    video_name = os.path.splitext(os.path.basename(video_path))[0]
    webm_path = os.path.join(video_dir, f"{video_name}_scrub.webm")
    
    # Check if webm already exists
    if os.path.exists(webm_path):
        return webm_path
    
    try:
        processing_status[video_path] = 'processing'
        
        print(f"Generating preview for: {os.path.basename(video_path)}")
        
        # Generate scrub webm using ffmpeg with timeout
        cmd = [
            'ffmpeg', '-i', video_path,
            '-filter:v', f'setpts=(PTS-STARTPTS)*0.0167,scale={OUTPUT_WIDTH}:-1',
            '-an',
            '-c:v', 'libvpx-vp9', '-crf', '40', '-b:v', '0',
            '-speed', '4', '-tile-columns', '4', '-frame-parallel', '1',
            '-g', '30', '-keyint_min', '30',
            webm_path
        ]
        
        print(f"Running command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)  # 5 minute timeout
        processing_status[video_path] = 'completed'
        print(f"Completed webm generation: {os.path.basename(video_path)}")
        return webm_path
        
    except Exception as e:
        print(f"Error generating scrub webm: {e}")
        processing_status[video_path] = 'error'
        return None

def webm_worker(worker_id):
    """Background worker to process webm generation queue"""
    while not shutdown_flag.is_set():
        try:
            video_path = webm_queue.get(timeout=1)
            if shutdown_flag.is_set():
                webm_queue.put(video_path)  # Put it back
                break
            
            queue_stats['processed'] += 1
            remaining = queue_stats['total'] - queue_stats['processed']
            print(f"Worker {worker_id}: Processing {queue_stats['processed']}/{queue_stats['total']} ({remaining} remaining) - {os.path.basename(video_path)}")
            
            generate_scrub_webm(video_path)
            webm_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"Worker {worker_id} error: {e}")
    print(f"Worker {worker_id} shutting down")

# Start background workers
for i in range(MAX_WORKERS):
    worker_thread = threading.Thread(target=webm_worker, args=(i+1,))
    worker_thread.start()
    worker_threads.append(worker_thread)

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"\nReceived signal {signum}, initiating graceful shutdown...")
    shutdown_flag.set()
    
    # Clear the queue
    queue_size = webm_queue.qsize()
    if queue_size > 0:
        print(f"Clearing {queue_size} items from queue...")
        while not webm_queue.empty():
            try:
                webm_queue.get_nowait()
            except queue.Empty:
                break
    
    # Wait for workers to finish current tasks
    print("Waiting for workers to finish current tasks...")
    for thread in worker_threads:
        thread.join(timeout=30)
    
    print("Graceful shutdown complete")
    sys.exit(0)

# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def load_cache():
    """Load cached scan results"""
    if os.path.exists(app.config['CACHE_FILE']):
        with open(app.config['CACHE_FILE'], 'r') as f:
            cache = json.load(f)
            if time.time() - cache['timestamp'] < app.config['CACHE_DURATION']:
                return cache['data']
    return None

def save_cache(data):
    """Save scan results to cache"""
    cache = {
        'timestamp': time.time(),
        'data': data
    }
    with open(app.config['CACHE_FILE'], 'w') as f:
        json.dump(cache, f)

def load_tags():
    """Load video tags"""
    if os.path.exists(app.config['TAGS_FILE']):
        with open(app.config['TAGS_FILE'], 'r') as f:
            return json.load(f)
    return {}

def save_tags(tags):
    """Save video tags"""
    with open(app.config['TAGS_FILE'], 'w') as f:
        json.dump(tags, f)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/scan')
def scan():
    """API endpoint to scan for video files"""
    root_path = request.args.get('path', '/var/lib/jellyfin/metadata')
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'
    
    if not force_refresh:
        cached_data = load_cache()
        if cached_data:
            return jsonify(cached_data)
    
    if not os.path.exists(root_path):
        return jsonify({'error': 'Path does not exist'}), 400
    
    try:
        files = scan_video_files(root_path)
        print(f"Found {len(files)} video files in {root_path}")
        for f in files[:5]:  # Log first 5 files
            print(f"  {f['filename']} ({f['size']}) - {f['path']}")
        if len(files) > 5:
            print(f"  ... and {len(files) - 5} more files")
        
        # Queue webm generation for all videos
        queued_count = 0
        for f in files:
            video_path = f['full_path']
            video_dir = os.path.dirname(video_path)
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            webm_path = os.path.join(video_dir, f"{video_name}_scrub.webm")
            
            if not os.path.exists(webm_path) and video_path not in processing_status:
                webm_queue.put(video_path)
                processing_status[video_path] = 'queued'
                queued_count += 1
        
        if queued_count > 0:
            queue_stats['total'] += queued_count
            print(f"Queued {queued_count} videos for processing")
        
        save_cache(files)
        return jsonify(files)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files')
def get_files():
    """Get paginated file list"""
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    tag_filter = request.args.get('tag')
    
    cached_data = load_cache() or []
    tags = load_tags()
    
    # Add tags to files and filter
    filtered_files = []
    for f in cached_data:
        file_tags = tags.get(f['full_path'], [])
        if not file_tags:
            file_tags = ['untagged']
        f['tags'] = file_tags
        
        if not tag_filter or tag_filter in file_tags:
            filtered_files.append(f)
    
    start = (page - 1) * per_page
    end = start + per_page
    
    return jsonify({
        'files': filtered_files[start:end],
        'total': len(filtered_files),
        'page': page,
        'per_page': per_page,
        'total_pages': (len(filtered_files) + per_page - 1) // per_page if filtered_files else 0
    })

@app.route('/api/video', methods=['GET'])
def serve_video():
    """Generate and serve scrub webm from video file"""
    import urllib.parse
    video_path = request.args.get('path')
    if not video_path:
        return jsonify({'error': 'No path provided'}), 400
    
    # URL decode the path
    video_path = urllib.parse.unquote(video_path)
    print(f"Video request for: {video_path}")
    
    try:
        video_dir = os.path.dirname(video_path)
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        webm_path = os.path.join(video_dir, f"{video_name}_scrub.webm")
        
        if os.path.exists(webm_path):
            return send_file(webm_path, mimetype='video/webm')
        
        # Check processing status
        status = processing_status.get(video_path, 'not_started')
        if status == 'queued':
            return jsonify({'error': 'Video queued for processing'}), 202
        elif status == 'processing':
            return jsonify({'error': 'Video being processed'}), 202
        elif status == 'error':
            return jsonify({'error': 'Error processing video'}), 500
        else:
            # Start processing immediately for single requests
            webm_path = generate_scrub_webm(video_path)
            if webm_path and os.path.exists(webm_path):
                return send_file(webm_path, mimetype='video/webm')
            else:
                return jsonify({'error': 'Could not generate video'}), 404
    except Exception as e:
        print(f"Error serving video: {e}")
        return jsonify({'error': str(e)}), 404

@app.route('/api/tags')
def get_all_tags():
    """Get all existing tags"""
    tags = load_tags()
    all_tags = set()
    for file_tags in tags.values():
        all_tags.update(file_tags)
    all_tags.discard('untagged')
    return jsonify(sorted(list(all_tags)))

@app.route('/api/queue')
def queue_status():
    """Get render queue status"""
    return jsonify({
        'queue_size': webm_queue.qsize(),
        'processed': queue_stats['processed'],
        'total': queue_stats['total'],
        'remaining': queue_stats['total'] - queue_stats['processed'],
        'processing_status': {k: v for k, v in processing_status.items() if v in ['processing', 'queued']}
    })

@app.route('/api/queue/clear', methods=['POST'])
def clear_queue():
    """Clear the processing queue"""
    try:
        # Clear the queue
        queue_size = webm_queue.qsize()
        while not webm_queue.empty():
            try:
                webm_queue.get_nowait()
            except queue.Empty:
                break
        
        # Reset processing status
        processing_status.clear()
        queue_stats['processed'] = 0
        queue_stats['total'] = 0
        
        return jsonify({'success': True, 'cleared': queue_size})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/queue/restart', methods=['POST'])
def restart_queue():
    """Restart stuck queue processing"""
    try:
        # Reset any stuck 'processing' items back to 'queued'
        stuck_items = []
        for video_path, status in processing_status.items():
            if status == 'processing':
                processing_status[video_path] = 'queued'
                webm_queue.put(video_path)
                stuck_items.append(video_path)
        
        return jsonify({'success': True, 'requeued': len(stuck_items), 'items': stuck_items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/tag', methods=['POST'])
def tag_file():
    """Add/remove tags for a video file"""
    try:
        print(f"Tag request received: {request.json}")
        filepath = request.json.get('filepath')
        tag = request.json.get('tag')
        action = request.json.get('action', 'add')
        
        print(f"Processing tag: filepath={filepath}, tag={tag}, action={action}")
        
        if not filepath or not tag:
            return jsonify({'error': 'Missing filepath or tag'}), 400
        
        tags = load_tags()
        file_tags = tags.get(filepath, [])
        
        if action == 'add' and tag not in file_tags:
            file_tags.append(tag)
            if 'untagged' in file_tags:
                file_tags.remove('untagged')
        elif action == 'remove' and tag in file_tags:
            file_tags.remove(tag)
            if not file_tags:
                file_tags = ['untagged']
        
        tags[filepath] = file_tags
        save_tags(tags)
        print(f"Tag operation completed: {file_tags}")
        return jsonify({'tags': file_tags})
    except Exception as e:
        print(f"Error in tag_file: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/delete', methods=['POST'])
def delete_file():
    """Delete a video file"""
    filepath = request.json.get('filepath')
    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    
    try:
        os.remove(filepath)
        # Also remove scrub file if it exists
        video_dir = os.path.dirname(filepath)
        video_name = os.path.splitext(os.path.basename(filepath))[0]
        scrub_path = os.path.join(video_dir, f"{video_name}_scrub.webm")
        if os.path.exists(scrub_path):
            os.remove(scrub_path)
        
        # Clear cache to force refresh
        if os.path.exists(app.config['CACHE_FILE']):
            os.remove(app.config['CACHE_FILE'])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    try:
        app.run(debug=True, host='0.0.0.0', port=5003, use_reloader=False)
    except KeyboardInterrupt:
        signal_handler(signal.SIGINT, None)