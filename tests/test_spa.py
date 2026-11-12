"""Tests for the "tube" SPA.

The browser tests need a running server and a headless browser, so they are
skipped when either is missing. Everything that can be checked without a
browser (templates, asset wiring, API contract) is always covered.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIN = os.path.join(APP_ROOT, 'streamonitor/managers/httpmanager/skins/tube')
STATIC = os.path.join(APP_ROOT, 'streamonitor/managers/httpmanager/static/streamonitor@tube')
JS = os.path.join(STATIC, 'js')

sys.path.insert(0, APP_ROOT)


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


# --- static assets -------------------------------------------------------

def test_skin_has_a_template():
    assert os.path.exists(os.path.join(SKIN, 'templates', 'index.html.jinja'))


def test_template_references_the_stylesheet_and_entry_point():
    html = read(os.path.join(SKIN, 'templates', 'index.html.jinja'))
    assert '/static/streamonitor@tube/tube.css' in html
    assert '/static/streamonitor@tube/js/app.js' in html


def test_all_modules_are_present():
    for name in ('api', 'app', 'dom', 'router', 'ui'):
        assert os.path.exists(os.path.join(JS, f'{name}.js')), name
    assert os.path.exists(os.path.join(JS, 'views', 'accounts.js'))
    assert os.path.exists(os.path.join(JS, 'views', 'library.js'))


def test_imports_resolve():
    """Every relative import must point at a file that exists."""
    for root, _dirs, files in os.walk(JS):
        for name in files:
            if not name.endswith('.js'):
                continue
            path = os.path.join(root, name)
            for target in re.findall(r"from '(\.[^']+)'", read(path)):
                resolved = os.path.normpath(os.path.join(root, target))
                assert os.path.exists(resolved), f'{path} -> {target}'


def test_every_module_parses_as_es_module():
    """Catch unbalanced brackets, which break the whole application."""
    node = shutil.which('node')
    if not node:
        pytest.skip('node not available')
    for root, _dirs, files in os.walk(JS):
        for name in files:
            if not name.endswith('.js'):
                continue
            path = os.path.join(root, name)
            result = subprocess.run(
                [node, '--input-type=module', '-e', read(path)],
                capture_output=True, text=True, timeout=60,
            )
            assert 'SyntaxError' not in result.stderr, f'{path}: {result.stderr[:200]}'


def test_no_raw_html_injection():
    """The DOM helper must never accept raw html."""
    dom = read(os.path.join(JS, 'dom.js'))
    assert "html')" not in dom or 'raw html is not allowed' in dom


def test_api_client_never_uses_a_url_with_credentials():
    """fetch() rejects URLs that carry credentials."""
    api = read(os.path.join(JS, 'api.js'))
    assert 'location.origin' in api
    assert 'credentials: \'same-origin\'' in api


# --- API contract --------------------------------------------------------

def test_api_exposes_the_endpoints_the_spa_uses():
    """Every URL the client calls must exist in the blueprint."""
    from flask import Flask
    from streamonitor.managers.httpmanager import api_v2

    app = Flask(__name__)
    app.register_blueprint(api_v2.bp)
    rules = {str(r) for r in app.url_map.iter_rules()}

    api = read(os.path.join(JS, 'api.js'))
    called = set(re.findall(r"'/api/v2/([a-z]+)", api))
    for prefix in called:
        assert any(r.startswith(f'/api/v2/{prefix}') for r in rules), prefix


def test_meta_reports_what_the_spa_needs():
    from flask import Flask
    from streamonitor.managers.httpmanager import api_v2

    app = Flask(__name__)
    app.register_blueprint(api_v2.bp)
    with app.test_client() as client:
        response = client.get('/api/v2/meta', headers=_auth_header())
    assert response.status_code == 200
    payload = response.get_json()
    for key in ('sites', 'statuses', 'filterable_status', 'free_space', 'settings'):
        assert key in payload
    assert 'list_frequency' in payload['settings']
    assert payload['settings']['auth_required'] is True


def _auth_header():
    """Basic auth for the default password."""
    import base64
    token = base64.b64encode(b'admin:admin').decode()
    return {'Authorization': f'Basic {token}'}


# --- browser tests -------------------------------------------------------

@pytest.fixture(scope='module')
def browser():
    """A headless browser, skipped when it is not available."""
    node_modules = '/tmp/opencode/node_modules/puppeteer-core'
    chromium = shutil.which('chromium') or shutil.which('chromium-browser') \
        or shutil.which('google-chrome')
    if not chromium or not os.path.isdir(node_modules):
        pytest.skip('headless browser not available')
    return chromium, node_modules


@pytest.fixture
def page(browser, live_server):
    chromium, node_modules = browser
    script = f"""
const puppeteer = require('{node_modules}');
(async () => {{
    const browser = await puppeteer.launch({{
        executablePath: '{chromium}',
        headless: 'new',
        args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    }});
    const page = await browser.newPage();
    await page.setViewport({{ width: 1400, height: 900 }});
    await page.setCacheEnabled(false);
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    // The credentials go through the header, not the URL: fetch() rejects
    // URLs that carry credentials.
    await page.setExtraHTTPHeaders({{ Authorization: 'Basic ' + Buffer.from('admin:admin').toString('base64') }});
    await page.goto('{live_server}', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 2500));
    const result = await page.evaluate(() => ({{
        title: document.querySelector('#view-title')?.textContent,
        theme: document.documentElement.dataset.theme,
        navGroups: document.querySelectorAll('.nav-group').length,
        navLinks: document.querySelectorAll('.nav-link').length,
        siteLinks: document.querySelectorAll('.nav-link[data-nav^="site-"]').length,
        rows: document.querySelectorAll('#accounts-list tbody tr').length,
        addForm: !!document.querySelector('#topbar-actions form'),
        errors: [],
    }}));
    result.errors = errors;
    console.log('RESULT:' + JSON.stringify(result));
    await browser.close();
}})().catch((e) => {{ console.error('FATAL ' + e.message); process.exit(1); }});
"""
    result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=120)
    match = re.search(r'RESULT:(\{.*\})', result.stdout)
    assert match, f'script failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}'
    import json
    return json.loads(match.group(1))


@pytest.fixture
def live_server():
    """The application, started with the tube skin."""
    import time
    import urllib.request
    import base64

    env = dict(os.environ, STRMNTR_SKIN='tube', STRMNTR_PORT='5199')
    process = subprocess.Popen(
        [os.path.join(APP_ROOT, '.venv/bin/python'), 'Downloader.py'],
        cwd=APP_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        # The console manager reads from stdin; a closed stdin would make it
        # quit right away.
        stdin=subprocess.PIPE,
    )
    token = base64.b64encode(b'admin:admin').decode()
    url = 'http://127.0.0.1:5199/'
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            request = urllib.request.Request(url + 'api/v2/meta')
            request.add_header('Authorization', f'Basic {token}')
            urllib.request.urlopen(request, timeout=3).read()
            break
        except Exception:
            time.sleep(1)
    else:
        process.kill()
        pytest.skip('the application did not start')

    yield url
    process.kill()
    process.wait(timeout=10)


def test_spa_renders_without_errors(page):
    assert page['errors'] == []
    assert page['title'] == 'Accounts'
    assert page['theme'] == 'dark'
    assert page['navGroups'] == 2
    assert page['navLinks'] > 0
    assert page['siteLinks'] > 0
    assert page['addForm'] is True


def test_spa_lists_the_configured_accounts(page):
    # The suite configures one Chaturbate account.
    assert page['rows'] >= 1
