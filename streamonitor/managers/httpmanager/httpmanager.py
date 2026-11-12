from flask import Flask, redirect, render_template, request, Response
import os
import logging

from parameters import WEBSERVER_HOST, WEBSERVER_PORT, WEBSERVER_SKIN
import streamonitor.log as log
from streamonitor.manager import Manager
from streamonitor.utils import human_file_size

from .auth import login_required as _login_required
# Imported here, not inside run(): the blueprint pulls in concurrent.futures,
# which must not be imported for the first time while the interpreter is
# already shutting down.
from .api_v2 import bp as api_v2_bp

# The running HTTPManager instance. The v2 API needs access to the streamer
# list, which lives on the manager.
_current_manager = None


def current_manager():
    return _current_manager


class HTTPManager(Manager):
    def __init__(self, streamers):
        super().__init__(streamers)
        self.logger = log.Logger("manager_http")

        # Only one skin is shipped. An unknown name falls back to it, so an
        # old value of STRMNTR_SKIN does not break the start.
        self.skin = WEBSERVER_SKIN
        if self.skin not in os.listdir(os.path.join(os.path.dirname(__file__), 'skins')):
            self.logger.warning(
                f"Unknown skin '{self.skin}', falling back to 'tube'")
            self.skin = 'tube'

    def icon_sprite(self):
        """The SVG sprite of the skin, inlined into the page.

        The sprite has to be part of the document: a browser cannot resolve a
        reference to another file in a CSS mask, so the icons would stay
        invisible.
        """
        path = os.path.join(os.path.dirname(__file__),
                            'static', f'streamonitor@{self.skin}', 'icons.svg')
        try:
            with open(path, encoding='utf-8') as handle:
                return handle.read()
        except OSError:
            return ''

    def asset_version(self):
        """A token that changes when the assets change.

        The stylesheet and the entry point are referenced with it, so the
        browser does not keep an old file after an update.
        """
        root = os.path.join(os.path.dirname(__file__),
                            'static', f'streamonitor@{self.skin}')
        newest = 0
        for base, _dirs, files in os.walk(root):
            for name in files:
                try:
                    newest = max(newest, os.path.getmtime(os.path.join(base, name)))
                except OSError:
                    continue
        return f'{int(newest):x}'

    def run(self):
        global _current_manager
        _current_manager = self

        app = Flask(
            __name__,
            template_folder=f'skins/{self.skin}/templates'
        )

        werkzeug_logger = logging.getLogger('werkzeug')
        werkzeug_logger.disabled = True

        # The stylesheets and the JavaScript modules are always revalidated:
        # an update of the code would otherwise be invisible until the cache
        # expired. An unchanged file is answered with 304, so the cost is one
        # request per file.
        @app.after_request
        def no_stale_assets(response):
            if request.path.startswith('/static/'):
                response.headers['Cache-Control'] = 'no-cache'
            return response

        # Shared with the v2 API blueprint.
        login_required = _login_required

        # The one JSON API of the application.
        app.register_blueprint(api_v2_bp)

        @app.route('/')
        @login_required
        def status():
            return render_template('index.html.jinja', icon_sprite=self.icon_sprite(),
                                   asset_version=self.asset_version())

        @app.route('/dashboard')
        @login_required
        def mainSite():
            return redirect('/')

        @app.route('/health')
        def health():
            """A cheap check for a reverse proxy or a container."""
            return Response('OK', mimetype='text/plain')

        # Metadata database and the periodic cache sweep.
        from . import db as media_db
        from .media_manager import start_sweeper
        try:
            media_db.init()
        except Exception as e:
            self.logger.warning(f"Could not open the media database: {e}")
        start_sweeper(self.logger)

        app.run(host=WEBSERVER_HOST, port=WEBSERVER_PORT)
