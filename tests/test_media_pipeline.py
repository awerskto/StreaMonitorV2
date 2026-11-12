"""Tests for the media pipeline: queueing, hooks and the cache sweep.

ffmpeg is used for the real artifact generation. When it is not available the
artifact tests are skipped, but the queueing logic is always covered.
"""
import os
import shutil
import time

import pytest

from parameters import FFMPEG_PATH
from streamonitor.managers.httpmanager import db, media_manager, media_hooks


def have_ffmpeg():
    return shutil.which(FFMPEG_PATH) is not None or os.path.exists(FFMPEG_PATH)


@pytest.fixture
def pipeline(workdir, monkeypatch):
    """A media manager wired to its own temporary directory."""
    # Own subdirectory, so recordings left by other tests cannot leak in.
    base = os.path.join(workdir, 'pipeline')
    downloads = os.path.join(base, 'downloads')
    cache = os.path.join(base, 'cache')
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(downloads, exist_ok=True)
    os.makedirs(cache, exist_ok=True)

    monkeypatch.setattr('parameters.DOWNLOADS_DIR', downloads)
    monkeypatch.setattr('parameters.CACHE_DIR', cache)
    monkeypatch.setattr(db, 'CACHE_DIR', cache)
    monkeypatch.setattr(media_manager, 'CACHE_DIR', cache)
    monkeypatch.setattr(media_manager, 'STABLE_AFTER', 0.0)
    monkeypatch.setattr(media_hooks, '_relative_cache_path',
                        lambda rel: os.path.join(cache, rel + '.cache'))

    db.close()
    for suffix in ('', '-wal', '-shm'):
        candidate = os.path.join(cache, 'index.sqlite' + suffix)
        if os.path.exists(candidate):
            os.unlink(candidate)
    db._db_path = None
    db._local = type(db._local)()
    db.init()

    manager = media_manager.MediaManager(hooks=[], workers=1)
    yield manager, downloads, cache
    manager.shutdown()


def make_recording(downloads, username='alpha', site='CB', name='alpha-20260101-120000.mp4'):
    folder = os.path.join(downloads, f'{username} [{site}]')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'wb') as handle:
        handle.write(b'0' * 4096)
    return folder, path


# --- queueing ------------------------------------------------------------

def test_enqueue_finds_new_recording(pipeline):
    manager, downloads, _ = pipeline
    folder, path = make_recording(downloads)

    jobs = manager.scan(folder, 'alpha', 'CB')
    assert len(jobs) == 1
    assert jobs[0].rel_path == os.path.join('alpha [CB]', os.path.basename(path))
    assert jobs[0].size == 4096


def test_enqueue_ignores_unknown_files(pipeline):
    """Only video files are picked up."""
    manager, downloads, _ = pipeline
    folder = os.path.join(downloads, 'alpha [CB]')
    os.makedirs(folder, exist_ok=True)
    for name in ('notes.txt', 'poster.jpg', 'thumbnail.png', 'data.json'):
        with open(os.path.join(folder, name), 'w') as handle:
            handle.write('hello')

    assert manager.scan(folder, 'alpha', 'CB') == []


def test_enqueue_ignores_subdirectories(pipeline):
    manager, downloads, _ = pipeline
    folder, _ = make_recording(downloads)
    os.makedirs(os.path.join(folder, 'subdir'), exist_ok=True)

    assert len(manager.scan(folder, 'alpha', 'CB')) == 1


def test_enqueue_skips_files_still_being_written(pipeline, monkeypatch):
    manager, downloads, _ = pipeline
    folder, path = make_recording(downloads)
    # A file written a moment ago is still being recorded.
    monkeypatch.setattr(media_manager, 'STABLE_AFTER', 3600.0)

    assert manager.scan(folder, 'alpha', 'CB') == []


