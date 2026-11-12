"""Media hooks: the individual jobs that run after a recording finishes.

A hook takes a source file and produces one artifact. Hooks are registered in
a list so new ones can be added without touching the pipeline. Every hook
writes to a temporary file and renames it into place, and every hook returns
False instead of raising when it cannot produce its artifact.
"""
import os
import shutil
import subprocess
import tempfile
import time

from parameters import (
    CACHE_DIR, MIN_FREE_DISK_PERCENT, POST_RECORD_CMD, PROXY_ENABLED,
    PROXY_HEIGHT, SCRUB_ENABLED, SCRUB_FRAMES, SPRITE_CELL, SPRITE_QUALITY,
    THUMBS_ENABLED,
)

from . import db
from .ffmpeg_utils import atomic_output, hwaccel_chain, probe, run_ffmpeg

# A recording shorter than this is decoded in one pass, twenty process
# starts would cost more than the decoding.
_SINGLE_PASS_MAX = 120.0

# Poster dimensions
POSTER_WIDTH = 640
POSTER_HEIGHT = 360


def _relative_cache_path(rel_path):
    """Mirror the recordings layout inside the cache directory."""
    from parameters import CACHE_DIR
    return os.path.join(CACHE_DIR, rel_path + '.cache')


def _warn(job, message):
    """Log a warning, tolerating a job without a logger."""
    if job.log is not None:
        job.log.warning(message)


def hook_ffprobe(job):
    """Read duration and resolution into the database."""
    info = probe(job.abs_path)
    if info is None:
        return False
    job.duration = info['duration']
    job.display_w = info.get('display_w') or info['width']
    job.display_h = info.get('display_h') or info['height']
    db.upsert_media(
        job.rel_path, job.username, job.site, job.size, job.mtime,
        duration=info['duration'], width=info['width'], height=info['height'],
        display_w=job.display_w, display_h=job.display_h)
    return True


def hook_poster(job):
    """A single JPEG frame, taken ~10% into the recording."""
    if not THUMBS_ENABLED:
        return False

    duration = job.duration
    offset = 0
    if duration and duration > 1:
        offset = max(0.0, duration * 0.1)

    target = _relative_cache_path(job.rel_path)
    target = os.path.splitext(target)[0] + '.jpg'
    tmp, commit, discard = atomic_output(target)

    chain = hwaccel_chain()
    scale = f'scale={POSTER_WIDTH}:{POSTER_HEIGHT}:force_original_aspect_ratio=decrease,pad={POSTER_WIDTH}:{POSTER_HEIGHT}:-1:-1'
    vf = f'{chain["filter"]},{scale}' if chain['filter'] else scale

    args = ['-ss', f'{offset:.3f}']
    args += chain['input']
    args += ['-i', job.abs_path, '-frames:v', '1', '-vf', vf,
             '-q:v', '3', '-f', 'image2', tmp]

    ok, err = run_ffmpeg(args, timeout=180)
    if not ok:
        discard()
        _warn(job, f"poster failed for {job.rel_path}: {err}")
        return False

    try:
        size = os.path.getsize(tmp)
        commit()
    except OSError as e:
        discard()
        _warn(job, f"poster failed for {job.rel_path}: {e}")
        return False

    db.record_artifact(job.rel_path, 'poster', target, size, os.path.getmtime(target),
                       job.size, job.mtime)
    return True


def _cell_size(display_w, display_h):
    """The size of one cell of the sprite.

    The longer side has the configured length, the proportions of the video
    are kept. Both sides are even, the encoders like that.
    """
    if not display_w or not display_h:
        return None
    aspect = display_w / display_h
    if aspect >= 1:
        w = SPRITE_CELL
        h = int(round(w / aspect))
    else:
        h = SPRITE_CELL
        w = int(round(h * aspect))
    # Even dimensions.
    return max(2, w - w % 2), max(2, h - h % 2)


