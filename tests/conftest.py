"""Shared pytest fixtures.

The tests exercise the real Flask app with a real (temporary) SQLite database
and generated media files, so the API is covered end to end. ffmpeg is used to
generate the sample clips; when it is unavailable those tests are skipped.
"""
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

from parameters import FFMPEG_PATH, FFPROBE_PATH  # noqa: E402


def have_ffmpeg():
    return shutil.which(FFMPEG_PATH) is not None or os.path.exists(FFMPEG_PATH)


@pytest.fixture(scope='session')
def workdir():
    """A temporary directory used as DOWNLOADS_DIR and CACHE_DIR."""
    path = tempfile.mkdtemp(prefix='sm-test-')
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope='session')
def sample_clip(tmp_path_factory):
    """A short synthetic video, or skip when ffmpeg is not available."""
    if not have_ffmpeg():
        pytest.skip('ffmpeg not available')
    target = tmp_path_factory.mktemp('media') / 'clip.mp4'
    cmd = [
        FFMPEG_PATH, '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=3',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest',
        str(target),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0 or not target.exists():
        pytest.skip(f'could not generate sample clip: {result.stderr[:200]}')
    return target


@pytest.fixture
def client(workdir, monkeypatch):
    """A Flask test client with the API blueprint registered.

    Parameters are read at import time, so the temporary directories are
    injected into the already imported modules.
    """
    import parameters
    from streamonitor.managers.httpmanager import api_v2, db, media_manager

    downloads = os.path.join(workdir, 'downloads')
    cache = os.path.join(workdir, 'cache')
    # The work directory is shared by the session, so start from a clean slate:
    # leftover files would look like recordings in progress.
    shutil.rmtree(downloads, ignore_errors=True)
    shutil.rmtree(cache, ignore_errors=True)
    os.makedirs(downloads, exist_ok=True)
    os.makedirs(cache, exist_ok=True)

    monkeypatch.setattr(parameters, 'DOWNLOADS_DIR', downloads)
    monkeypatch.setattr(parameters, 'CACHE_DIR', cache)
    monkeypatch.setattr(api_v2, 'DOWNLOADS_DIR', downloads)
    monkeypatch.setattr(api_v2, 'CACHE_DIR', cache)
    monkeypatch.setattr(db, 'CACHE_DIR', cache)
    monkeypatch.setattr(media_manager, 'CACHE_DIR', cache)
    monkeypatch.setattr(media_manager, 'STABLE_AFTER', 0.0)

    # A fresh database per test: drop the file, not just the connection.
    db.close()
    for suffix in ('', '-wal', '-shm'):
        candidate = os.path.join(cache, 'index.sqlite' + suffix)
        if os.path.exists(candidate):
            os.unlink(candidate)
    db._db_path = None
    db._local = type(db._local)()
    db.init()

    # The site classes register themselves on import.
    import streamonitor.sites  # noqa: F401

    from flask import Flask
    app = Flask(__name__)
    app.config['TESTING'] = True
    app.register_blueprint(api_v2.bp)

    class FakeManager:
        def __init__(self):
            self.streamers = []
        def getStreamer(self, user, site):
            for s in self.streamers:
                if s.username == user and (not site or s.siteslug == site):
                    return s
            return None
        def do_add(self, streamer, username, site):
            return 'Added [CB] ' + username
        def do_remove(self, streamer, user, site):
            return 'OK'
        def do_start(self, streamer, user, site):
            return 'OK'
        def do_stop(self, streamer, user, site):
            return 'OK'

    manager = FakeManager()
    monkeypatch.setattr(api_v2, '_manager', lambda: manager)

    # The default password is "admin", so every request needs credentials.
    import base64
    token = base64.b64encode(b'admin:admin').decode()
    default_headers = {
        'Authorization': f'Basic {token}',
        # Every mutating request of the SPA carries this header, the server
        # rejects a request that wants to change something without it.
        'X-Requested-With': 'strea',
    }

    class AuthClient:
        """Wraps the Flask test client and adds the auth header by default.

        Tests that exercise authentication pass their own header, which wins.
        """

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def _request(self, method, *args, **kwargs):
            headers = dict(default_headers)
            headers.update(kwargs.pop('headers', None) or {})
            return getattr(self._inner, method)(*args, headers=headers, **kwargs)

        def get(self, *args, **kwargs):
            return self._request('get', *args, **kwargs)

        def post(self, *args, **kwargs):
            return self._request('post', *args, **kwargs)

        def delete(self, *args, **kwargs):
            return self._request('delete', *args, **kwargs)

        def put(self, *args, **kwargs):
            return self._request('put', *args, **kwargs)

    with app.test_client() as test_client:
        test_client.manager = manager
        yield AuthClient(test_client)
