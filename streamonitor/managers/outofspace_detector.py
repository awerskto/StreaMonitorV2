import os.path
import shutil
from time import sleep
import streamonitor.log as log
from threading import Thread
from parameters import DOWNLOADS_DIR, MIN_FREE_DISK_PERCENT


class OOSDetector(Thread):
    under_threshold_message = 'Free space is under threshold.'

    def __init__(self, streamers):
        super().__init__()
        self.streamers = streamers
        self.daemon = True
        self.logger = log.Logger("out_of_space_detector")

    @staticmethod
    def free_space():
        usage = OOSDetector.space_usage()
        free_percent = usage.free / usage.total * 100
        return free_percent

    @staticmethod
    def space_usage():
        if os.path.exists(DOWNLOADS_DIR):
            usage = shutil.disk_usage(DOWNLOADS_DIR)
        else:
            usage = shutil.disk_usage('.')
        return usage

    @staticmethod
    def disk_space_good():
        return OOSDetector.free_space() > MIN_FREE_DISK_PERCENT

    def run(self):
        while True:
            if not self.disk_space_good():
                # Stopping the recording frees the space that the running
                # download would go on using. Closing the whole application
                # would take the web interface down with it, so the user
                # could not even see what filled the disk.
                self.logger.warning(self.under_threshold_message)
                for streamer in list(self.streamers):
                    if streamer.recording:
                        self.logger.warning(
                            f"[{streamer.siteslug}] {streamer.username}: "
                            "recording stopped, not enough free space")
                        try:
                            streamer.stop(None, None, True)
                        except Exception as e:
                            self.logger.error(e)
            sleep(5)
