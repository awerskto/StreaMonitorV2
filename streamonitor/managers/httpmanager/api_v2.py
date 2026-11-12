"""JSON API (v2) for the new "tube" skin.

Deliberately separate from the existing HTML routes so the old skins keep
working untouched. Every route requires authentication and every path that
comes from the client is validated with safe_join before it touches the disk.
"""
import os
import time
from enum import Enum
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request, send_file

from parameters import (
    CACHE_DIR, CONTAINER, DOWNLOADS_DIR, FFMPEG_PATH, PROXY_ENABLED,
    SCRUB_ENABLED, SCRUB_ZONE, THUMBS_ENABLED, WEB_LIST_FREQUENCY,
    WEBSERVER_PASSWORD, WEBSERVER_SKIN,
)
from streamonitor.bot import Bot, LOADED_SITES
from streamonitor.enums import Status
from streamonitor.managers.outofspace_detector import OOSDetector
from streamonitor.utils.human_file_size import human_file_size
from streamonitor.utils.safe_path import UnsafePathError, safe_join

from . import db
from . import media_manager
from .auth import csrf_protect, login_required
from .media_manager import get_manager

bp = Blueprint('api_v2', __name__, url_prefix='/api/v2')

# Statuses the UI can filter by, in display order.
FILTERABLE_STATUS = {
    'online': Status.PUBLIC,
    'offline': Status.OFFLINE,
    'longoffline': Status.LONG_OFFLINE,
    'notrunning': Status.NOTRUNNING,
    'private': Status.PRIVATE,
    'restricted': Status.RESTRICTED,
    'error': Status.ERROR,
    'ratelimit': Status.RATELIMIT,
    'notexist': Status.NOTEXIST,
}


def _manager():
    """The HTTPManager instance, which owns the streamer list."""
    from streamonitor.managers.httpmanager.httpmanager import current_manager
    return current_manager()


def _json(data, status=200, headers=None):
    response = jsonify(data)
    response.status_code = status
    if headers:
        for key, value in headers.items():
            response.headers[key] = value
    return response


# The icons of the sprite of the "tube" skin. The old skins use the font
# awesome names, so they keep their own mapping.
SPA_STATUS_ICONS = {
    Status.UNKNOWN: 'help',
    Status.PUBLIC: 'eye',
    Status.OFFLINE: 'octagon',
    Status.LONG_OFFLINE: 'octagon',
    Status.PRIVATE: 'eye-off',
    Status.RATELIMIT: 'alert',
    Status.NOTEXIST: 'minus',
    Status.NOTRUNNING: 'bell-off',
    Status.ERROR: 'alert',
    Status.RESTRICTED: 'lock',
}


def _folder_stats(username, slug):
    """The number of recordings, their size and the newest one.

    The database is the authority: it also knows the recordings that were
    made before the application was started.
    """
    empty = {'count': 0, 'total_size': 0, 'last_activity': 0}
    try:
        row = db.query_one("""
            SELECT COUNT(*) AS count,
                   COALESCE(SUM(size), 0) AS total_size,
                   COALESCE(MAX(mtime), 0) AS last_activity
            FROM media WHERE username = ? AND site = ?
        """, (username, slug))
    except Exception:
        return empty
    if row is None:
        return empty
    return {
        'count': row['count'] or 0,
        'total_size': row['total_size'] or 0,
        'last_activity': row['last_activity'] or 0,
    }


def _streamer_payload(streamer, stats=None):
    """Serialise one Bot for the UI."""
    videos = getattr(streamer, 'video_files', []) or []
    country = streamer.country_data or {}
    gender = streamer.gender_data or {}
    if stats is None:
        stats = _folder_stats(streamer.username, streamer.siteslug)
    return {
        'username': streamer.username,
        'site': streamer.site,
        'siteslug': streamer.siteslug,
        'status': streamer.sc.value,
        'status_label': streamer.status(),
        'status_icon': SPA_STATUS_ICONS.get(streamer.sc, 'help'),
        'running': bool(streamer.running),
        'recording': bool(streamer.recording),
        'country': streamer.country,
        'country_name': country.get('name'),
        'country_flag': country.get('flag'),
        'gender': streamer.gender.value if isinstance(streamer.gender, Enum) else streamer.gender,
        'gender_name': gender.get('name'),
        'url': streamer.url,
        'video_count': stats['count'] or len(videos),
        'total_size': stats['total_size'] or getattr(streamer, 'video_files_total_size', 0),
        'total_size_human': human_file_size(
            stats['total_size'] or getattr(streamer, 'video_files_total_size', 0)),
        'last_stream': stats['last_activity'] or _last_stream(streamer),
    }


