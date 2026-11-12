"""ffmpeg/ffprobe helpers for the media pipeline.

Everything degrades gracefully: when a hardware encoder is unavailable or
fails, the caller falls back to a CPU encode rather than losing the artifact.

The hardware detection is a real one-frame encode test, not just a check of
`ffmpeg -hwaccels`: the list of supported accelerators says nothing about
whether the driver is actually present and usable.
"""
import json
import os
import shutil
import subprocess
import tempfile
import threading

from parameters import FFMPEG_PATH, FFPROBE_PATH, HWACCEL, VAAPI_DEVICE

# The encoder/scaler pair used for each hardware backend.
#
# `input`   - decoder options that make ffmpeg deliver frames in a hardware
#              format
# `scale`   - the scaler filter that operates on those frames
# `encoder` - the hardware encoder
#
# The scaler must be able to read the format the decoder produces, so the two
# are always used together.
_HW_BACKENDS = {
    'nvenc': {
        'encoders': ['h264_nvenc'],
        'input': ['-hwaccel', 'cuda', '-hwaccel_output_format', 'cuda'],
        'scale': 'scale_cuda',
        'extra': [],
    },
    'vaapi': {
        'encoders': ['h264_vaapi'],
        'input': ['-hwaccel', 'vaapi', '-vaapi_device', VAAPI_DEVICE,
                  '-hwaccel_output_format', 'vaapi'],
        'scale': 'scale_vaapi',
        'extra': ['-vaapi_device', VAAPI_DEVICE],
    },
    'qsv': {
        'encoders': ['h264_qsv'],
        'input': ['-hwaccel', 'qsv', '-hwaccel_output_format', 'qsv'],
        'scale': 'scale_qsv',
        'extra': [],
    },
    'amf': {
        'encoders': ['h264_amf'],
        'input': [],
        'scale': None,
        'extra': [],
    },
    'videotoolbox': {
        'encoders': ['h264_videotoolbox'],
        'input': ['-hwaccel', 'videotoolbox'],
        'scale': None,
        'extra': [],
    },
}

_lock = threading.Lock()
_hw_cache = {}


def ffprobe_path():
    """ffprobe from the same directory as the configured ffmpeg."""
    if FFPROBE_PATH:
        return FFPROBE_PATH
    candidate = os.path.join(os.path.dirname(os.path.abspath(FFMPEG_PATH)), 'ffprobe')
    return candidate if os.path.exists(candidate) else 'ffprobe'


def probe(path):
    """Return {'duration', 'width', 'height'} for a media file, or None."""
    cmd = [
        ffprobe_path(), '-v', 'error', '-print_format', 'json',
        '-show_streams', '-show_format', path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    duration = None
    video = None
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video' and video is None:
            video = stream
    if video is not None:
        duration = video.get('duration')
        width = video.get('width')
        height = video.get('height')
    else:
        width = height = None

    if duration is None:
        try:
            duration = float(data.get('format', {}).get('duration'))
        except (TypeError, ValueError):
            duration = None

    if duration is None and width is None:
        return None

    result = {
        'duration': float(duration) if duration is not None else None,
        'width': int(width) if width else None,
        'height': int(height) if height else None,
    }
    result.update(_display_size(video or {}))
    return result


def _display_size(stream):
    """The size the player should show the video in.

    A recording from a phone is rotated and often stored with non-square
    pixels. The stored width and height are not what the user sees, the
    display matrix and the sample aspect ratio have to be applied.
    """
    width = stream.get('width')
    height = stream.get('height')
    if not width or not height:
        return {}

    try:
        num, den = (stream.get('sample_aspect_ratio') or '0:1').split(':')
        sar = float(num) / float(den) if float(den) else 1.0
    except (ValueError, ZeroDivisionError):
        sar = 1.0
    if not sar or sar <= 0:
        sar = 1.0

    # The rotation lives in the side data of the stream.
    rotation = 0
    for side in stream.get('side_data_list', []) or []:
        try:
            rotation = int(side.get('rotation') or 0)
        except (TypeError, ValueError):
            rotation = 0
    if rotation in (90, -270):
        width, height = height, width
    elif rotation in (270, -90):
        width, height = height, width

    return {
        'display_w': int(round(width * sar)) if sar != 1.0 else int(width),
        'display_h': int(height),
    }


def _supported_encoders():
    try:
        result = subprocess.run([FFMPEG_PATH, '-hide_banner', '-encoders'],
                                capture_output=True, text=True, timeout=20)
    except (subprocess.TimeoutExpired, OSError):
        return set()
    names = set()
    for line in (result.stdout or '').splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0][:1] in ('V', 'A'):
            names.add(parts[1])
    return names


