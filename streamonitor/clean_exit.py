import time
import signal
from threading import Event, Thread


# Set when the application is stopped, so that the main thread can leave its
# wait and the interpreter can shut down normally.
SHUTDOWN_EVENT = Event()


class CleanExit:
    class DummyThread(Thread):
        def __init__(self):
            super().__init__()
            self._stop = False

        def run(self):
            while True:
                if self._stop:
                    return
                time.sleep(1)

        def stop(self):
            self._stop = True

    dummy_thread = DummyThread()

    def __init__(self, streamers):
        self.streamers = streamers
        if not self.dummy_thread.is_alive():
            self.dummy_thread.start()
            signal.signal(signal.SIGINT, self.clean_exit)
            signal.signal(signal.SIGTERM, self.clean_exit)
            signal.signal(signal.SIGABRT, self.clean_exit)

    def __call__(self, *args, **kwargs):
        self.clean_exit()

    def clean_exit(self, _=None, __=None):
        for streamer in self.streamers:
            streamer.stop(None, None, True)
        for streamer in self.streamers:
            while streamer.is_alive():
                time.sleep(1)
        self.dummy_thread.stop()
        # Let the main thread know that it can return.
        SHUTDOWN_EVENT.set()