def _last_stream(streamer):
    """Unix timestamp of the newest recording, or 0."""
    videos = getattr(streamer, 'video_files', []) or []
    times = [getattr(v, 'mtime', 0) for v in videos]
    return max(times) if times else 0


def _matches(streamer, site=None, status=None, query=None):
    if site and streamer.siteslug.lower() != site.lower():
        return False
    if status:
        wanted = FILTERABLE_STATUS.get(status.lower())
        if wanted is not None and streamer.sc != wanted:
            return False
    if query:
        haystack = f"{streamer.username} {streamer.site}".lower()
        if query.lower() not in haystack:
            return False
    return True


# --- accounts ------------------------------------------------------------

@bp.route('/streamers', methods=['GET'])
@login_required
def list_streamers():
    manager = _manager()
    site = request.args.get('site') or None
    status = request.args.get('status') or None
    query = request.args.get('q') or None
    sort = request.args.get('sort') or 'username'
    direction = (request.args.get('dir') or 'asc').lower()

    items = [s for s in manager.streamers if _matches(s, site, status, query)]

    # One query for every account, not one per account: the list is asked
    # for by every open browser tab every few seconds.
    stats = {}
    try:
        for row in db.query("""
            SELECT username, site,
                   COUNT(*) AS count,
                   COALESCE(SUM(size), 0) AS total_size,
                   COALESCE(MAX(mtime), 0) AS last_activity
            FROM media GROUP BY username, site
        """):
            stats[(row['username'], row['site'])] = {
                'count': row['count'],
                'total_size': row['total_size'],
                'last_activity': row['last_activity'],
            }
    except Exception:
        pass

    payloads = [_streamer_payload(s, stats.get((s.username, s.siteslug)))
                for s in items]

    keys = {
        'username': lambda p: p['username'].lower(),
        'site': lambda p: p['site'].lower(),
        'status': lambda p: p['status'],
        'last': lambda p: p['last_stream'],
        'recordings': lambda p: p['video_count'],
        'size': lambda p: p['total_size'],
    }
    key = keys.get(sort, keys['username'])
    reverse = direction == 'desc'
    # The name breaks the ties, so the list is stable within a column.
    payloads.sort(key=lambda p: (key(p), p['username'].lower()), reverse=reverse)

    return _json({
        'streamers': payloads,
        'count': len(payloads),
        'sort': sort,
        'dir': direction,
        'free_space': _free_space(),
    })


@bp.route('/streamers/<user>/<site>', methods=['GET'])
@login_required
def get_streamer(user, site):
    manager = _manager()
    streamer = manager.getStreamer(user, site)
    if streamer is None:
        return _json({'error': 'Streamer not found'}, 404)
    return _json({'streamer': _streamer_payload(streamer)})


@bp.route('/streamers', methods=['POST'])
@login_required
@csrf_protect
def add_streamer():
    manager = _manager()
    payload = request.get_json(silent=True) or request.form
    username = (payload.get('username') or '').strip()
    site = (payload.get('site') or '').strip()
    if not username or not site:
        return _json({'error': 'Missing value(s)'}, 400)

    existing = manager.getStreamer(username, site)
    result = manager.do_add(existing, username, site)
    if result != 'Added [CB] ' + username and 'Added' not in str(result):
        status = 409 if 'already exists' in str(result) else 400
        return _json({'error': str(result)}, status)
    return _json({'result': str(result)}, 201)


