"""Tests for the library endpoints: sorting, the recording in progress,
pagination and the artifacts."""
import os

from streamonitor.managers.httpmanager import db


def make_recording(downloads, username, site, name, size=1024):
    folder = os.path.join(downloads, f'{username} [{site}]')
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, name)
    with open(path, 'wb') as handle:
        handle.write(b'x' * size)
    return path


def register(downloads, username, site, names):
    out = []
    for name in names:
        path = os.path.join(downloads, f'{username} [{site}]', name)
        stat = os.stat(path)
        rel = os.path.join(f'{username} [{site}]', name)
        db.upsert_media(rel, username, site, stat.st_size, stat.st_mtime,
                        duration=5.0, width=640, height=360)
        out.append(rel)
    return out


# --- sorting --------------------------------------------------------------

def test_sort_by_size(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'small.mp4', 100)
    make_recording(downloads, 'a', 'CB', 'big.mp4', 9000)
    register(downloads, 'a', 'CB', ['small.mp4', 'big.mp4'])

    asc = client.get('/api/v2/folders/a/CB/videos?sort=size&dir=asc').get_json()
    assert [v['filename'] for v in asc['videos']] == ['small.mp4', 'big.mp4']

    desc = client.get('/api/v2/folders/a/CB/videos?sort=size&dir=desc').get_json()
    assert [v['filename'] for v in desc['videos']] == ['big.mp4', 'small.mp4']