def _test_encode(backend):
    """Actually encode one frame with the backend. Returns True on success.

    The test uses the same decoder options and scaler as the real pipeline,
    so a backend that only encodes but cannot scale is rejected.
    """
    spec = _HW_BACKENDS[backend]
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, 'test.mp4')
        cmd = [FFMPEG_PATH, '-hide_banner', '-loglevel', 'error', '-y']
        cmd += ['-f', 'lavfi', '-i', 'color=c=black:s=256x256:d=0.1']
        cmd += spec['input']
        if spec['scale']:
            cmd += ['-vf', f'{spec["scale"]}=w=256:h=256']
        cmd += ['-c:v', spec['encoders'][0]]
        if backend == 'vaapi':
            cmd += ['-vaapi_device', VAAPI_DEVICE]
        cmd += ['-frames:v', '1', '-f', 'mp4', out]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (subprocess.TimeoutExpired, OSError):
            return False
        return result.returncode == 0 and os.path.exists(out)


def detect_hwaccel():
    """Resolve STRMNTR_HWACCEL to a usable backend name, or None for CPU."""
    requested = (HWACCEL or 'auto').strip().lower()
    with _lock:
        if requested in _hw_cache:
            return _hw_cache[requested]

        result = None
        if requested == 'off':
            result = None
        elif requested in _HW_BACKENDS:
            result = requested if _backend_works(requested) else None
        else:  # auto
            for candidate in ('nvenc', 'vaapi', 'qsv', 'amf', 'videotoolbox'):
                if _backend_works(candidate):
                    result = candidate
                    break

        _hw_cache[requested] = result
        return result


def _backend_works(backend):
    spec = _HW_BACKENDS[backend]
    available = _supported_encoders()
    if not any(enc in available for enc in spec['encoders']):
        return False
    if backend == 'vaapi' and not os.path.exists(VAAPI_DEVICE):
        return False
    return _test_encode(backend)


def hwaccel_chain():
    """The decoder/scaler/encoder arguments for the detected backend.

    Returns a dict with 'input' (list), 'filter' (str or None), 'encoder' and
    'extra' (list). Falls back to libx264 when no backend works.
    """
    backend = detect_hwaccel()
    if backend is None:
        return {
            'backend': None,
            'input': [],
            'filter': None,
            'encoder': 'libx264',
            'extra': ['-preset', 'veryfast'],
        }

    spec = _HW_BACKENDS[backend]
    return {
        'backend': backend,
        'input': list(spec['input']),
        'filter': spec['scale'],
        'encoder': spec['encoders'][0],
        'extra': list(spec['extra']),
    }


def run_ffmpeg(args, timeout=600):
    """Run ffmpeg with the given arguments. Returns (ok, stderr)."""
    cmd = [FFMPEG_PATH, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y'] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, 'ffmpeg timed out'
    except OSError as e:
        return False, str(e)
    if result.returncode != 0:
        return False, (result.stderr or '').strip()[:500]
    return True, ''


def atomic_output(target):
    """Return a temporary path next to `target` plus a commit function.

    Artifacts are written to a .part file and renamed into place, so a reader
    never sees a half written file.
    """
    target_dir = os.path.dirname(os.path.abspath(target))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix='.part', dir=target_dir)
    os.close(fd)

    def commit():
        os.replace(tmp, target)

    def discard():
        try:
            os.unlink(tmp)
        except OSError:
            pass

    return tmp, commit, discard