@bp.route('/streamers/<user>/<site>', methods=['DELETE'])
@login_required
@csrf_protect
def remove_streamer(user, site):
    manager = _manager()
    streamer = manager.getStreamer(user, site)
    result = manager.do_remove(streamer, user, site)
    if result != 'OK':
        return _json({'error': str(result)}, 404)
    return _json({'result': 'OK'})


def _set_running(manager, targets, running):
    """Start or stop monitoring for the given streamers."""
    errors = []
    changed = 0
    for streamer in targets:
        if running:
            result = manager.do_start(streamer, None, None)
        else:
            result = manager.do_stop(streamer, None, None)
        if result == 'OK':
            changed += 1
        else:
            errors.append(streamer.username)
    return changed, errors


@bp.route('/streamers/<user>/<site>/start', methods=['POST'])
@login_required
@csrf_protect
def start_streamer(user, site):
    manager = _manager()
    streamer = manager.getStreamer(user, site)
    if streamer is None:
        return _json({'error': 'Streamer not found'}, 404)
    changed, errors = _set_running(manager, [streamer], True)
    return _json({'changed': changed, 'errors': errors}, 200 if not errors else 207)


@bp.route('/streamers/<user>/<site>/stop', methods=['POST'])
@login_required
@csrf_protect
def stop_streamer(user, site):
    manager = _manager()
    streamer = manager.getStreamer(user, site)
    if streamer is None:
        return _json({'error': 'Streamer not found'}, 404)
    changed, errors = _set_running(manager, [streamer], False)
    return _json({'changed': changed, 'errors': errors}, 200 if not errors else 207)


@bp.route('/streamers/start', methods=['POST'])
@login_required
@csrf_protect
def start_streamers_bulk():
    """Start every streamer, or only the ones in the request body."""
    manager = _manager()
    payload = request.get_json(silent=True) or {}
    usernames = payload.get('streamers')
    if usernames:
        targets = [s for s in manager.streamers
                   if f"{s.username}|{s.siteslug}" in usernames or s.username in usernames]
    else:
        targets = list(manager.streamers)
    changed, errors = _set_running(manager, targets, True)
    return _json({'changed': changed, 'errors': errors}, 200 if not errors else 207)


@bp.route('/streamers/stop', methods=['POST'])
@login_required
@csrf_protect
def stop_streamers_bulk():
    manager = _manager()
    payload = request.get_json(silent=True) or {}
    usernames = payload.get('streamers')
    if usernames:
        targets = [s for s in manager.streamers
                   if f"{s.username}|{s.siteslug}" in usernames or s.username in usernames]
    else:
        targets = list(manager.streamers)
    changed, errors = _set_running(manager, targets, False)
    return _json({'changed': changed, 'errors': errors}, 200 if not errors else 207)


# --- library -------------------------------------------------------------

def _folder_payload(row, known=None):
    username = row['username']
    site = row['site']
    poster = None
    newest = db.query_one(
        "SELECT path, duration, display_w, display_h, sprite_cols, sprite_rows,"
        " cell_w, cell_h FROM media WHERE username = ? AND site = ?"
        " ORDER BY mtime DESC LIMIT 1", (username, site))
    if newest is not None:
        art = db.get_artifact(newest['path'], 'poster')
        if art is not None and os.path.exists(db.artifact_path(art)):
            # The URL of the newest recording's poster, so the client does not
            # have to know about the cache layout.
            poster = f"/api/v2/videos/{_encode_path(newest['path'])}/poster"
        sprite = db.get_artifact(newest['path'], 'sprite')
        sprite_url = f"/api/v2/videos/{_encode_path(newest['path'])}/sprite" if sprite else None
    else:
        sprite_url = None
        newest = {}
    return {
        'username': username,
        'site': site,
        'count': row['count'],
        'total_size': row['total_size'] or 0,
        'total_size_human': human_file_size(row['total_size'] or 0),
        'last_activity': row['last_activity'] or 0,
        'poster': poster,
        # The storyboard of the newest recording, the card of the folder
        # scrubs like a card of a recording.
        'duration': newest['duration'] if newest else None,
        'display_w': newest['display_w'] if newest else None,
        'display_h': newest['display_h'] if newest else None,
        'sprite_url': sprite_url,
        'sprite_cols': newest['sprite_cols'] if newest else None,
        'sprite_rows': newest['sprite_rows'] if newest else None,
        'cell_w': newest['cell_w'] if newest else None,
        'cell_h': newest['cell_h'] if newest else None,
        'recording_files': sorted(_recording_files(username, site)),
    }


