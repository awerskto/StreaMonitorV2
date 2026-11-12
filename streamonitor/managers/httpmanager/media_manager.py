"""Post-recording media pipeline.

After a recording finishes, `enqueue(folder)` is called. It scans the folder
for files that are not in the database yet and hands them to a worker pool.
The Bot thread never waits for any of this.

Guarantees:
  - the Bot thread is never blocked (enqueue only submits work)
  - a file that is still being written is not processed (it is still growing)
  - the same file is never processed twice at the same time
  - a failing job is retried at most twice
  - artifacts are written to a .part file and renamed into place
"""
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from parameters import (CACHE_DIR, CACHE_SWEEP_MIN, POST_RECORD_CMD, PROXY_ENABLED,
                        SCRUB_ENABLED, THUMB_WORKERS, THUMBS_ENABLED)

from . import db
from .media_hooks import HOOKS

# The containers that are treated as recordings. Everything else in a
# download folder (the local playlist cache, the logs) is a helper file.
VIDEO_EXTENSIONS = ('mp4', 'mkv', 'webm', 'mov', 'avi', 'wmv', 'ts')

# A file younger than this is considered still being written.
STABLE_AFTER = 5.0
# How often watched folders are polled for newly finished recordings.
WATCH_INTERVAL = 15.0
MAX_RETRIES = 2
# How many recordings the backfill may queue at the start. The queue is
# shared with the fresh recordings, they must not wait for the whole
# history.
BACKFILL_LIMIT = 50
# How many missing artifacts one sweep may rebuild.
REBUILD_LIMIT = 20


class Job:
    """One recording waiting to be processed."""

    def __init__(self, rel_path, abs_path, username, site, size, mtime, duration=None, log=None,
                 backfill=False, display_w=None, display_h=None):
        self.rel_path = rel_path
        self.abs_path = abs_path
        self.username = username
        self.site = site
        self.size = size
        self.mtime = mtime
        self.duration = duration
        self.log = log
        # A job of the backfill is not a new recording, so the command of the
        # user must not run for it.
        self.backfill = backfill
        # What the player should show, set by the ffprobe hook.
        self.display_w = display_w
        self.display_h = display_h

    def __repr__(self):
        return f"<Job {self.rel_path}>"