def test_enqueue_skips_already_known_file(pipeline):
    manager, downloads, _ = pipeline
    folder, path = make_recording(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    assert manager.scan(folder, 'alpha', 'CB') == []


def test_enqueue_detects_changed_file(pipeline):
    manager, downloads, _ = pipeline
    folder, path = make_recording(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    # The file grew: it must be picked up again.
    with open(path, 'ab') as handle:
        handle.write(b'1' * 1024)

    jobs = manager.scan(folder, 'alpha', 'CB')
    assert len(jobs) == 1
    assert jobs[0].size > stat.st_size


def test_enqueue_does_not_block(pipeline):
    """enqueue must return immediately, even with a slow hook."""
    manager, downloads, _ = pipeline
    folder, _ = make_recording(downloads)

    def slow_hook(job):
        time.sleep(5)
        return True

    manager.hooks = [slow_hook]
    start = time.time()
    manager.enqueue(folder, 'alpha', 'CB')
    assert time.time() - start < 2.0


def test_enqueue_deduplicates(pipeline):
    manager, downloads, _ = pipeline
    folder, _ = make_recording(downloads)

    started = []
    manager.hooks = [lambda job: started.append(job.rel_path) or True]

    manager.enqueue(folder, 'alpha', 'CB')
    manager.enqueue(folder, 'alpha', 'CB')
    time.sleep(1.0)
    assert len(started) == 1


# --- hooks ---------------------------------------------------------------

@pytest.mark.skipif(not have_ffmpeg(), reason='ffmpeg not available')
def test_ffprobe_hook_reads_metadata(pipeline, sample_clip):
    manager, downloads, _ = pipeline
    folder = os.path.join(downloads, 'alpha [CB]')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, 'alpha-20260101-120000.mp4')
    shutil.copy(sample_clip, target)

    job = media_manager.Job('alpha [CB]/alpha-20260101-120000.mp4', target,
                            'alpha', 'CB', os.path.getsize(target),
                            os.path.getmtime(target), None, None)
    assert media_hooks.hook_ffprobe(job) is True

    row = db.query_one("SELECT * FROM media WHERE path = ?", (job.rel_path,))
    assert row is not None
    assert row['duration'] and row['duration'] > 0
    assert row['width'] == 320
    assert row['height'] == 240


@pytest.mark.skipif(not have_ffmpeg(), reason='ffmpeg not available')
def test_poster_hook_creates_jpeg(pipeline, sample_clip):
    manager, downloads, cache = pipeline
    folder = os.path.join(downloads, 'alpha [CB]')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, 'alpha-20260101-120000.mp4')
    shutil.copy(sample_clip, target)

    job = media_manager.Job('alpha [CB]/alpha-20260101-120000.mp4', target,
                            'alpha', 'CB', os.path.getsize(target),
                            os.path.getmtime(target), 3.0, None)
    assert media_hooks.hook_poster(job) is True

    artifact = db.get_artifact(job.rel_path, 'poster')
    assert artifact is not None
    # The path is stored relative to the cache directory, so the database
    # survives a move of the downloads folder.
    assert not os.path.isabs(artifact['file'])
    assert os.path.exists(db.artifact_path(artifact))
    assert artifact['file'].endswith('.jpg')
    assert artifact['src_size'] == job.size


@pytest.mark.skipif(not have_ffmpeg(), reason='ffmpeg not available')
def test_poster_hook_writes_atomically(pipeline, sample_clip):
    """No .part file may be left behind."""
    manager, downloads, cache = pipeline
    folder = os.path.join(downloads, 'alpha [CB]')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, 'alpha-20260101-120000.mp4')
    shutil.copy(sample_clip, target)

    job = media_manager.Job('alpha [CB]/alpha-20260101-120000.mp4', target,
                            'alpha', 'CB', os.path.getsize(target),
                            os.path.getmtime(target), 3.0, None)
    media_hooks.hook_poster(job)

    leftovers = [f for f in os.listdir(cache) if f.endswith('.part')]
    assert leftovers == []


@pytest.mark.skipif(not have_ffmpeg(), reason='ffmpeg not available')
def test_sprite_hook_creates_a_storyboard(pipeline, sample_clip, monkeypatch):
    monkeypatch.setattr(media_hooks, 'SCRUB_ENABLED', True)
    monkeypatch.setattr(media_hooks, 'THUMBS_ENABLED', True)
    manager, downloads, cache = pipeline
    folder = os.path.join(downloads, 'alpha [CB]')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, 'alpha-20260101-120000.mp4')
    shutil.copy(sample_clip, target)

    from streamonitor.managers.httpmanager.ffmpeg_utils import probe
    info = probe(target)
    job = media_manager.Job('alpha [CB]/alpha-20260101-120000.mp4', target,
                            'alpha', 'CB', os.path.getsize(target),
                            os.path.getmtime(target), info['duration'], None,
                            display_w=info.get('display_w'),
                            display_h=info.get('display_h'))
    assert media_hooks.hook_sprite(job) is True

    artifact = db.get_artifact(job.rel_path, 'sprite')
    assert artifact is not None
    assert '.sprite.' in artifact['file']
    assert os.path.exists(db.artifact_path(artifact))
    assert artifact['src_size'] == job.size

    row = db.query_one('SELECT * FROM media WHERE path = ?', (job.rel_path,))
    assert row['sprite_cols'] == 5
    assert row['sprite_rows'] == 4
    assert row['cell_w'] > 0 and row['cell_h'] > 0


