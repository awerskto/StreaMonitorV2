import os
import os.path
import environ


env = environ.Env()
if os.path.exists('.env'):
    environ.Env.read_env('.env')


DOWNLOADS_DIR = env.str("STRMNTR_DOWNLOAD_DIR", "downloads")
MIN_FREE_DISK_PERCENT = env.float("STRMNTR_MIN_FREE_SPACE", 5.0)  # in %
DEBUG = env.bool("STRMNTR_DEBUG", False)

# The camsoda bot ignores this setting in favor of a chrome useragent generated with the fake-useragent library
HTTP_USER_AGENT = env.str("STRMNTR_USER_AGENT", "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:135.0) Gecko/20100101 Firefox/135.0")

# Local static FFmpeg build (n9.0.2, gpl) lives in ../ffmpeg next to this project.
# Falls back to whatever "ffmpeg" is found on PATH if the local build is missing.
_FFMPEG_BIN_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'ffmpeg', 'bin'))
_local_ffmpeg = os.path.join(_FFMPEG_BIN_DIR, 'ffmpeg')
_local_ffprobe = os.path.join(_FFMPEG_BIN_DIR, 'ffprobe')

# Specify the full path to the ffmpeg binary. By default, ffmpeg found on PATH is used.
FFMPEG_PATH = env.str("STRMNTR_FFMPEG_PATH", _local_ffmpeg if os.path.exists(_local_ffmpeg) else 'ffmpeg')

# Specify the full path to the ffprobe binary. By default, ffprobe found on PATH is used.
FFPROBE_PATH = env.str("STRMNTR_FFPROBE_PATH", _local_ffprobe if os.path.exists(_local_ffprobe) else 'ffprobe')

# Make sure ffprobe (used by ffmpeg-python) and other helpers resolve to the same build
if os.path.isdir(_FFMPEG_BIN_DIR) and _FFMPEG_BIN_DIR not in os.environ.get('PATH', '').split(os.pathsep):
    os.environ['PATH'] = _FFMPEG_BIN_DIR + os.pathsep + os.environ.get('PATH', '')

# You can enter a number to select a specific height.
# Use a huge number here and closest match to get the highest resolution variant
# Eg: 240, 360, 480, 720, 1080, 1440, 99999
WANTED_RESOLUTION = env.int("STRMNTR_RESOLUTION", 480)

# Specify match type when specified height
# Possible values: exact, exact_or_least_higher, exact_or_highest_lower, closest
# Beware of the exact policy. Nothing gets downloaded if the wanted resolution is not available
WANTED_RESOLUTION_PREFERENCE = env.str("STRMNTR_RESOLUTION_PREF", 'closest')

# Specify output container here
# Suggested values are 'mkv' or 'mp4'
CONTAINER = env.str("STRMNTR_CONTAINER", 'mp4')

# Add auto-generated VR format suffix to files
VR_FORMAT_SUFFIX = env.bool("STRMNTR_VR_FORMAT_SUFFIX", True)

# Set ffmpeg readrate to whatever works for you.
# Usually this should be either 0, 1 or 1.3 depending on the network
# Setting it to 0 can result in very fragmented recordings.
# 1 can result in skipped segments
# 1.3 should be the sweet spot but use what works
FFMPEG_READRATE = env.int("STRMNTR_FFMPEG_READRATE", 1.3)

# Specify the segment time in seconds
# If None, the video will be downloaded as a single file
# Example:
# 5 minutes
# SEGMENT_TIME = 300
# 1 hour
# SEGMENT_TIME = 3600
# Also see the ffmpeg documentation for the segment_time option
# You can specify time in hh:mm:ss format
# Example:
# 1 hour
# SEGMENT_TIME = '1:00:00'
SEGMENT_TIME = env.str("STRMNTR_SEGMENT_TIME", None)

