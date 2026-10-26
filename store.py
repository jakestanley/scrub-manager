"""SQLite persistence for videos, their scrub status, and tags."""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

import config

DB_PATH = os.path.join(config.DATA_DIR, 'trickplay.db')
LEGACY_TAGS_FILE = os.path.join(config.DATA_DIR, 'video_tags.json')

# status values:
#   pending    - waiting to be generated
#   processing - a worker is generating it
#   ready      - scrub exists in SCRUB_DIR
#   error      - last attempt failed, will be retried after next_attempt_at
#   failed     - gave up after MAX_ATTEMPTS
SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    path TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    directory TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tags (
    path TEXT NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY (path, tag)
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

SORTS = {
    'size_desc': 'size_bytes DESC',
    'size_asc': 'size_bytes ASC',
    'name_asc': 'filename COLLATE NOCASE ASC',
    'name_desc': 'filename COLLATE NOCASE DESC',
}

_claim_lock = threading.Lock()


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with connect() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript(SCHEMA)
        # Anything mid-generation when we last stopped needs doing again
        conn.execute("UPDATE videos SET status = 'pending' WHERE status = 'processing'")
    _migrate_legacy_tags()


def _migrate_legacy_tags():
    """Import tags from the old video_tags.json once."""
    with connect() as conn:
        if conn.execute("SELECT 1 FROM meta WHERE key = 'legacy_tags_migrated'").fetchone():
            return
        if os.path.exists(LEGACY_TAGS_FILE):
            with open(LEGACY_TAGS_FILE) as f:
                legacy = json.load(f)
            rows = [(path, tag) for path, tags in legacy.items() for tag in tags if tag != 'untagged']
            conn.executemany('INSERT OR IGNORE INTO tags (path, tag) VALUES (?, ?)', rows)
            print(f"Migrated {len(rows)} tags from {LEGACY_TAGS_FILE}")
        conn.execute("INSERT INTO meta (key, value) VALUES ('legacy_tags_migrated', ?)", (str(time.time()),))


def sync_scanned(found):
    """Record scan results. `found` is a list of dicts with path/filename/directory/size_bytes/mtime.

    New or changed videos are (re)queued. Returns (added, changed).
    """
    now = time.time()
    added = changed = 0
    with connect() as conn:
        existing = {row['path']: row for row in conn.execute('SELECT path, size_bytes, mtime FROM videos')}
        for v in found:
            row = existing.get(v['path'])
            if row is None:
                conn.execute(
                    'INSERT INTO videos (path, filename, directory, size_bytes, mtime, updated_at) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (v['path'], v['filename'], v['directory'], v['size_bytes'], v['mtime'], now))
                added += 1
            elif row['size_bytes'] != v['size_bytes'] or row['mtime'] != v['mtime']:
                conn.execute(
                    "UPDATE videos SET size_bytes = ?, mtime = ?, status = 'pending', source = NULL, "
                    'attempts = 0, last_error = NULL, next_attempt_at = 0, updated_at = ? WHERE path = ?',
                    (v['size_bytes'], v['mtime'], now, v['path']))
                changed += 1
    return added, changed


def remove_missing(found_paths):
    """Forget videos that are no longer on disk. Returns the removed paths."""
    with connect() as conn:
        known = [row['path'] for row in conn.execute('SELECT path FROM videos')]
        missing = [p for p in known if p not in found_paths]
        for path in missing:
            remove_video(path, conn)
    return missing


def remove_video(path, conn=None):
    if conn is None:
        with connect() as conn:
            return remove_video(path, conn)
    conn.execute('DELETE FROM videos WHERE path = ?', (path,))
    conn.execute('DELETE FROM tags WHERE path = ?', (path,))


def claim_next():
    """Mark the next video needing a scrub as processing and return its path (or None)."""
    with _claim_lock, connect() as conn:
        row = conn.execute(
            "SELECT path FROM videos WHERE status = 'pending' "
            "OR (status = 'error' AND next_attempt_at <= ?) "
            'ORDER BY attempts ASC, size_bytes DESC LIMIT 1',
            (time.time(),)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE videos SET status = 'processing', updated_at = ? WHERE path = ?",
                     (time.time(), row['path']))
        return row['path']


def mark_ready(path, source):
    with connect() as conn:
        conn.execute(
            "UPDATE videos SET status = 'ready', source = ?, last_error = NULL, updated_at = ? WHERE path = ?",
            (source, time.time(), path))


def mark_error(path, message):
    with connect() as conn:
        row = conn.execute('SELECT attempts FROM videos WHERE path = ?', (path,)).fetchone()
        if row is None:
            return
        attempts = row['attempts'] + 1
        status = 'failed' if attempts >= config.MAX_ATTEMPTS else 'error'
        # Back off: 5 min, 10 min, 20 min, ...
        next_attempt_at = time.time() + 300 * 2 ** (attempts - 1)
        conn.execute(
            'UPDATE videos SET status = ?, attempts = ?, last_error = ?, next_attempt_at = ?, updated_at = ? '
            'WHERE path = ?',
            (status, attempts, message[-1000:], next_attempt_at, time.time(), path))


def mark_pending(path):
    with connect() as conn:
        conn.execute("UPDATE videos SET status = 'pending', updated_at = ? WHERE path = ?", (time.time(), path))


def retry_failed():
    with connect() as conn:
        cur = conn.execute(
            "UPDATE videos SET status = 'pending', attempts = 0, next_attempt_at = 0, updated_at = ? "
            "WHERE status IN ('error', 'failed')",
            (time.time(),))
        return cur.rowcount


def ready_videos():
    with connect() as conn:
        return [dict(row) for row in conn.execute("SELECT path, source FROM videos WHERE status = 'ready'")]


def get_video(path):
    with connect() as conn:
        row = conn.execute('SELECT * FROM videos WHERE path = ?', (path,)).fetchone()
        return dict(row) if row else None


def list_videos(page, per_page, sort, name_filter=None, tag_filter=None):
    where = []
    params = []
    if name_filter:
        where.append('filename LIKE ? COLLATE NOCASE')
        params.append(f'%{name_filter}%')
    if tag_filter == 'untagged':
        where.append('path NOT IN (SELECT path FROM tags)')
    elif tag_filter:
        where.append('path IN (SELECT path FROM tags WHERE tag = ?)')
        params.append(tag_filter)
    where_sql = f"WHERE {' AND '.join(where)}" if where else ''
    order_sql = SORTS.get(sort, SORTS['size_desc'])

    with connect() as conn:
        total = conn.execute(f'SELECT COUNT(*) FROM videos {where_sql}', params).fetchone()[0]
        rows = conn.execute(
            f'SELECT * FROM videos {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?',
            params + [per_page, (page - 1) * per_page]).fetchall()
        videos = [dict(row) for row in rows]
        for v in videos:
            v['tags'] = [r['tag'] for r in conn.execute(
                'SELECT tag FROM tags WHERE path = ? ORDER BY tag', (v['path'],))]
    return videos, total


def status_counts():
    with connect() as conn:
        return {row['status']: row['n'] for row in
                conn.execute('SELECT status, COUNT(*) AS n FROM videos GROUP BY status')}


def all_tags():
    with connect() as conn:
        return [row['tag'] for row in conn.execute('SELECT DISTINCT tag FROM tags ORDER BY tag')]


def add_tag(path, tag):
    with connect() as conn:
        conn.execute('INSERT OR IGNORE INTO tags (path, tag) VALUES (?, ?)', (path, tag))


def remove_tag(path, tag):
    with connect() as conn:
        conn.execute('DELETE FROM tags WHERE path = ? AND tag = ?', (path, tag))


def tags_for(path):
    with connect() as conn:
        return [row['tag'] for row in conn.execute('SELECT tag FROM tags WHERE path = ? ORDER BY tag', (path,))]
