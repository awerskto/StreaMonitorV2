"""Persistent metadata for the media library.

SQLite (stdlib) in WAL mode, stored next to the recordings so it travels with
the same volume. The database holds:

  media      - one row per recording file (size, mtime, ffprobe metadata)
  artifacts  - generated files (poster, preview, proxy) plus the source
               size/mtime they were built from, which is the cache key
  favorites  - starred recordings, keyed by their relative path

Every Bot thread writes here, so connections are per thread and transactions
are kept short. WAL lets readers work while a writer is active.
"""
import os
import sqlite3
import threading
import time

from parameters import CACHE_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    path       TEXT PRIMARY KEY,
    username   TEXT NOT NULL,
    site       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    mtime      REAL NOT NULL,
    duration   REAL,
    width      INTEGER,
    height     INTEGER,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_media_user_site ON media(username, site);
CREATE INDEX IF NOT EXISTS idx_media_mtime ON media(mtime);

CREATE TABLE IF NOT EXISTS artifacts (
    path       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    file       TEXT NOT NULL,
    size       INTEGER,
    mtime      REAL,
    src_size   INTEGER,
    src_mtime  REAL,
    created_at REAL NOT NULL,
    PRIMARY KEY (path, kind)
);

CREATE TABLE IF NOT EXISTS favorites (
    path     TEXT PRIMARY KEY,
    added_at REAL NOT NULL
);
"""

_local = threading.local()
_db_path = None


def db_path():
    global _db_path
    if _db_path is None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        _db_path = os.path.join(CACHE_DIR, 'index.sqlite')
    return _db_path


def connection():
    """Per-thread connection with WAL enabled."""
    conn = getattr(_local, 'conn', None)
    if conn is None:
        conn = sqlite3.connect(db_path(), timeout=10, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


# The version of the schema. It is written with PRAGMA user_version, so the
# migration does not have to run at every start.
SCHEMA_VERSION = 3

# The columns the migrations add to the media table.
_MEDIA_COLUMNS = [
    ('sprite_cols', 'INTEGER'),
    ('sprite_rows', 'INTEGER'),
    ('cell_w', 'INTEGER'),
    ('cell_h', 'INTEGER'),
    ('frames', 'INTEGER'),
    ('display_w', 'INTEGER'),
    ('display_h', 'INTEGER'),
]


def init():
    """Create the schema if needed. Safe to call from several threads."""
    conn = connection()
    with conn:
        conn.executescript(SCHEMA)
        # Columns that a later version added. ALTER TABLE has no "IF NOT
        # EXISTS", so the existing columns are asked for.
        present = {row['name'] for row in conn.execute('PRAGMA table_info(media)')}
        for name, kind in _MEDIA_COLUMNS:
            if name not in present:
                conn.execute(f'ALTER TABLE media ADD COLUMN {name} {kind}')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version < SCHEMA_VERSION:
        with conn:
            conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')
        migrate_artifact_paths()


def query(sql, params=()):
    return connection().execute(sql, params).fetchall()


def query_one(sql, params=()):
    return connection().execute(sql, params).fetchone()


def execute(sql, params=()):
    conn = connection()
    with conn:
        return conn.execute(sql, params)


def close():
    conn = getattr(_local, 'conn', None)
    if conn is not None:
        conn.close()
        _local.conn = None


# --- media ---------------------------------------------------------------

def upsert_media(path, username, site, size, mtime, duration=None, width=None, height=None,
                 display_w=None, display_h=None):
    """Insert or refresh a recording's row. Returns True when it is new.

    The metadata columns are always refreshed when a value is given, so that
    ffprobe results are stored even when the file itself did not change.
    """
    now = time.time()
    conn = connection()
    with conn:
        row = conn.execute("SELECT path FROM media WHERE path = ?", (path,)).fetchone()
        exists = row is not None
        conn.execute("""
            INSERT INTO media (path, username, site, size, mtime, duration, width, height,
                               display_w, display_h, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                size = excluded.size,
                mtime = excluded.mtime,
                duration = COALESCE(excluded.duration, media.duration),
                width = COALESCE(excluded.width, media.width),
                height = COALESCE(excluded.height, media.height),
                display_w = COALESCE(excluded.display_w, media.display_w),
                display_h = COALESCE(excluded.display_h, media.display_h)
        """, (path, username, site, size, mtime, duration, width, height,
              display_w, display_h, now))
    return not exists


def delete_media(path):
    execute("DELETE FROM media WHERE path = ?", (path,))
    execute("DELETE FROM artifacts WHERE path = ?", (path,))
    execute("DELETE FROM favorites WHERE path = ?", (path,))


def list_media(username=None, site=None):
    if username is None:
        return query("SELECT * FROM media ORDER BY mtime DESC")
    return query(
        "SELECT * FROM media WHERE username = ? AND site = ? ORDER BY mtime DESC",
        (username, site))


def all_folders():
    """One row per (username, site) with aggregate counts."""
    return query("""
        SELECT username, site,
               COUNT(*) AS count,
               SUM(size) AS total_size,
               MAX(mtime) AS last_activity,
               MAX(created_at) AS last_created
        FROM media
        GROUP BY username, site
        ORDER BY last_activity DESC
    """)


# --- artifacts -----------------------------------------------------------

# Suffix of the artifact file per kind. The artifacts mirror the layout of the
# recordings, so their location is derived from the recording path.
ARTIFACT_SUFFIX = {
    'poster': '.jpg',
    'sprite': '.sprite.webp',
    'proxy': '.proxy.mp4',
}


def artifact_relpath(path, kind):
    """Where the artifact of a recording lives, relative to the cache dir."""
    suffix = ARTIFACT_SUFFIX.get(kind, '.bin')
    return f'{path}{suffix}'


def record_artifact(path, kind, file, size, mtime, src_size, src_mtime):
    """Store an artifact. The location is kept relative to the cache directory,
    so that the database survives a move of the downloads folder or a different
    mount point inside a container."""
    execute("""
        INSERT INTO artifacts (path, kind, file, size, mtime, src_size, src_mtime, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path, kind) DO UPDATE SET
            file = excluded.file,
            size = excluded.size,
            mtime = excluded.mtime,
            src_size = excluded.src_size,
            src_mtime = excluded.src_mtime,
            created_at = excluded.created_at
    """, (path, kind, artifact_relpath(path, kind), size, mtime, src_size, src_mtime, time.time()))


def migrate_artifact_paths():
    """Rewrite absolute artifact paths as relative ones.

    Older databases stored absolute paths, which break when the downloads
    folder is moved or mounted somewhere else. The new location is derived from
    the recording path, so it is correct even when the old folder is gone.
    """
    changed = 0
    for row in query("SELECT path, kind, file FROM artifacts"):
        if os.path.isabs(row['file']):
            execute("UPDATE artifacts SET file = ? WHERE path = ? AND kind = ?",
                    (artifact_relpath(row['path'], row['kind']), row['path'], row['kind']))
            changed += 1
    return changed


def artifact_path(row_or_file):
    """Absolute path of an artifact row (or of a stored relative path).

    The path is made absolute because Flask resolves a relative path against
    the application root, not the working directory.
    """
    file = row_or_file['file'] if isinstance(row_or_file, (sqlite3.Row, dict)) else row_or_file
    if os.path.isabs(file):
        return file
    return os.path.abspath(os.path.join(CACHE_DIR, file))


def get_artifact(path, kind):
    return query_one("SELECT * FROM artifacts WHERE path = ? AND kind = ?", (path, kind))


def list_artifacts(path=None):
    if path is None:
        return query("SELECT * FROM artifacts")
    return query("SELECT * FROM artifacts WHERE path = ?", (path,))


def delete_artifact(path, kind):
    execute("DELETE FROM artifacts WHERE path = ? AND kind = ?", (path, kind))


def artifact_is_stale(path, kind, src_size, src_mtime):
    """True when the artifact was built from a different version of the file."""
    row = get_artifact(path, kind)
    if row is None:
        return True
    return row['src_size'] != src_size or row['src_mtime'] != src_mtime


# --- favorites -----------------------------------------------------------

def toggle_favorite(path):
    """Star or unstar a recording. Returns the new state."""
    conn = connection()
    with conn:
        row = conn.execute("SELECT path FROM favorites WHERE path = ?", (path,)).fetchone()
        if row:
            conn.execute("DELETE FROM favorites WHERE path = ?", (path,))
            return False
        conn.execute("INSERT INTO favorites (path, added_at) VALUES (?, ?)", (path, time.time()))
        return True


def is_favorite(path):
    return query_one("SELECT path FROM favorites WHERE path = ?", (path,)) is not None


def list_favorites():
    return query("SELECT path FROM favorites ORDER BY added_at DESC")


def remove_favorite(path):
    execute("DELETE FROM favorites WHERE path = ?", (path,))