# How often (in seconds) the local master playlist is refreshed while a
# recording is running. Chaturbate and other LL-HLS sites embed a short
# lived session token in the playlist URLs; when it expires the recording
# stalls with HTTP 403 and the audio/video renditions drift apart.
# Set to 0 to disable refreshing.
PLAYLIST_REFRESH_INTERVAL = env.int("STRMNTR_PLAYLIST_REFRESH", 120)

# HTTP Manager configuration

# Bind address for the web server
# 0.0.0.0 for remote access from all host
WEBSERVER_HOST = env.str("STRMNTR_HOST", "0.0.0.0")
WEBSERVER_PORT = env.int("STRMNTR_PORT", 5090)

# Web UI skin
# Available options:
# The skin of the web interface. One skin ships with the application: the
# single page application "tube".
WEBSERVER_SKIN = env.str("STRMNTR_SKIN", "tube")

# set frequency in seconds of how often the streamer list will update
WEB_LIST_FREQUENCY = env.int("STRMNTR_LIST_FREQ", 30)

# Password for the web server
# If empty no auth required, else username admin and choosen password
WEBSERVER_PASSWORD = env.str("STRMNTR_PASSWORD", "admin")

# Media pipeline (thumbnails, previews, optional proxy)

# Generate poster images and animated previews after a recording finishes
THUMBS_ENABLED = env.bool("STRMNTR_THUMBS", True)

# Number of parallel workers for the media pipeline. Keep at 1 on small hosts;
# the pipeline is CPU bound (video decoding).
THUMB_WORKERS = env.int("STRMNTR_THUMB_WORKERS", 1)

# Hardware acceleration for the media pipeline.
# auto|off|nvenc|vaapi|qsv|amf|videotoolbox
HWACCEL = env.str("STRMNTR_HWACCEL", "auto")

# VAAPI render device, used when HWACCEL is vaapi (or auto picks vaapi)
VAAPI_DEVICE = env.str("STRMNTR_VAAPI_DEVICE", "/dev/dri/renderD128")

# Scrub storyboard: one sprite with a frame every 5% of the recording, the
# interface shows it when the pointer moves over the lower zone of a
# thumbnail.
SCRUB_ENABLED = env.bool("STRMNTR_SCRUB", True)

# How many frames the sprite holds. The grid is 5 columns, so 20 frames
# means 5x4. The frames are spread evenly over the duration.
SCRUB_FRAMES = env.int("STRMNTR_SCRUB_FRAMES", 20)

# The longer side of one cell of the sprite, in pixels. A landscape
# recording gets 320x180, a portrait one 180x320.
SPRITE_CELL = env.int("STRMNTR_SPRITE_CELL", 320)
SPRITE_QUALITY = env.int("STRMNTR_SPRITE_QUALITY", 75)

# The lower part of a thumbnail that reacts to the pointer, as a fraction of
# the height.
SCRUB_ZONE = env.float("STRMNTR_SCRUB_ZONE", 0.30)

# Generate a full 360p proxy of every recording. Off by default: it doubles
# the disk usage and the CPU time.
PROXY_ENABLED = env.bool("STRMNTR_PROXY", False)
PROXY_HEIGHT = env.int("STRMNTR_PROXY_HEIGHT", 360)

# Where artifacts (posters, previews, proxies) and the metadata database live.
# Defaults to DOWNLOADS_DIR/.cache so that everything stays on one volume.
CACHE_DIR = env.str("STRMNTR_CACHE_DIR", os.path.join(DOWNLOADS_DIR, ".cache"))

# How often (in minutes) the cache sweep runs, to clean up files that were
# removed outside the application.
CACHE_SWEEP_MIN = env.int("STRMNTR_CACHE_SWEEP_MIN", 15)

# Optional external command run after a recording finishes. The path of the
# finished file is passed as the first argument.
# Example: STRMNTR_POST_RECORD_CMD=/usr/local/bin/notify-recording.sh
POST_RECORD_CMD = env.str("STRMNTR_POST_RECORD_CMD", "")

