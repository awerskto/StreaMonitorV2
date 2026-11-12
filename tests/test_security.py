"""Tests of the protections against a foreign page.

The basic authentication is sent by the browser on its own, so a page on
another origin could otherwise start a streamer or delete a recording.
"""
import os
import time

from streamonitor.managers.httpmanager import db


def make_recording(downloads, username, site, name, size=1024):
    folder = os.path.join(downloads, f'{username} [{site}]')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'wb') as handle:
        handle.write(b'x' * size)
    return path


def register(downloads, username, site, names):
    for name in names:
        path = os.path.join(downloads, f'{username} [{site}]', name)
        stat = os.stat(path)
        rel = os.path.join(f'{username} [{site}]', name)
        db.upsert_media(rel, username, site, stat.st_size, stat.st_mtime, duration=5.0)


# --- the header that a foreign page cannot set ----------------------------

def test_a_post_without_the_header_is_rejected(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])

    # The credentials are there, but the request does not come from the
    # application.
    response = client.post('/api/v2/favorites/a%20%5BCB%5D/one.mp4',
                           headers={'X-Requested-With': ''})
    assert response.status_code == 403


def test_a_delete_without_the_header_is_rejected(client, workdir):
    response = client.delete('/api/v2/videos/a%20%5BCB%5D/one.mp4',
                             headers={'X-Requested-With': ''})
    assert response.status_code == 403


def test_a_foreign_origin_is_rejected(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])

    response = client.post('/api/v2/favorites/a%20%5BCB%5D/one.mp4',
                           headers={'X-Requested-With': 'strea',
                                    'Origin': 'http://evil.example'})
    assert response.status_code == 403


def test_the_own_origin_is_accepted(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])

    # The origin of the application itself: the test client talks to
    # localhost, the interface would send the host it was opened on.
    response = client.post('/api/v2/favorites/a%20%5BCB%5D/one.mp4',
                           headers={'X-Requested-With': 'strea',
                                    'Origin': 'http://localhost'})
    assert response.status_code in (200, 201)


def test_a_get_is_not_blocked(client, workdir):
    """Only the requests that change something are protected."""
    assert client.get('/api/v2/meta').status_code == 200


# --- only the recordings may be deleted -----------------------------------

def test_deleting_a_helper_file_is_rejected(client, workdir):
    """The downloader keeps its own files in the folder of a recording."""
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    with open(os.path.join(downloads, 'a [CB]', '.playlist.m3u8'), 'w') as handle:
        handle.write('#EXTM3U\n')

    response = client.delete('/api/v2/videos/a%20%5BCB%5D/.playlist.m3u8')
    assert response.status_code == 400
    assert os.path.exists(os.path.join(downloads, 'a [CB]', '.playlist.m3u8'))


def test_deleting_a_foreign_file_in_the_downloads_folder_is_rejected(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    with open(os.path.join(downloads, 'note.txt'), 'w') as handle:
        handle.write('x')

    response = client.delete('/api/v2/videos/note.txt')
    assert response.status_code == 400
    assert os.path.exists(os.path.join(downloads, 'note.txt'))


def test_the_cache_is_not_reachable(client, workdir):
    """The database and the artifacts belong to the application."""
    downloads = os.path.join(workdir, 'downloads')
    cache = os.path.join(downloads, '.cache')
    os.makedirs(cache, exist_ok=True)
    with open(os.path.join(cache, 'index.sqlite'), 'w') as handle:
        handle.write('x')

    response = client.delete('/api/v2/videos/.cache/index.sqlite')
    assert response.status_code == 400
    assert os.path.exists(os.path.join(cache, 'index.sqlite'))


def test_a_recording_that_is_still_running_is_not_deleted(client, workdir, monkeypatch):
    """The recorder would go on writing into a file nobody sees any more."""
    from streamonitor.managers.httpmanager import media_manager
    monkeypatch.setattr(media_manager, 'STABLE_AFTER', 60.0)

    downloads = os.path.join(workdir, 'downloads')
    path = make_recording(downloads, 'a', 'CB', 'live.mp4')
    register(downloads, 'a', 'CB', ['live.mp4'])

    response = client.delete('/api/v2/videos/a%20%5BCB%5D/live.mp4')
    assert response.status_code == 409
    assert os.path.exists(path)


def test_a_recording_in_progress_without_metadata_can_be_deleted(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'live.mp4')

    response = client.delete('/api/v2/videos/a%20%5BCB%5D/live.mp4')
    assert response.status_code == 200
    assert not os.path.exists(os.path.join(downloads, 'a [CB]', 'live.mp4'))


def test_a_recording_of_the_database_can_be_deleted(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])

    response = client.delete('/api/v2/videos/a%20%5BCB%5D/one.mp4')
    assert response.status_code == 200
    assert not os.path.exists(os.path.join(downloads, 'a [CB]', 'one.mp4'))
    assert db.query_one('SELECT * FROM media WHERE path = ?', ('a [CB]/one.mp4',)) is None


def test_an_unknown_recording_is_404(client, workdir):
    response = client.delete('/api/v2/videos/a%20%5BCB%5D/gone.mp4')
    assert response.status_code == 404
