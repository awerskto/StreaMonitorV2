"""Tests for the v2 JSON API: auth, folders, sorting, delete and favourites."""
import os
import shutil
import time

import pytest

from streamonitor.managers.httpmanager import db


def make_folder(downloads, username, site, files=('a.mp4', 'b.mp4')):
    """Create a streamer folder with recordings on disk."""
    folder = os.path.join(downloads, f'{username} [{site}]')
    os.makedirs(folder, exist_ok=True)
    paths = []
    for index, name in enumerate(files):
        path = os.path.join(folder, name)
        with open(path, 'wb') as handle:
            handle.write(b'x' * (1024 * (index + 1)))
        paths.append(os.path.relpath(path, downloads))
    return folder, paths


def register(downloads, username, site, files):
    """Register the files in the database as if the pipeline had scanned them."""
    for index, rel in enumerate(files):
        abs_path = os.path.join(downloads, rel)
        stat = os.stat(abs_path)
        db.upsert_media(rel, username, site, stat.st_size, stat.st_mtime,
                        duration=10.0 + index, width=320, height=240)


# --- auth ----------------------------------------------------------------

def test_api_requires_auth(client, monkeypatch):
    import parameters
    monkeypatch.setattr(parameters, 'WEBSERVER_PASSWORD', 'secret')

    response = client.get('/api/v2/streamers', headers={'Authorization': ''})
    assert response.status_code == 401
    assert 'WWW-Authenticate' in response.headers


def test_api_accepts_correct_credentials(client, monkeypatch):
    import parameters
    monkeypatch.setattr(parameters, 'WEBSERVER_PASSWORD', 'secret')

    import base64
    token = base64.b64encode(b'admin:secret').decode()
    response = client.get('/api/v2/streamers', headers={'Authorization': f'Basic {token}'})
    assert response.status_code == 200


def test_api_rejects_wrong_password(client, monkeypatch):
    import parameters
    monkeypatch.setattr(parameters, 'WEBSERVER_PASSWORD', 'secret')

    import base64
    token = base64.b64encode(b'admin:wrong').decode()
    response = client.get('/api/v2/streamers', headers={'Authorization': f'Basic {token}'})
    assert response.status_code == 401


# --- folders -------------------------------------------------------------

def test_list_folders_groups_by_streamer(client, workdir, monkeypatch):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB')
    register(downloads, 'alpha', 'CB', files)

    response = client.get('/api/v2/folders')
    assert response.status_code == 200
    data = response.get_json()
    assert data['total'] == 1
    folder = data['folders'][0]
    assert folder['username'] == 'alpha'
    assert folder['site'] == 'CB'
    assert folder['count'] == 2
    assert folder['total_size'] > 0