def test_sprite_is_skipped_when_scrub_is_off(pipeline, sample_clip, monkeypatch):
    monkeypatch.setattr(media_hooks, 'SCRUB_ENABLED', False)
    manager, downloads, cache = pipeline
    folder = os.path.join(downloads, 'alpha [CB]')
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, 'alpha-20260101-120000.mp4')
    shutil.copy(sample_clip, target)

    from streamonitor.managers.httpmanager.ffmpeg_utils import probe
    info = probe(target)
    job = media_manager.Job('alpha [CB]/alpha-20260101-120000.mp4', target,
                            'alpha', 'CB', os.path.getsize(target),
                            os.path.getmtime(target), info['duration'], None,
                            display_w=info.get('display_w'),
                            display_h=info.get('display_h'))
    assert media_hooks.hook_sprite(job) is False
    assert db.get_artifact(job.rel_path, 'sprite') is None


# --- sweep ---------------------------------------------------------------

def test_sweep_removes_row_when_file_is_gone(pipeline):
    manager, downloads, cache = pipeline
    folder, path = make_recording(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    os.unlink(path)
    manager.sweep()
    assert db.query_one("SELECT path FROM media WHERE path = ?", (rel,)) is None


def test_sweep_removes_artifact_when_source_is_gone(pipeline):
    manager, downloads, cache = pipeline
    folder, path = make_recording(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    # The artifact lives at a location derived from the recording path.
    artifact = os.path.join(cache, db.artifact_relpath(rel, 'poster'))
    os.makedirs(os.path.dirname(artifact), exist_ok=True)
    with open(artifact, 'wb') as handle:
        handle.write(b'x')
    db.record_artifact(rel, 'poster', artifact, 1, 1.0, stat.st_size, stat.st_mtime)

    os.unlink(path)
    manager.sweep()

    assert not os.path.exists(artifact)
    assert db.get_artifact(rel, 'poster') is None


def test_sweep_regenerates_stale_artifact(pipeline):
    """When the source file changed, the artifact must be dropped."""
    manager, downloads, cache = pipeline
    folder, path = make_recording(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    artifact = os.path.join(cache, db.artifact_relpath(rel, 'poster'))
    os.makedirs(os.path.dirname(artifact), exist_ok=True)
    with open(artifact, 'wb') as handle:
        handle.write(b'x')
    # The artifact was built from an older version of the file.
    db.record_artifact(rel, 'poster', artifact, 1, 1.0, stat.st_size - 1, stat.st_mtime - 1)

    manager.sweep()

    assert not os.path.exists(artifact)
    assert db.get_artifact(rel, 'poster') is None


def test_sweep_removes_orphan_artifacts(pipeline):
    """An artifact without a database row or source file is garbage."""
    manager, downloads, cache = pipeline
    rel = 'alpha [CB]/gone.mp4'
    orphan = os.path.join(cache, db.artifact_relpath(rel, 'poster'))
    os.makedirs(os.path.dirname(orphan), exist_ok=True)
    with open(orphan, 'wb') as handle:
        handle.write(b'x')
    db.record_artifact(rel, 'poster', orphan, 1, 1.0, 1, 1.0)

    manager.sweep()

    assert not os.path.exists(orphan)


def test_sweep_removes_empty_cache_dirs(pipeline):
    manager, downloads, cache = pipeline
    empty = os.path.join(cache, 'alpha [CB]')
    os.makedirs(empty, exist_ok=True)

    manager.sweep()

    assert not os.path.exists(empty)


def test_sweep_keeps_valid_entries(pipeline):
    manager, downloads, cache = pipeline
    folder, path = make_recording(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    manager.sweep()

    assert db.query_one("SELECT path FROM media WHERE path = ?", (rel,)) is not None
    assert os.path.exists(path)