def _frame_times(duration, frames):
    """The middle of every 1/frames part of the recording.

    The very beginning and the very end of a recording are often black, so
    the frames come from the middle of their interval: with 20 frames the
    first one stands for the first 5% and the last one for the last 5%.
    """
    if not duration or duration <= 0:
        return []
    return [(k + 0.5) * duration / frames for k in range(frames)]


def _grid(frames):
    """Columns and rows of the sprite. Five columns, the rest in rows."""
    cols = 5
    rows = (frames + cols - 1) // cols
    return cols, rows


def _sprite_target(job):
    """Where the sprite of a recording lives, relative to the cache."""
    target = _relative_cache_path(job.rel_path)
    return os.path.splitext(target)[0] + '.sprite.webp'


def _encode_sprite(job, input_args):
    """Write the sprite file. The webp encoder is preferred.

    `input_args` carries the input and the filters, this function adds the
    encoder settings.
    """
    target = _sprite_target(job)
    tmp, commit, discard = atomic_output(target)

    args = list(input_args) + ['-frames:v', '1', '-c:v', 'libwebp', '-lossless', '0',
                               '-q:v', str(SPRITE_QUALITY), '-an', '-f', 'webp', tmp]
    ok, err = run_ffmpeg(args, timeout=300)
    if not ok:
        # Not every build carries the webp encoder, the sprite falls back to
        # a JPEG. The interface is told by the file name.
        discard()
        target = os.path.splitext(target)[0] + '.sprite.jpg'
        tmp, commit, discard = atomic_output(target)
        args = list(input_args) + ['-frames:v', '1', '-q:v', '5', '-f', 'image2', tmp]
        ok, err = run_ffmpeg(args, timeout=300)
        if not ok:
            discard()
            _warn(job, f"sprite failed for {job.rel_path}: {err}")
            return None

    try:
        size = os.path.getsize(tmp)
        commit()
    except OSError as e:
        discard()
        _warn(job, f"sprite failed for {job.rel_path}: {e}")
        return None
    return target, size


def _sprite_single_pass(job, cell_w, cell_h, cols, rows, rate):
    """Decode the recording once and tile the frames on the fly.

    Cheaper than twenty process starts for a short recording.
    """
    chain = hwaccel_chain()
    vf = f'fps={rate:.6f},scale={cell_w}:{cell_h},tile={cols}x{rows}'
    if chain['filter']:
        vf = f'{chain["filter"]},{vf}'
    args = chain['input'] + ['-i', job.abs_path, '-vf', vf]
    return _encode_sprite(job, args)


def _sprite_by_seeks(job, times, cell_w, cell_h, cols, rows):
    """Seek to every frame and assemble the file afterwards.

    A long recording is not decoded from the beginning, only the frames that
    are used are read.
    """
    folder = tempfile.mkdtemp(prefix='sm-sprite-')
    pattern = os.path.join(folder, 'cell-%03d.jpg')
    try:
        chain = hwaccel_chain()
        for index, when in enumerate(times):
            args = ['-ss', f'{when:.3f}']
            args += chain['input']
            args += ['-i', job.abs_path, '-frames:v', '1',
                     '-vf', f'scale={cell_w}:{cell_h}', '-q:v', '3',
                     '-f', 'image2', pattern % index]
            ok, err = run_ffmpeg(args, timeout=120)
            if not ok:
                _warn(job, f"frame {index} of {job.rel_path} failed: {err}")
                return None
        vf = f'tile={cols}x{rows}'
        args = ['-pattern_type', 'glob', '-i', os.path.join(folder, 'cell-*.jpg'), '-vf', vf]
        return _encode_sprite(job, args)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def hook_sprite(job):
    """The storyboard for scrubbing: one file with a frame every 5%."""
    if not SCRUB_ENABLED or not THUMBS_ENABLED:
        return False
    if not job.duration or job.duration < 1:
        return False

    frames = max(1, min(60, SCRUB_FRAMES))
    cols, rows = _grid(frames)
    times = _frame_times(job.duration, frames)
    if not times:
        return False

    cell = _cell_size(job.display_w, job.display_h)
    if cell is None:
        return False
    cell_w, cell_h = cell

    if job.duration <= _SINGLE_PASS_MAX:
        built = _sprite_single_pass(job, cell_w, cell_h, cols, rows,
                                    len(times) / job.duration)
    else:
        built = _sprite_by_seeks(job, times, cell_w, cell_h, cols, rows)
    if built is None:
        return False
    target, size = built

    db.record_artifact(job.rel_path, 'sprite', target, size, os.path.getmtime(target),
                       job.size, job.mtime)
    # The geometry of the sprite. The recording may have no row yet, so it
    # is an upsert and not an update.
    db.execute("""
        INSERT INTO media (path, username, site, size, mtime, duration,
                           display_w, display_h, sprite_cols, sprite_rows,
                           cell_w, cell_h, frames, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            duration = COALESCE(excluded.duration, media.duration),
            display_w = COALESCE(excluded.display_w, media.display_w),
            display_h = COALESCE(excluded.display_h, media.display_h),
            sprite_cols = excluded.sprite_cols,
            sprite_rows = excluded.sprite_rows,
            cell_w = excluded.cell_w,
            cell_h = excluded.cell_h,
            frames = excluded.frames
    """, (job.rel_path, job.username, job.site, job.size, job.mtime, job.duration,
          job.display_w, job.display_h, cols, rows, cell_w, cell_h, len(times),
          time.time()))
    return True


