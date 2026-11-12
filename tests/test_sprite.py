"""Acceptance tests of the scrub storyboard (stage 2).

They build real sprites with the real ffmpeg from generated samples: a
landscape recording, a portrait one and one that carries a rotation.
"""
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parameters import FFMPEG_PATH, FFPROBE_PATH


def make_clip(path, width, height, seconds, rotation=None):
    """A test sample: a moving pattern, so the frames differ."""
    args = [FFMPEG_PATH, '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', f'testsrc2=size={width}x{height}:rate=25:duration={seconds}',
            '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}']
    args += ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '40',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-shortest', path]
    subprocess.run(args, capture_output=True, check=True)


def probe(path):
    out = subprocess.run(
        [FFPROBE_PATH, '-v', 'error', '-print_format', 'json',
         '-show_streams', '-show_format', path],
        capture_output=True, text=True)
    import json
    return json.loads(out.stdout)


def read_media_row(db, rel):
    return db.query_one('SELECT * FROM media WHERE path = ?', (rel,))


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    """The media manager with a clean cache and three samples."""
    import parameters
    from streamonitor.managers.httpmanager import db, media_manager

    downloads = tmp_path / 'downloads'
    cache = tmp_path / 'cache'
    downloads.mkdir()
    cache.mkdir()
    monkeypatch.setattr(parameters, 'DOWNLOADS_DIR', str(downloads))
    monkeypatch.setattr(parameters, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(db, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(media_manager, 'CACHE_DIR', str(cache))
    monkeypatch.setattr(media_manager, 'STABLE_AFTER', 0.0)

    db.close()
    db._db_path = None
    db._local = type(db._local)()
    db.init()

    samples = [
        ('landscape', 'landscape.mp4', 1920, 1080, 130, None),
        ('portrait', 'portrait.mp4', 1080, 1920, 130, None),
        ('tiny', 'tiny.mp4', 640, 360, 0.4, None),
    ]
    paths = {}
    for name, filename, width, height, seconds, rotation in samples:
        folder = downloads / f'user [{name.upper()}]'
        folder.mkdir()
        path = str(folder / filename)
        make_clip(path, width, height, seconds, rotation)
        paths[name] = (path, f'user [{name.upper()}]/{filename}')

    manager = media_manager.MediaManager()
    yield manager, downloads, cache, paths
    manager.shutdown()


def run_pipeline(manager, downloads, rel, username, site):
    from streamonitor.managers.httpmanager import db
    path = os.path.join(downloads, rel)
    stat = os.stat(path)
    manager.enqueue(os.path.join(downloads, f'{username} [{site}]'), username, site, None)
    deadline = time.time() + 180
    while time.time() < deadline:
        if db.get_artifact(rel, 'sprite') is not None:
            break
        time.sleep(0.5)
    return path, stat


import time  # noqa: E402


# --- the sprite of a landscape recording ---------------------------------

def test_landscape_sprite_has_20_cells_5x4(pipeline):
    manager, downloads, cache, paths = pipeline
    from streamonitor.managers.httpmanager import db

    path, rel = paths['landscape']
    manager.enqueue(os.path.join(downloads, 'user [LANDSCAPE]'), 'user', 'LANDSCAPE', None)
    deadline = time.time() + 180
    while time.time() < deadline and db.get_artifact(rel, 'sprite') is None:
        time.sleep(0.5)

    artifact = db.get_artifact(rel, 'sprite')
    assert artifact is not None, 'the sprite was not built'

    file = db.artifact_path(artifact)
    assert os.path.isfile(file)
    # One file, one request: the whole storyboard.
    assert file.endswith('.sprite.webp') or file.endswith('.sprite.jpg')
    assert os.path.getsize(file) < 500 * 1024, os.path.getsize(file)

    row = read_media_row(db, rel)
    assert row['sprite_cols'] == 5
    assert row['sprite_rows'] == 4
    assert row['frames'] == 20
    # The file holds the grid: one cell per frame, no double tiling.
    import json as _json
    import subprocess as _sp
    _out = _sp.run([FFPROBE_PATH, '-v', 'error', '-print_format', 'json',
                    '-show_streams', file], capture_output=True, text=True)
    _stream = _json.loads(_out.stdout)['streams'][0]
    assert _stream['width'] == 5 * row['cell_w']
    assert _stream['height'] == 4 * row['cell_h']
    # A landscape recording: the cell keeps the proportions.
    assert row['cell_w'] == 320
    assert row['cell_h'] == 180
    # The display size is the stored one, no rotation, square pixels.
    assert row['display_w'] == 1920
    assert row['display_h'] == 1080


def test_portrait_sprite_keeps_the_proportions(pipeline):
    manager, downloads, cache, paths = pipeline
    from streamonitor.managers.httpmanager import db

    path, rel = paths['portrait']
    manager.enqueue(os.path.join(downloads, 'user [PORTRAIT]'), 'user', 'PORTRAIT', None)
    deadline = time.time() + 180
    while time.time() < deadline and db.get_artifact(rel, 'sprite') is None:
        time.sleep(0.5)

    row = read_media_row(db, rel)
    assert db.get_artifact(rel, 'sprite') is not None
    assert row['cell_w'] == 180
    assert row['cell_h'] == 320
    assert row['display_w'] == 1080
    assert row['display_h'] == 1920


def test_rotated_and_anamorphic_display_sizes_are_computed():
    """The rotation and the sample aspect ratio come from the metadata."""
    from streamonitor.managers.httpmanager.ffmpeg_utils import _display_size
    # A phone recording: the pixels are stored sideways.
    assert _display_size({
        'width': 1920, 'height': 1080,
        'sample_aspect_ratio': '1:1',
        'side_data_list': [{'rotation': -90}],
    })['display_w'] == 1080
    assert _display_size({
        'width': 1920, 'height': 1080,
        'sample_aspect_ratio': '1:1',
        'side_data_list': [{'rotation': -90}],
    })['display_h'] == 1920
    # Non square pixels: the width grows.
    anamorphic = _display_size({
        'width': 1440, 'height': 1080, 'sample_aspect_ratio': '4:3',
    })
    assert anamorphic['display_w'] == 1920
    assert anamorphic['display_h'] == 1080
    # Nothing to do.
    plain = _display_size({'width': 1280, 'height': 720, 'sample_aspect_ratio': '1:1'})
    assert plain == {'display_w': 1280, 'display_h': 720}


def test_too_short_recording_gets_no_sprite(pipeline):
    manager, downloads, cache, paths = pipeline
    from streamonitor.managers.httpmanager import db

    path, rel = paths['tiny']
    manager.enqueue(os.path.join(downloads, 'user [TINY]'), 'user', 'TINY', None)
    deadline = time.time() + 60
    while time.time() < deadline and db.get_artifact(rel, 'sprite') is None:
        time.sleep(0.5)

    # The poster still exists, the pipeline did not crash.
    assert db.get_artifact(rel, 'poster') is not None
    assert db.query_one('SELECT * FROM media WHERE path = ?', (rel,)) is not None


# --- the frame times ------------------------------------------------------

def test_frame_times_are_the_middle_of_their_interval():
    from streamonitor.managers.httpmanager.media_hooks import _frame_times, _grid
    times = _frame_times(100.0, 20)
    assert len(times) == 20
    assert times[0] == pytest.approx(2.5)
    assert times[10] == pytest.approx(52.5)
    assert times[19] == pytest.approx(97.5)
    assert _grid(20) == (5, 4)


def test_cell_sizes_are_even_and_keep_the_aspect():
    from streamonitor.managers.httpmanager.media_hooks import _cell_size
    assert _cell_size(1920, 1080) == (320, 180)
    assert _cell_size(1080, 1920) == (180, 320)
    # A square recording stays square.
    assert _cell_size(640, 640) == (320, 320)
    # Odd proportions are rounded to even numbers.
    for w, h in ((1900, 1060), (1280, 720), (720, 1280)):
        cw, ch = _cell_size(w, h)
        assert cw % 2 == 0 and ch % 2 == 0
        assert (cw / ch) == pytest.approx(w / h, rel=0.02)
