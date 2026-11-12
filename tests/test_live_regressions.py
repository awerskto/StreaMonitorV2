"""Regression tests for the bugs the live test uncovered.

1. `main()` returned immediately, so the interpreter started its shutdown
   phase and `concurrent.futures` stopped working -> the media pipeline never
   produced a thumbnail in the running application.
2. The gender was saved in the configuration as a plain number and was not
   turned back into the enum when the configuration was read -> the accounts
   API answered with HTTP 500 after a restart.
3. The helper file of the downloader (the local playlist) was listed as a
   recording in progress.
"""
import os
import subprocess
import sys
import threading
import time

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)


# --- 1. the interpreter must not shut down under the running application ---

def test_downloader_keeps_the_main_thread_alive():
    """`main()` returns, so the main thread has to wait afterwards.

    Otherwise the interpreter starts its shutdown phase, which disables
    concurrent.futures, and the media pipeline fails for every recording.
    """
    source = open(os.path.join(APP_ROOT, 'Downloader.py')).read()
    assert 'SHUTDOWN_EVENT.wait()' in source

    # The event must be set when the application is stopped.
    from streamonitor.clean_exit import SHUTDOWN_EVENT
    assert not SHUTDOWN_EVENT.is_set()


def test_clean_exit_releases_the_main_thread():
    from streamonitor.clean_exit import SHUTDOWN_EVENT

    class FakeStreamer:
        def stop(self, *args):
            pass

        def is_alive(self):
            return False

    from streamonitor.clean_exit import CleanExit
    CleanExit([FakeStreamer()])()
    assert SHUTDOWN_EVENT.is_set()
    # Reset it, the other tests expect a running application.
    SHUTDOWN_EVENT.clear()


def test_media_pipeline_can_queue_jobs_in_a_running_application():
    """The executor must accept work as long as the application runs."""
    from streamonitor.managers.httpmanager import media_manager

    manager = media_manager.MediaManager(hooks=[], workers=1)
    manager.start()
    try:
        future = manager._executor.submit(lambda: 'ok')
        assert future.result(timeout=5) == 'ok'
    finally:
        manager.shutdown()


# --- 2. the gender survives a restart --------------------------------------

def test_gender_is_restored_from_the_configuration():
    from streamonitor.sites import Chaturbate
    from streamonitor.enums import Gender

    instance = Chaturbate.fromConfig({'site': 'Chaturbate', 'username': 'x',
                                      'running': True, 'gender': 1})
    assert instance.gender is Gender.FEMALE
    # The number has to survive the next export.
    assert instance.export()['gender'] == 1


def test_unknown_gender_in_the_configuration_is_not_fatal():
    from streamonitor.sites import Chaturbate
    from streamonitor.enums import Gender

    instance = Chaturbate.fromConfig({'site': 'Chaturbate', 'username': 'x',
                                      'running': True, 'gender': 999})
    assert instance.gender is Gender.UNKNOWN


def test_streamers_endpoint_survives_a_numeric_gender(client):
    """The accounts API must not answer with HTTP 500."""
    from streamonitor.sites import Chaturbate

    streamer = Chaturbate.fromConfig({'site': 'Chaturbate', 'username': 'numeric',
                                      'running': True, 'gender': 1})
    assert streamer.gender.value == 1

    # The payload must work with a plain number too.
    from streamonitor.managers.httpmanager import api_v2
    payload = api_v2._streamer_payload(streamer)
    assert payload['gender'] == 1


# --- 3. the helper files are not recordings --------------------------------

def test_playlist_helper_file_is_not_a_recording(client, workdir):
    from streamonitor.managers.httpmanager import api_v2

    downloads = os.path.join(workdir, 'downloads')
    folder = os.path.join(downloads, 'a [CB]')
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, '.playlist.m3u8'), 'w') as handle:
        handle.write('#EXTM3U\n')

    data = client.get('/api/v2/folders/a/CB/videos').get_json()
    assert data['videos'] == []

    folders = client.get('/api/v2/folders').get_json()
    assert folders['folders'] == [] or folders['folders'][0]['recording_files'] == []