@bp.route('/folders', methods=['GET'])
@login_required
def list_folders():
    """One entry per streamer folder that has recordings."""
    query = (request.args.get('q') or '').strip().lower()
    sort = request.args.get('sort') or 'last_activity'
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(200, max(1, request.args.get('per_page', 60, type=int)))

    rows = db.all_folders()
    if query:
        rows = [r for r in rows
                if query in r['username'].lower() or query in r['site'].lower()]
    if sort == 'name':
        rows.sort(key=lambda r: r['username'].lower())
    else:
        rows.sort(key=lambda r: r['last_activity'] or 0, reverse=True)

    total = len(rows)
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]

    # The payload costs several queries and a directory listing per folder,
    # so only the page that the client asked for is built.
    manager = _manager()
    recording = {(s.username, s.siteslug) for s in manager.streamers if s.recording}
    folders = []
    for row in page_rows:
        payload = _folder_payload(row)
        payload['recording'] = (payload['username'], payload['site']) in recording
        folders.append(payload)

    return _json({
        'folders': folders,
        'total': total,
        'page': page,
        'per_page': per_page,
    })


_SORTS = {
    'date': 'mtime',
    'name': 'name',
    'size': 'size',
    'duration': 'duration',
}


@bp.route('/folders/<user>/<site>/videos', methods=['GET'])
@login_required
def list_videos(user, site):
    """The recordings of one folder, sorted and paginated."""
    sort = request.args.get('sort') or 'date'
    direction = request.args.get('dir') or 'desc'
    favorites_only = request.args.get('favorites') in ('1', 'true', 'yes')
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(200, max(1, request.args.get('per_page', 40, type=int)))

    column = _SORTS.get(sort, 'mtime')
    order = 'ASC' if direction.lower() == 'asc' else 'DESC'
    if column == 'name':
        # Both tables have a path column, so the media one must be named.
        order_expr = "m.username || '/' || m.path"
    else:
        order_expr = f"COALESCE(m.{column}, 0)"

    where = "WHERE m.username = ? AND m.site = ?"
    params = [user, site]
    if favorites_only:
        where += " AND f.path IS NOT NULL"

    counted = db.query_one(f"""
        SELECT COUNT(*) AS total
        FROM media m
        LEFT JOIN favorites f ON f.path = m.path
        {where}
    """, tuple(params))
    total = counted['total'] if counted else 0

    start = (page - 1) * per_page
    rows = db.query(f"""
        SELECT m.*, (f.path IS NOT NULL) AS favorite
        FROM media m
        LEFT JOIN favorites f ON f.path = m.path
        {where}
        ORDER BY {order_expr} {order}, m.path {order}
        LIMIT ? OFFSET ?
    """, tuple(params) + (per_page, start))

    videos = []
    recording_names = _recording_files(user, site)
    known = {row['path'] for row in rows}

    # A recording in progress has no metadata yet, so it is not in the
    # database. It is listed separately with a REC badge and no thumbnail.
    pending = []
    for name in recording_names:
        rel = os.path.join(f'{user} [{site}]', name)
        if rel in known:
            continue
        abs_path = os.path.join(DOWNLOADS_DIR, rel)
        try:
            stat = os.stat(abs_path)
        except OSError:
            continue
        encoded = _encode_path(rel)
        pending.append({
            'path': rel,
            'filename': name,
            'username': user,
            'site': site,
            'size': stat.st_size,
            'size_human': human_file_size(stat.st_size),
            'mtime': stat.st_mtime,
            'duration': None,
            'width': None,
            'height': None,
            'favorite': db.is_favorite(rel),
            'has_poster': False,
            'has_sprite': False,
            'has_proxy': False,
            'recording': True,
            'poster_url': None,
            'sprite_url': None,
            'sprite_cols': None,
            'sprite_rows': None,
            'cell_w': None,
            'cell_h': None,
            'display_w': None,
            'display_h': None,
            'proxy_url': None,
            'stream_url': f'/api/v2/videos/{encoded}/stream',
        })
    total += len(pending)

    for row in rows:
        poster = db.get_artifact(row['path'], 'poster')
        sprite = db.get_artifact(row['path'], 'sprite')
        proxy = db.get_artifact(row['path'], 'proxy')
        encoded = _encode_path(row['path'])
        name = os.path.basename(row['path'])
        videos.append({
            'path': row['path'],
            'filename': name,
            'username': row['username'],
            'site': row['site'],
            'size': row['size'],
            'size_human': human_file_size(row['size']),
            'mtime': row['mtime'],
            'duration': row['duration'],
            'width': row['width'],
            'height': row['height'],
            'favorite': bool(row['favorite']),
            'has_poster': poster is not None,
            'has_sprite': sprite is not None,
            'has_proxy': proxy is not None,
            'recording': name in recording_names,
            'display_w': row['display_w'],
            'display_h': row['display_h'],
            'sprite_cols': row['sprite_cols'],
            'sprite_rows': row['sprite_rows'],
            'cell_w': row['cell_w'],
            'cell_h': row['cell_h'],
            # Ready to use urls, so the client needs no knowledge of the cache.
            'poster_url': f'/api/v2/videos/{encoded}/poster' if poster else None,
            'sprite_url': f'/api/v2/videos/{encoded}/sprite' if sprite else None,
            'proxy_url': f'/api/v2/videos/{encoded}/proxy' if proxy else None,
            'stream_url': f'/api/v2/videos/{encoded}/stream',
        })

    # The recording in progress is always first.
    videos = pending + videos

    return _json({
        'videos': videos,
        'total': total,
        'page': page,
        'per_page': per_page,
        'sort': sort,
        'dir': direction.lower(),
    })