def test_list_folders_search(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB')
    register(downloads, 'alpha', 'CB', files)
    _, other = make_folder(downloads, 'bravo', 'Cam4')
    register(downloads, 'bravo', 'Cam4', other)

    data = client.get('/api/v2/folders?q=bravo').get_json()
    assert data['total'] == 1
    assert data['folders'][0]['username'] == 'bravo'


def test_list_folders_sort_by_name(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    for name in ('charlie', 'alpha', 'bravo'):
        _, files = make_folder(downloads, name, 'CB')
        register(downloads, name, 'CB', files)

    data = client.get('/api/v2/folders?sort=name').get_json()
    assert [f['username'] for f in data['folders']] == ['alpha', 'bravo', 'charlie']


# --- videos --------------------------------------------------------------

def test_list_videos_sorted_by_date_desc(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB', ('a.mp4', 'b.mp4', 'c.mp4'))
    register(downloads, 'alpha', 'CB', files)

    data = client.get('/api/v2/folders/alpha/CB/videos?sort=date&dir=desc').get_json()
    assert data['total'] == 3
    times = [v['mtime'] for v in data['videos']]
    assert times == sorted(times, reverse=True)


def test_list_videos_sorted_by_size(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB', ('a.mp4', 'b.mp4', 'c.mp4'))
    register(downloads, 'alpha', 'CB', files)

    data = client.get('/api/v2/folders/alpha/CB/videos?sort=size&dir=asc').get_json()
    sizes = [v['size'] for v in data['videos']]
    assert sizes == sorted(sizes)


def test_list_videos_pagination(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB',
                           tuple(f'v{i}.mp4' for i in range(7)))
    register(downloads, 'alpha', 'CB', files)

    first = client.get('/api/v2/folders/alpha/CB/videos?page=1&per_page=3').get_json()
    second = client.get('/api/v2/folders/alpha/CB/videos?page=2&per_page=3').get_json()
    assert len(first['videos']) == 3
    assert len(second['videos']) == 3
    assert first['total'] == 7


def test_favorites_only_filter(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB', ('a.mp4', 'b.mp4'))
    register(downloads, 'alpha', 'CB', files)

    db.toggle_favorite(files[0])
    data = client.get('/api/v2/folders/alpha/CB/videos?favorites=1').get_json()
    assert data['total'] == 1
    assert data['videos'][0]['path'] == files[0]


# --- favourites ----------------------------------------------------------

def test_favorite_toggle_roundtrip(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB')
    register(downloads, 'alpha', 'CB', files)

    first = client.post(f'/api/v2/favorites/{files[0]}').get_json()
    assert first['favorite'] is True

    second = client.post(f'/api/v2/favorites/{files[0]}').get_json()
    assert second['favorite'] is False


def test_favorites_survive_restart(client, workdir):
    """Favourites live in the database, so they outlive the process."""
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB')
    register(downloads, 'alpha', 'CB', files)

    client.post(f'/api/v2/favorites/{files[0]}')

    # Simulate a restart: a new connection against the same database file.
    from streamonitor.managers.httpmanager import db as db_module
    db_module.close()
    db_module._local = type(db_module._local)()
    assert db_module.is_favorite(files[0]) is True


def test_list_favorites_endpoint(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB')
    register(downloads, 'alpha', 'CB', files)

    client.post(f'/api/v2/favorites/{files[0]}')
    data = client.get('/api/v2/favorites').get_json()
    assert data['count'] == 1
    assert data['videos'][0]['path'] == files[0]


# --- delete --------------------------------------------------------------

def test_delete_video_removes_file_and_favorite(client, workdir):
    downloads = os.path.join(workdir, 'downloads')
    _, files = make_folder(downloads, 'alpha', 'CB')
    register(downloads, 'alpha', 'CB', files)
    client.post(f'/api/v2/favorites/{files[0]}')

    response = client.delete(f'/api/v2/videos/{files[0]}')
    assert response.status_code == 200

    assert not os.path.exists(os.path.join(downloads, files[0]))
    assert db.query_one('SELECT path FROM media WHERE path = ?', (files[0],)) is None
    assert db.query_one('SELECT path FROM favorites WHERE path = ?', (files[0],)) is None


def test_delete_unknown_video_is_404(client):
    assert client.delete('/api/v2/videos/nope.mp4').status_code == 404


# --- path traversal ------------------------------------------------------

@pytest.mark.parametrize('evil', [
    '../../etc/passwd',
    '../../../etc/shadow',
    'user [CB]/../../../../etc/passwd',
])
def test_path_traversal_is_rejected(client, evil):
    response = client.get(f'/api/v2/videos/{evil}/stream')
    assert response.status_code in (400, 404)


def test_delete_path_traversal_is_rejected(client):
    response = client.delete('/api/v2/videos/../../etc/passwd')
    assert response.status_code in (400, 404)


# --- meta ----------------------------------------------------------------

def test_meta_returns_sites_and_statuses(client):
    data = client.get('/api/v2/meta').get_json()
    assert 'CB' in data['sites']
    assert 'statuses' in data
    assert 'free_space' in data