def hook_proxy(job):
    """An optional 360p H.264+AAC proxy with faststart."""
    if not PROXY_ENABLED:
        return False

    # A proxy of a long recording takes hundreds of megabytes. The cache
    # lives next to the recordings, so filling the disk up would stop the
    # whole application.
    try:
        usage = shutil.disk_usage(CACHE_DIR)
        free_percent = 100.0 * usage.free / usage.total if usage.total else 0
    except OSError:
        free_percent = None
    if free_percent is not None and free_percent < MIN_FREE_DISK_PERCENT:
        _warn(job, f"proxy skipped for {job.rel_path}: only {free_percent:.1f}% free")
        return False

    target = _relative_cache_path(job.rel_path)
    target = os.path.splitext(target)[0] + '.proxy.mp4'
    tmp, commit, discard = atomic_output(target)

    chain = hwaccel_chain()
    args = list(chain['input'])
    args += ['-i', job.abs_path]
    vf = f"scale=-2:{PROXY_HEIGHT}"
    if chain['filter']:
        vf = f'{chain["filter"]},{vf}'
    args += ['-vf', vf, '-c:v', chain['encoder']]
    if chain['backend'] is None:
        args += ['-preset', 'veryfast']
    args += ['-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', '-f', 'mp4', tmp]
    args += chain['extra']

    ok, err = run_ffmpeg(args, timeout=1800)
    if not ok:
        discard()
        _warn(job, f"proxy failed for {job.rel_path}: {err}")
        return False

    try:
        size = os.path.getsize(tmp)
        commit()
    except OSError as e:
        discard()
        _warn(job, f"proxy failed for {job.rel_path}: {e}")
        return False

    db.record_artifact(job.rel_path, 'proxy', target, size, os.path.getmtime(target),
                       job.size, job.mtime)
    return True


def hook_post_record_cmd(job):
    """Run the optional external command with the finished file as argument."""
    if not POST_RECORD_CMD:
        return False
    try:
        subprocess.run([POST_RECORD_CMD, job.abs_path], timeout=300,
                       capture_output=True, check=False)
    except (subprocess.TimeoutExpired, OSError) as e:
        _warn(job, f"post record command failed for {job.rel_path}: {e}")
        return False
    return True


# The pipeline runs these in order. ffprobe first: the other hooks need the
# duration to pick a good frame.
HOOKS = [
    hook_ffprobe,
    hook_poster,
    hook_sprite,
    hook_proxy,
    hook_post_record_cmd,
]
