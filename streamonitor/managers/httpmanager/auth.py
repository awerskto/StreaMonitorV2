"""HTTP Basic auth shared by every route that needs it.

This used to live as a closure inside `HTTPManager.run()`, which made it
impossible to reuse from a Blueprint. The behaviour is unchanged: when
WEBSERVER_PASSWORD is empty no authentication is required, otherwise the
credentials must be "admin" plus the configured password.
"""
from functools import wraps
from secrets import compare_digest

from flask import request

import parameters

# The header the single page application sends with every request. A request
# without it is not simple for the browser: the header cannot be added by a
# form or by an image, so a foreign page cannot make the browser send it.
X_REQUESTED_WITH = 'X-Requested-With'
X_REQUESTED_WITH_VALUE = 'strea'


def check_auth(username, password):
    if parameters.WEBSERVER_PASSWORD == "":
        return True
    return username == 'admin' and compare_digest(password, parameters.WEBSERVER_PASSWORD)


def login_required(f):
    @wraps(f)
    def wrapped_view(*args, **kwargs):
        if parameters.WEBSERVER_PASSWORD != "":
            auth = request.authorization
            if not (auth and check_auth(auth.username, auth.password)):
                return ('Unauthorized', 401, {
                    'WWW-Authenticate': 'Basic realm="Login Required"'
                })
        return f(*args, **kwargs)

    return wrapped_view


def csrf_protect(f):
    """Reject the requests that a foreign page could make.

    Basic authentication is sent by the browser on its own, so a page on
    another origin could start a streamer or delete a recording. Two checks
    stop that:

    * the request has to carry the header of the application, which a form
      or an image cannot set,
    * the origin, when the browser sends one, has to be the origin of the
      request.
    """
    @wraps(f)
    def wrapped_view(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            if request.headers.get(X_REQUESTED_WITH) != X_REQUESTED_WITH_VALUE:
                return ('Forbidden', 403, {
                    'X-Content-Type-Options': 'nosniff'
                })
            origin = request.headers.get('Origin')
            if origin:
                host = request.host_url.rstrip('/')
                if origin.rstrip('/') != host:
                    return ('Forbidden', 403, {
                        'X-Content-Type-Options': 'nosniff'
                    })
        return f(*args, **kwargs)

    return wrapped_view