@bp.route('/videos/<path:rel_path>', methods=['GET'])
@login_required
def get_video_info(rel_path):
    row = db.query_one("SELECT * FROM media WHERE path = ?", (rel_path,))
    if row is None:
        return _json({'error': 'Not found'}, 404)
    return _json({
        'path': row['path'],
        'filename': os.path.basename(row['path']),
        'size': row['size'],
        'size_human': human_file_size(row['size']),
        'mtime': row['mtime'],
        'duration': row['duration'],
        'width': row['width'],
        'height': row['height'],
        'display_w': row['display_w'],
        'display_h': row['display_h'],
        'sprite_cols': row['sprite_cols'],
        'sprite_rows': row['sprite_rows'],
        'cell_w': row['cell_w'],
        'cell_h': row['cell_h'],
        'favorite': db.is_favorite(row['path']),
    })


@bp.route('/videos/<path:rel_path>', methods=['DELETE'])
@login_required
@csrf_protect
def delete_video(rel_path):
    """Delete the recording, its artifacts and its favourite entry.

    Only the recordings may be deleted. The cache with the database and the
    artifacts belongs to the application, not to the user of the web
    interface.
    """
    try:
        abs_path = safe_join(DOWNLOADS_DIR, rel_path)
    except UnsafePathError as e:
        return _json({'error': str(e)}, 400)

    # The cache holds the database and the artifacts of the application.
    cache = os.path.abspath(CACHE_DIR)
    if os.path.abspath(abs_path).startswith(cache + os.sep) or \
            os.path.abspath(abs_path) == cache:
        return _json({'error': 'This file does not belong to the recordings'}, 400)

    # Only the recordings themselves.
    if os.path.splitext(rel_path)[1][1:].lower() not in media_manager.VIDEO_EXTENSIONS:
        return _json({'error': 'Only a recording can be deleted'}, 400)

    row = db.query_one("SELECT * FROM media WHERE path = ?", (rel_path,))
    if row is None and not os.path.isfile(abs_path):
        return _json({'error': 'Not found'}, 404)

    # A file that is still being written cannot be removed: the recorder
    # would go on writing into a file that nobody sees any more.
    try:
        if time.time() - os.path.getmtime(abs_path) < media_manager.STABLE_AFTER:
            return _json({'error': 'The recording is still running, stop it first'}, 409)
    except OSError:
        pass

    try:
        os.unlink(abs_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        return _json({'error': str(e)}, 500)

    for artifact in db.list_artifacts(rel_path):
        try:
            os.unlink(db.artifact_path(artifact))
        except OSError:
            pass
    db.delete_media(rel_path)

    # Refresh the in-memory cache of the owning streamer.
    if row is not None:
        manager = _manager()
        streamer = manager.getStreamer(row['username'], row['site'])
        if streamer is not None:
            try:
                streamer.cache_file_list()
            except Exception:
                pass

    return _json({'result': 'OK'})


@bp.route('/videos/<path:rel_path>/stream', methods=['GET'])
@login_required
def stream_video(rel_path):
    """Serve the original file. Werkzeug handles Range requests (206)."""
    try:
        abs_path = safe_join(DOWNLOADS_DIR, rel_path)
    except UnsafePathError as e:
        return _json({'error': str(e)}, 400)
    if not os.path.isfile(abs_path):
        return _json({'error': 'Not found'}, 404)
    return send_file(abs_path, conditional=True)


@bp.route('/videos/<path:rel_path>/proxy', methods=['GET'])
@login_required
def stream_proxy(rel_path):
    """Serve the 360p proxy when one exists."""
    artifact = db.get_artifact(rel_path, 'proxy')
    if artifact is None or not os.path.isfile(db.artifact_path(artifact)):
        return _json({'error': 'No proxy available'}, 404)
    return send_file(db.artifact_path(artifact), conditional=True)


@bp.route('/videos/<path:rel_path>/poster', methods=['GET'])
@login_required
def get_poster(rel_path):
    artifact = db.get_artifact(rel_path, 'poster')
    if artifact is None or not os.path.isfile(db.artifact_path(artifact)):
        return _json({'error': 'No poster available'}, 404)
    response = send_file(db.artifact_path(artifact), conditional=True, max_age=86400,
                         mimetype='image/jpeg')
    response.headers['Cache-Control'] = 'private, max-age=86400'
    return response


def _sprite_response(artifact):
    """Serve a sprite with a strong validator.

    The sprite is large, the browser should only fetch it again when the
    recording changed.
    """
    absolute = db.artifact_path(artifact)
    etag = f'"{int(artifact["src_mtime"] or 0):x}-{artifact["src_size"] or 0:x}"'
    response = send_file(absolute, conditional=True, max_age=86400,
                         mimetype='image/webp')
    response.headers['Cache-Control'] = 'private, max-age=86400'
    response.headers['ETag'] = etag
    response.headers['Vary'] = 'Accept-Encoding'
    return response


@bp.route('/videos/<path:rel_path>/sprite', methods=['GET'])
@login_required
def get_sprite(rel_path):
    """The storyboard of the recording: one file, a frame every 5%."""
    artifact = db.get_artifact(rel_path, 'sprite')
    if artifact is None or not os.path.isfile(db.artifact_path(artifact)):
        return _json({'error': 'No sprite available'}, 404)
    # The fallback is a JPEG, the browser is told what it got.
    if artifact['file'].endswith('.jpg'):
        response = send_file(db.artifact_path(artifact), conditional=True,
                             max_age=86400, mimetype='image/jpeg')
        response.headers['Cache-Control'] = 'private, max-age=86400'
        response.headers['ETag'] = f'"{int(artifact["src_mtime"] or 0):x}-{artifact["src_size"] or 0:x}"'
        return response
    return _sprite_response(artifact)


# --- favorites -----------------------------------------------------------

@bp.route('/favorites', methods=['GET'])
@login_required
def list_favorites():
    """Every starred recording, newest first."""
    rows = db.query("""
        SELECT m.*, f.added_at AS added_at
        FROM favorites f
        JOIN media m ON m.path = f.path
        ORDER BY f.added_at DESC
    """)
    videos = []
    for row in rows:
        encoded = _encode_path(row['path'])
        poster = db.get_artifact(row['path'], 'poster')
        sprite = db.get_artifact(row['path'], 'sprite')
        proxy = db.get_artifact(row['path'], 'proxy')
        videos.append({
            'path': row['path'],
            'filename': os.path.basename(row['path']),
            'username': row['username'],
            'site': row['site'],
            'size': row['size'],
            'size_human': human_file_size(row['size']),
            'mtime': row['mtime'],
            'duration': row['duration'],
            'width': row['width'],
            'height': row['height'],
            'favorite': True,
            'has_poster': poster is not None,
            'has_sprite': sprite is not None,
            'has_proxy': proxy is not None,
            'display_w': row['display_w'],
            'display_h': row['display_h'],
            'sprite_cols': row['sprite_cols'],
            'sprite_rows': row['sprite_rows'],
            'cell_w': row['cell_w'],
            'cell_h': row['cell_h'],
            'poster_url': f'/api/v2/videos/{encoded}/poster' if poster else None,
            'sprite_url': f'/api/v2/videos/{encoded}/sprite' if sprite else None,
            'proxy_url': f'/api/v2/videos/{encoded}/proxy' if proxy else None,
            'stream_url': f'/api/v2/videos/{encoded}/stream',
        })
    return _json({'videos': videos, 'count': len(videos)})


@bp.route('/favorites/<path:rel_path>', methods=['POST'])
@login_required
@csrf_protect
def toggle_favorite(rel_path):
    """Star or unstar a recording."""
    row = db.query_one("SELECT path FROM media WHERE path = ?", (rel_path,))
    if row is None:
        return _json({'error': 'Not found'}, 404)
    state = db.toggle_favorite(rel_path)
    return _json({'path': rel_path, 'favorite': state})


# --- meta ----------------------------------------------------------------

def _free_space():
    usage = OOSDetector.space_usage()
    return {
        'percentage': str(round(usage.free / usage.total * 100, 3)) if usage.total else '0',
        'absolute': human_file_size(usage.free),
        'total': human_file_size(usage.total),
    }


def _encode_path(rel_path):
    """Percent-encode each segment of a relative path for use in a URL."""
    return '/'.join(quote(part, safe='') for part in rel_path.split('/'))


def _recording_files(username, site, known=None):
    """Names of the files that are still being written.

    A recording in progress is not in the database yet, so it is the file that
    is on disk but unknown. Those get a placeholder instead of a thumbnail.
    """
    folder = os.path.join(DOWNLOADS_DIR, f'{username} [{site}]')
    if not os.path.isdir(folder):
        return set()
    if known is None:
        known = {row['path'] for row in db.list_media(username, site)}
    names = set()
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return names
    for entry in entries:
        if not entry.is_file():
            continue
        # Only the recordings, not the helper files of the downloader.
        if os.path.splitext(entry.name)[1][1:].lower() not in media_manager.VIDEO_EXTENSIONS:
            continue
        rel = os.path.join(f'{username} [{site}]', entry.name)
        if rel not in known:
            names.add(entry.name)
    return names


@bp.route('/meta', methods=['GET'])
@login_required
def get_meta():
    """Sites, statuses and the settings the UI needs."""
    sites = {site.siteslug: site.site for site in LOADED_SITES}
    statuses = {}
    for status in Status:
        statuses[status.value] = Bot.status_messages.get(status, str(status))
    return _json({
        'sites': sites,
        'statuses': statuses,
        'filterable_status': {k: v.value for k, v in FILTERABLE_STATUS.items()},
        'free_space': _free_space(),
        'settings': {
            'skin': WEBSERVER_SKIN,
            'container': CONTAINER,
            'list_frequency': WEB_LIST_FREQUENCY,
            'thumbs': THUMBS_ENABLED,
            'scrub': SCRUB_ENABLED,
            'scrub_zone': SCRUB_ZONE,
            'proxy': PROXY_ENABLED,
            'auth_required': bool(WEBSERVER_PASSWORD),
        },
        'server_time': time.time(),
    })