class MediaManager:
    """Queue and worker pool for the media pipeline."""

    def __init__(self, hooks=None, workers=None):
        self.hooks = list(HOOKS if hooks is None else hooks)
        self.workers = max(1, THUMB_WORKERS if workers is None else workers)
        self._executor = None
        self._pending = set()
        self._watched = {}          # folder -> (username, site)
        self._lock = threading.Lock()
        self._started = False
        self._watcher = None

    # --- lifecycle --------------------------------------------------------

    def start(self):
        with self._lock:
            if self._started:
                return
            self._executor = ThreadPoolExecutor(max_workers=self.workers,
                                                 thread_name_prefix='media')
            self._started = True
        self._start_watcher()

    def _start_watcher(self):
        """Poll the watched folders so that segments finished while a long
        recording is still running also get their artifacts."""
        if self._watcher is not None:
            return

        def loop():
            while True:
                time.sleep(WATCH_INTERVAL)
                try:
                    self.poll()
                except Exception:
                    pass

        self._watcher = threading.Thread(target=loop, name='media-watcher', daemon=True)
        self._watcher.start()

    def shutdown(self):
        with self._lock:
            if self._executor is not None:
                self._executor.shutdown(wait=False)
                self._executor = None
            self._started = False

    # --- queueing ---------------------------------------------------------

    def enqueue(self, folder, username=None, site=None, log=None):
        """Queue the finished recordings of a folder. Never blocks.

        The caller knows the recording just ended, so the files are complete
        and the stability check is not applied.
        """
        try:
            self.start()
            if folder:
                with self._lock:
                    self._watched[os.path.abspath(folder)] = (username, site)
            jobs = self.scan(folder, username, site, log, require_stable=False)
        except Exception as e:
            if log:
                log.warning(f"media pipeline enqueue failed: {e}")
            return
        self._submit(jobs)

    def poll(self):
        """Look at every watched folder and queue what appeared since.

        This runs while a recording may still be going on, so files that are
        still being written are skipped.
        """
        with self._lock:
            watched = list(self._watched.items())
        for folder, (username, site) in watched:
            try:
                jobs = self.scan(folder, username, site, None, require_stable=True)
            except Exception:
                continue
            self._submit(jobs)

    def _submit(self, jobs):
        if not jobs:
            return
        self.start()
        for job in jobs:
            with self._lock:
                if job.rel_path in self._pending:
                    continue
                self._pending.add(job.rel_path)
            self._executor.submit(self._run_job, job)

    def scan(self, folder, username=None, site=None, log=None, require_stable=True,
             backfill=False, limit=None):
        """Find recordings in `folder` that are not in the database yet."""
        jobs = []
        if not folder or not os.path.isdir(folder):
            return jobs

        rel_folder = self._relative_folder(folder)
        now = time.time()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return jobs

        for entry in entries:
            if limit is not None and len(jobs) >= limit:
                break
            if not entry.is_file():
                continue
            if os.path.splitext(entry.name)[1][1:].lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            # Skip files that are still being written.
            if require_stable and now - stat.st_mtime < STABLE_AFTER:
                continue

            rel_path = os.path.join(rel_folder, entry.name) if rel_folder else entry.name
            row = db.query_one("SELECT size, mtime FROM media WHERE path = ?", (rel_path,))
            if row is not None and row['size'] == stat.st_size and row['mtime'] == stat.st_mtime:
                continue

            duration = None
            if row is not None:
                media_row = db.query_one("SELECT duration FROM media WHERE path = ?", (rel_path,))
                duration = media_row['duration'] if media_row else None

            jobs.append(Job(rel_path, entry.path, username or '', site or '',
                             stat.st_size, stat.st_mtime, duration, log, backfill=backfill))
        return jobs

    def _relative_folder(self, folder):
        from parameters import DOWNLOADS_DIR
        base = os.path.realpath(DOWNLOADS_DIR)
        real = os.path.realpath(folder)
        if real == base:
            return ''
        if real.startswith(base + os.sep):
            return os.path.relpath(real, base)
        return os.path.basename(real)

    # --- execution --------------------------------------------------------

    def _run_job(self, job):
        try:
            for attempt in range(MAX_RETRIES + 1):
                try:
                    self._process(job)
                    break
                except Exception as e:
                    if job.log:
                        job.log.warning(f"media hook error for {job.rel_path}: {e}")
                    if attempt >= MAX_RETRIES:
                        break
                    time.sleep(1 + attempt)
        finally:
            with self._lock:
                self._pending.discard(job.rel_path)

    def _process(self, job):
        # The recording may have been deleted while the job was waiting.
        if not os.path.isfile(job.abs_path):
            return

        # Make sure the row exists so the other hooks can read the duration.
        db.upsert_media(job.rel_path, job.username, job.site, job.size, job.mtime,
                        duration=job.duration)
        for hook in self.hooks:
            # The command of the user belongs to a new recording. The
            # backfill walks through the whole history, it must not run a
            # script for every recording of the last years.
            if job.backfill and getattr(hook, '__name__', '') == 'hook_post_record_cmd':
                continue
            try:
                hook(job)
            except Exception as e:
                if job.log:
                    job.log.warning(f"media hook {getattr(hook, '__name__', hook)} failed "
                                    f"for {job.rel_path}: {e}")

    # --- housekeeping -----------------------------------------------------

    def backfill(self, log=None, limit=None):
        """Queue the recordings that have no metadata yet.

        Recordings made before the media pipeline existed (or before it was
        able to run) are unknown to the database, so they would never get a
        thumbnail. The folders are named "username [SITE]", which gives the
        owner of the recording.

        The number of recordings is limited: the queue is shared with the
        fresh recordings and they must not wait for the whole history.
        """
        from parameters import DOWNLOADS_DIR
        base = os.path.abspath(DOWNLOADS_DIR)
        if not os.path.isdir(base):
            return 0
        if limit is None:
            limit = BACKFILL_LIMIT
        queued = 0
        try:
            entries = sorted(os.scandir(base), key=lambda e: e.name.lower())
        except OSError:
            return 0
        for entry in entries:
            if queued >= limit:
                break
            if not entry.is_dir() or entry.name.startswith('.'):
                continue
            match = re.match(r'^(?P<user>.+?)\s*\[(?P<site>[^\]]+)\]$', entry.name)
            if match:
                username = match.group('user')
                site = match.group('site')
            else:
                username = entry.name
                site = ''
            jobs = self.scan(entry.path, username, site, log, require_stable=True,
                             backfill=True, limit=limit - queued)
            if jobs:
                self._submit(jobs)
                queued += len(jobs)
        if queued and log:
            log.info(f"media backfill: queued {queued} recordings")
        return queued

    def _missing_artifacts(self, limit=None):
        """The recordings whose artifacts are missing.

        A hook can fail: no codec, no space, a timeout. The row of the
        recording is written before the hooks run, so a failed hook would
        leave the recording without a thumbnail forever. These recordings
        are queued again.
        """
        wanted = []
        if THUMBS_ENABLED:
            wanted.append('poster')
        if SCRUB_ENABLED:
            wanted.append('sprite')
        if PROXY_ENABLED:
            wanted.append('proxy')
        if not wanted:
            return []

        missing = []
        try:
            rows = db.query("SELECT path, username, site, size, mtime, duration FROM media")
        except Exception:
            return []
        for row in rows:
            if limit is not None and len(missing) >= limit:
                break
            try:
                present = {a['kind'] for a in db.list_artifacts(row['path'])}
            except Exception:
                continue
            if all(kind in present for kind in wanted):
                continue
            missing.append(row)
        return missing

    def _queue_missing_artifacts(self, log=None, limit=None):
        """Build the artifacts that the pipeline could not create."""
        from parameters import DOWNLOADS_DIR
        rows = self._missing_artifacts(limit=limit)
        if not rows:
            return 0
        jobs = []
        for row in rows:
            try:
                abs_path = os.path.join(DOWNLOADS_DIR, row['path'])
                if not os.path.isfile(abs_path):
                    continue
                stat = os.stat(abs_path)
            except OSError:
                continue
            jobs.append(Job(row['path'], abs_path, row['username'], row['site'],
                             stat.st_size, stat.st_mtime, row['duration'], log))
        if jobs:
            self._submit(jobs)
            if log:
                log.info(f"media pipeline: rebuilding {len(jobs)} artifacts")
        return len(jobs)

    def sweep(self, log=None):
        """Remove database rows and artifacts whose source file is gone.

        Handles three cases:
          1. the recording was deleted -> drop its artifacts (files and rows)
          2. the recording changed   -> the artifact is stale, drop it
          3. the artifact file is gone or orphaned -> drop the row
        """
        removed = 0
        try:
            db.init()

            # 1. Recordings whose file disappeared.
            for row in db.query("SELECT path FROM media"):
                if self._source_exists(row['path']):
                    continue
                removed += self._drop_media(row['path'])
                removed += self._drop_artifacts(row['path'])

            # 2 and 3. Artifacts of recordings that still exist.
            for row in db.list_artifacts():
                media_row = db.query_one("SELECT size, mtime FROM media WHERE path = ?",
                                         (row['path'],))
                if media_row is None:
                    # Orphan: no recording left.
                    self._unlink(db.artifact_path(row))
                    db.delete_artifact(row['path'], row['kind'])
                    removed += 1
                    continue
                if not os.path.exists(db.artifact_path(row)):
                    db.delete_artifact(row['path'], row['kind'])
                    removed += 1
                    continue
                if (media_row['size'] != row['src_size']
                        or media_row['mtime'] != row['src_mtime']):
                    # The recording changed, so the artifact is stale.
                    self._unlink(db.artifact_path(row))
                    db.delete_artifact(row['path'], row['kind'])
                    removed += 1

            self._remove_empty_dirs()
            removed += self._drop_disabled_artifacts(log)

            # 4. Recordings whose artifacts the pipeline could not build.
            self._queue_missing_artifacts(log, limit=REBUILD_LIMIT)
        except Exception as e:
            if log:
                log.warning(f"media sweep failed: {e}")
        return removed

    def _drop_disabled_artifacts(self, log=None):
        """Remove the artifacts of features that are switched off.

        The animated hover preview is gone, so its files are deleted. When
        the scrub is off the storyboards have no meaning either.
        """
        removable = {'preview'}
        if not SCRUB_ENABLED:
            removable.add('sprite')
        removed = 0
        for row in db.list_artifacts():
            if row['kind'] not in removable:
                continue
            self._unlink(db.artifact_path(row))
            db.delete_artifact(row['path'], row['kind'])
            removed += 1
        return removed

    @staticmethod
    def _unlink(path):
        try:
            os.unlink(path)
        except OSError:
            pass

    def _drop_media(self, rel_path):
        """Remove a recording row and its favourite entry."""
        db.execute("DELETE FROM media WHERE path = ?", (rel_path,))
        db.remove_favorite(rel_path)
        return 1

    def _drop_artifacts(self, rel_path):
        """Remove the artifact files and rows of a recording."""
        count = 0
        for row in db.list_artifacts(rel_path):
            self._unlink(db.artifact_path(row))
            db.delete_artifact(row['path'], row['kind'])
            count += 1
        return count

    def _source_exists(self, rel_path):
        from parameters import DOWNLOADS_DIR
        return os.path.exists(os.path.join(DOWNLOADS_DIR, rel_path))

    def _remove_empty_dirs(self):
        for root, dirs, files in os.walk(CACHE_DIR, topdown=False):
            if root == os.path.realpath(CACHE_DIR):
                continue
            if not dirs and not files:
                try:
                    os.rmdir(root)
                except OSError:
                    pass


_manager = None
_manager_lock = threading.Lock()


def get_manager():
    """The shared media manager instance."""
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = MediaManager()
        return _manager


def enqueue(folder, username=None, site=None, log=None):
    """Queue a folder for processing. Safe to call from the Bot thread."""
    get_manager().enqueue(folder, username, site, log)


def start_sweeper(log=None, interval_minutes=None):
    """Run the cache sweep periodically and once at startup."""
    interval = (interval_minutes if interval_minutes is not None else CACHE_SWEEP_MIN) * 60
    if interval <= 0:
        return None

    def loop():
        manager = get_manager()
        try:
            db.init()
        except Exception:
            pass
        # Recordings that the pipeline never saw still need their artifacts.
        try:
            manager.backfill(log)
        except Exception as e:
            if log:
                log.warning(f"media backfill failed: {e}")
        while True:
            try:
                manager.sweep(log)
            except Exception as e:
                if log:
                    log.warning(f"media sweep failed: {e}")
            time.sleep(interval)

    thread = threading.Thread(target=loop, name='media-sweeper', daemon=True)
    thread.start()
    return thread