def test_sort_by_duration(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    for name, duration in (('short.mp4', 2.0), ('long.mp4', 90.0)):
        make_recording(downloads, 'a', 'CB', name)
        rel = os.path.join('a [CB]', name)
        stat = os.stat(os.path.join(downloads, 'a [CB]', name))
        db.upsert_media(rel, 'a', 'CB', stat.st_size, stat.st_mtime, duration=duration)

    data = client.get('/api/v2/folders/a/CB/videos?sort=duration&dir=asc').get_json()
    assert [v['filename'] for v in data['videos']] == ['short.mp4', 'long.mp4']


def test_sort_by_name(client, workdir):
    """Sorting by name must not trip over the ambiguous path column."""
    downloads = os.path.join(workdir, 'downloads')
    for name in ('zebra.mp4', 'alpha.mp4', 'middle.mp4'):
        make_recording(downloads, 'a', 'CB', name)
    register(downloads, 'a', 'CB', ['zebra.mp4', 'alpha.mp4', 'middle.mp4'])

    response = client.get('/api/v2/folders/a/CB/videos?sort=name&dir=asc')
    assert response.status_code == 200
    names = [v['filename'] for v in response.get_json()['videos']]
    assert names == sorted(names)


def test_sort_by_date(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    old = make_recording(downloads, 'a', 'CB', 'old.mp4')
    new = make_recording(downloads, 'a', 'CB', 'new.mp4')
    # Distinct modification times, otherwise the order is undefined.
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    register(downloads, 'a', 'CB', ['old.mp4', 'new.mp4'])

    data = client.get('/api/v2/folders/a/CB/videos?sort=date&dir=desc').get_json()
    assert [v['filename'] for v in data['videos']] == ['new.mp4', 'old.mp4']

    asc = client.get('/api/v2/folders/a/CB/videos?sort=date&dir=asc').get_json()
    assert [v['filename'] for v in asc['videos']] == ['old.mp4', 'new.mp4']


# --- recording in progress ------------------------------------------------

def test_recording_in_progress_is_listed(client, workdir):
    """A file that is still being written has no metadata yet."""
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'done.mp4')
    register(downloads, 'a', 'CB', ['done.mp4'])
    make_recording(downloads, 'a', 'CB', 'live.mp4')

    data = client.get('/api/v2/folders/a/CB/videos').get_json()
    names = [v['filename'] for v in data['videos']]
    assert 'live.mp4' in names
    # The recording in progress comes first.
    assert names[0] == 'live.mp4'

    live = data['videos'][0]
    assert live['recording'] is True
    assert live['poster_url'] is None
    assert live['duration'] is None
    assert live['has_poster'] is False


def test_recording_in_progress_can_be_deleted(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'live.mp4')

    response = client.delete('/api/v2/videos/a%20%5BCB%5D/live.mp4')
    assert response.status_code == 200
    assert not os.path.exists(os.path.join(downloads, 'a [CB]', 'live.mp4'))


def test_folder_reports_recording_files(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'done.mp4')
    register(downloads, 'a', 'CB', ['done.mp4'])
    make_recording(downloads, 'a', 'CB', 'live.mp4')

    data = client.get('/api/v2/folders').get_json()
    folder = data['folders'][0]
    assert folder['recording_files'] == ['live.mp4']


# --- artifacts ------------------------------------------------------------

def test_video_payload_has_ready_to_use_urls(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'clip.mp4')
    register(downloads, 'a', 'CB', ['clip.mp4'])

    video = client.get('/api/v2/folders/a/CB/videos').get_json()['videos'][0]
    assert video['stream_url'].endswith('/stream')
    assert 'clip.mp4' in video['stream_url']
    # The paths are percent encoded, spaces included.
    assert '%20' in video['stream_url']


def test_stream_endpoint_serves_the_file(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'clip.mp4', 2048)
    register(downloads, 'a', 'CB', ['clip.mp4'])

    response = client.get('/api/v2/videos/a%20%5BCB%5D/clip.mp4/stream')
    assert response.status_code == 200
    assert len(response.data) == 2048


def test_stream_endpoint_supports_range(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'clip.mp4', 4096)
    register(downloads, 'a', 'CB', ['clip.mp4'])

    response = client.get('/api/v2/videos/a%20%5BCB%5D/clip.mp4/stream',
                          headers={'Range': 'bytes=0-1023'})
    assert response.status_code == 206
    assert len(response.data) == 1024


def test_poster_and_preview_report_missing(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'clip.mp4')
    register(downloads, 'a', 'CB', ['clip.mp4'])

    assert client.get('/api/v2/videos/a%20%5BCB%5D/clip.mp4/poster').status_code == 404
    assert client.get('/api/v2/videos/a%20%5BCB%5D/clip.mp4/preview').status_code == 404
    assert client.get('/api/v2/videos/a%20%5BCB%5D/clip.mp4/proxy').status_code == 404


# --- favourites view ------------------------------------------------------

def test_favorites_aggregates_over_folders(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    make_recording(downloads, 'b', 'Cam4', 'two.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    register(downloads, 'b', 'Cam4', ['two.mp4'])

    client.post('/api/v2/favorites/a%20%5BCB%5D/one.mp4')
    client.post('/api/v2/favorites/b%20%5BCam4%5D/two.mp4')

    data = client.get('/api/v2/favorites').get_json()
    assert data['count'] == 2
    assert {v['username'] for v in data['videos']} == {'a', 'b'}
    for video in data['videos']:
        assert video['favorite'] is True
        assert video['stream_url'].endswith('/stream')


def test_favorites_survive_a_deleted_media_row(client, workdir):
    """A favourite of a file that vanished is dropped as well."""
    downloads = os.path.join(workdir, 'downloads')
    make_recording(downloads, 'a', 'CB', 'one.mp4')
    register(downloads, 'a', 'CB', ['one.mp4'])
    client.post('/api/v2/favorites/a%20%5BCB%5D/one.mp4')

    os.unlink(os.path.join(downloads, 'a [CB]', 'one.mp4'))
    client.delete('/api/v2/videos/a%20%5BCB%5D/one.mp4')

    assert db.query_one('SELECT path FROM favorites WHERE path = ?',
                        ('a [CB]/one.mp4',)) is None
    assert client.get('/api/v2/favorites').get_json()['count'] == 0
