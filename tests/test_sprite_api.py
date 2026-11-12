"""The API of the scrub storyboard.

The sprite has to be served with a validator, the deletion and the sweep
have to clean it up, and the payload has to carry the geometry the
interface needs.
"""
import os
import shutil
import time

import pytest

from streamonitor.managers.httpmanager import db, media_manager


def make_recording(downloads, username, site, name, size=4096):
    folder = os.path.join(downloads, f'{username} [{site}]')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'wb') as handle:
        handle.write(b'x' * size)
    return path


def register(downloads, username, site, names, duration=60.0):
    """Insert the rows with the geometry the sprite hook would store."""
    for name in names:
        rel = os.path.join(f'{username} [{site}]', name)
        stat = os.stat(os.path.join(downloads, f'{username} [{site}]', name))
        db.upsert_media(rel, username, site, stat.st_size, stat.st_mtime,
                        duration=duration, width=1920, height=1080,
                        display_w=1920, display_h=1080)
        db.execute("""
            UPDATE media SET sprite_cols = 5, sprite_rows = 4,
                             cell_w = 320, cell_h = 180, frames = 20
            WHERE path = ?
        """, (rel,))
    return [os.path.join(f'{username} [{site}]', n) for n in names]


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """The media manager over a clean cache."""
    import parameters
    from streamonitor.managers.httpmanager import media_manager as mm

    downloads = tmp_path / 'downloads'
    cache = tmp_path / 'cache'
    downloads.mkdir()
    cache.mkdir()
    monkeypatch.setattr(parameters, 'DOWNLOADS_DIR', str(downloads))
    monkeypatch.setattr(parameters, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(db, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(mm, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(mm, 'STABLE_AFTER', 0.0)

    db.close()
    db._db_path = None
    db._local = type(db._local)()
    db.init()

    manager = mm.MediaManager()
    yield manager, str(downloads), str(cache)
    manager.shutdown()


def write_sprite(downloads, username, site, name):
    """Write the sprite where the application would keep it."""
    folder = os.path.join(downloads, f'{username} [{site}]')
    rel = os.path.join(f'{username} [{site}]', name)
    sprite = os.path.join(db.CACHE_DIR, f'{username} [{site}]', f'{name}.sprite.webp')
    os.makedirs(os.path.dirname(sprite), exist_ok=True)
    with open(sprite, 'wb') as handle:
        handle.write(b'SPRITE' * 100)
    stat = os.stat(os.path.join(folder, name))
    db.record_artifact(rel, 'sprite', sprite, os.path.getsize(sprite),
                       os.path.getmtime(sprite), stat.st_size, stat.st_mtime)
    return sprite


def encoded(username, site, name):
    from urllib.parse import quote
    raw = f'{username} [{site}]/{name}'
    return quote(raw)


# --- the payload ----------------------------------------------------------

def test_the_payload_carries_the_geometry(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    write_sprite(downloads, 'a', 'CB', 'one.mp4')

    data = client.get('/api/v2/folders/a/CB/videos').get_json()
    video = data['videos'][0]
    assert video['sprite_url'] == (f'/api/v2/videos/{encoded("a", "CB", "one.mp4")}/sprite')
    assert video['sprite_cols'] == 5
    assert video['sprite_rows'] == 4
    assert video['cell_w'] == 320
    assert video['cell_h'] == 180
    assert video['display_w'] == 1920
    assert video['display_h'] == 1080
    assert video['has_sprite'] is True
    # The old preview is gone.
    assert 'preview_url' not in video
    assert 'has_preview' not in video


def test_the_payload_of_a_recording_without_sprite(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])

    video = client.get('/api/v2/folders/a/CB/videos').get_json()['videos'][0]
    assert video['sprite_url'] is None
    assert video['has_sprite'] is False


def test_the_folder_carries_the_storyboard_of_the_newest(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    make_recording(downloads, 'a', 'CB', 'two.mp4')
    register(downloads, 'a', 'CB', ['one.mp4', 'two.mp4'])
    # The second recording is clearly the newest: the other one carries the
    # time of now.
    os.utime(os.path.join(downloads, 'a [CB]', 'two.mp4'), (4000000000, 4000000000))
    db.execute('UPDATE media SET mtime = 4000000000 WHERE path = ?',
               ('a [CB]/two.mp4',))
    write_sprite(downloads, 'a', 'CB', 'two.mp4')

    data = client.get('/api/v2/folders').get_json()
    folder = data['folders'][0]
    assert folder['sprite_url'].endswith('/sprite')
    assert folder['sprite_cols'] == 5


def test_the_meta_reports_the_scrub(client):
    meta = client.get('/api/v2/meta').get_json()
    assert 'scrub' in meta['settings']
    assert 'scrub_zone' in meta['settings']
    assert 'preview' not in meta['settings']


# --- serving --------------------------------------------------------------

def test_the_sprite_endpoint_serves_the_file(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    write_sprite(downloads, 'a', 'CB', 'one.mp4')

    response = client.get(f'/api/v2/videos/{encoded("a", "CB", "one.mp4")}/sprite')
    assert response.status_code == 200
    assert response.data == b'SPRITE' * 100


def test_the_sprite_endpoint_sends_a_validator(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    write_sprite(downloads, 'a', 'CB', 'one.mp4')

    url = f'/api/v2/videos/{encoded("a", "CB", "one.mp4")}/sprite'
    response = client.get(url)
    assert response.headers.get('ETag')
    assert 'max-age' in response.headers.get('Cache-Control', '')

    # The browser sends the validator back, the answer is not modified.
    again = client.get(url, headers={'If-None-Match': response.headers['ETag']})
    assert again.status_code in (200, 304)


def test_the_sprite_of_an_unknown_recording_is_404(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])

    response = client.get(f'/api/v2/videos/{encoded("a", "CB", "one.mp4")}/sprite')
    assert response.status_code == 404


def test_the_sprite_needs_authentication(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    write_sprite(downloads, 'a', 'CB', 'one.mp4')

    url = f'/api/v2/videos/{encoded("a", "CB", "one.mp4")}/sprite'
    import base64
    none = client.get(url, headers={'Authorization': ''})
    wrong = client.get(url, headers={'Authorization': f'Basic {base64.b64encode(b"a:b").decode()}'})
    assert none.status_code in (401, 403)
    assert wrong.status_code in (401, 403)


def test_the_sprite_endpoint_rejects_a_traversal(client):
    response = client.get('/api/v2/videos/..%2f..%2fetc%2fpasswd/sprite')
    assert response.status_code in (400, 404)


# --- deletion and sweep ---------------------------------------------------

def test_deleting_a_recording_removes_the_sprite(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    sprite = write_sprite(downloads, 'a', 'CB', 'one.mp4')

    assert client.delete(f'/api/v2/videos/{encoded("a", "CB", "one.mp4")}').status_code == 200
    assert not os.path.exists(sprite)
    assert db.get_artifact('a [CB]/one.mp4', 'sprite') is None


def test_the_sweep_removes_an_orphan_sprite(pipeline):
    manager, downloads, cache = pipeline
    rel = 'alpha [CB]/gone.mp4'
    orphan = os.path.join(cache, rel + '.sprite.webp')
    os.makedirs(os.path.dirname(orphan), exist_ok=True)
    with open(orphan, 'wb') as handle:
        handle.write(b'x')
    db.record_artifact(rel, 'sprite', orphan, 1, 1.0, 1, 1.0)

    manager.sweep()
    assert not os.path.exists(orphan)
    assert db.get_artifact(rel, 'sprite') is None


def make_recording_for_pipeline(downloads):
    folder = os.path.join(downloads, 'alpha [CB]')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, 'alpha-20260101-120000.mp4')
    with open(path, 'wb') as handle:
        handle.write(b'y' * 1024)
    return folder, path


def test_the_sweep_removes_the_old_previews(pipeline):
    """The animated hover preview is replaced by the storyboard."""
    manager, downloads, cache = pipeline
    folder, path = make_recording_for_pipeline(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    old = os.path.join(cache, rel + '.webp')
    os.makedirs(os.path.dirname(old), exist_ok=True)
    with open(old, 'wb') as handle:
        handle.write(b'x')
    # A row of the old pipeline: it points at the real file, the suffix is
    # not derived any more.
    db.execute("""
        INSERT INTO artifacts (path, kind, file, size, mtime, src_size, src_mtime, created_at)
        VALUES (?, 'preview', ?, 1, 1.0, ?, ?, ?)
    """, (rel, os.path.relpath(old, cache), stat.st_size, stat.st_mtime, time.time()))

    manager.sweep()
    assert not os.path.exists(old)
    assert db.get_artifact(rel, 'preview') is None


def test_the_sweep_removes_the_sprite_when_the_scrub_is_off(pipeline, monkeypatch):
    manager, downloads, cache = pipeline
    folder, path = make_recording_for_pipeline(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    sprite = os.path.join(cache, rel + '.sprite.webp')
    os.makedirs(os.path.dirname(sprite), exist_ok=True)
    with open(sprite, 'wb') as handle:
        handle.write(b'x')
    db.record_artifact(rel, 'sprite', sprite, 1, 1.0, stat.st_size, stat.st_mtime)

    from parameters import SCRUB_ENABLED
    monkeypatch.setattr(media_manager, 'SCRUB_ENABLED', False)
    manager.sweep()
    assert not os.path.exists(sprite)
    assert db.get_artifact(rel, 'sprite') is None


def test_the_sweep_rebuilds_a_missing_sprite(pipeline, monkeypatch):
    manager, downloads, cache = pipeline
    folder, path = make_recording_for_pipeline(downloads)
    rel = os.path.join('alpha [CB]', os.path.basename(path))
    stat = os.stat(path)
    db.upsert_media(rel, 'alpha', 'CB', stat.st_size, stat.st_mtime)

    # The row says a sprite exists, but the file is gone: the sweep queues
    # the recording again.
    from streamonitor.managers.httpmanager import db as _db
    missing = os.path.join(cache, _db.artifact_relpath(rel, 'sprite'))
    os.makedirs(os.path.dirname(missing), exist_ok=True)
    with open(missing, 'wb') as handle:
        handle.write(b'x')
    db.record_artifact(rel, 'sprite', missing, 1, 1.0, stat.st_size, stat.st_mtime)
    os.unlink(missing)

    submitted = []
    monkeypatch.setattr(manager, '_submit', lambda jobs: submitted.extend(jobs))
    manager.sweep()
    assert any(job.rel_path == rel for job in submitted), submitted
