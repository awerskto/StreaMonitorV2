"""Browser tests of the library view: folders, player, sorting and favourites.

They need a running server with some recordings, so they are skipped when the
browser or the test media is not available.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_ROOT)

MEDIA = '/tmp/opencode/libtest'
NODE_MODULES = '/tmp/opencode/node_modules/puppeteer-core'


@pytest.fixture(scope='module')
def library_server():
    """The application with the tube skin and the test recordings."""
    chromium = shutil.which('chromium') or shutil.which('chromium-browser') \
        or shutil.which('google-chrome')
    if not chromium or not os.path.isdir(NODE_MODULES):
        pytest.skip('headless browser not available')
    if not os.path.isdir(os.path.join(MEDIA, 'downloads')):
        pytest.skip('test media not available')

    import base64
    import time
    import urllib.request

    # Use a copy, so the tests never touch the real downloads. The cache lives
    # inside the downloads directory, that is where the application looks.
    work = '/tmp/opencode/libtest-run'
    shutil.rmtree(work, ignore_errors=True)
    downloads = os.path.join(work, 'downloads')
    os.makedirs(downloads, exist_ok=True)
    shutil.copytree(os.path.join(MEDIA, 'downloads'), downloads, dirs_exist_ok=True)
    cache = os.path.join(MEDIA, 'cache')
    if os.path.isdir(cache):
        shutil.copytree(cache, os.path.join(downloads, '.cache'), dirs_exist_ok=True)

    env = dict(os.environ, STRMNTR_SKIN='tube', STRMNTR_PORT='5198',
               STRMNTR_DOWNLOAD_DIR=downloads)
    process = subprocess.Popen(
        [os.path.join(APP_ROOT, '.venv/bin/python'), 'Downloader.py'],
        cwd=APP_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.PIPE,
    )

    token = base64.b64encode(b'admin:admin').decode()
    url = 'http://127.0.0.1:5198/'
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

    yield {'url': url, 'chromium': chromium, 'downloads': downloads}

    process.kill()
    process.wait(timeout=10)
    shutil.rmtree(work, ignore_errors=True)


def run_browser(library_server, script):
    """Run a snippet in the browser and return the value it logs."""
    chromium = library_server['chromium']
    wrapped = f"""
const puppeteer = require('{NODE_MODULES}');
(async () => {{
    const browser = await puppeteer.launch({{
        executablePath: '{chromium}',
        headless: 'new',
        args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
    }});
    const page = await browser.newPage();
    await page.setViewport({{ width: 1500, height: 950 }});
    await page.setCacheEnabled(false);
    // The credentials go through the header, not the URL.
    await page.setExtraHTTPHeaders({{
        Authorization: 'Basic ' + Buffer.from('admin:admin').toString('base64'),
    }});
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    {script}
    console.log('RESULT:' + JSON.stringify({{ value: await result, errors }}));
    await browser.close();
}})().catch((e) => {{ console.error('FATAL ' + e.message); process.exit(1); }});
"""
    out = subprocess.run(['node', '-e', wrapped], capture_output=True, text=True, timeout=180)
    match = None
    for line in out.stdout.splitlines():
        if line.startswith('RESULT:'):
            match = json.loads(line[len('RESULT:'):])
    assert match, f'script failed:\n{out.stdout[-2000:]}\n{out.stderr[-2000:]}'
    return match


def test_library_shows_folders_with_posters(library_server):
    script = """
    await page.goto('{url}#/library', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3000));
    const result = await page.evaluate(() => ({{
        title: document.querySelector('#view-title')?.textContent,
        folders: [...document.querySelectorAll('.folder')].map((f) => f.querySelector('.folder-name')?.textContent),
        postersLoaded: [...document.querySelectorAll('.folder-thumb img')].every((i) => i.complete && i.naturalWidth > 0),
        count: document.querySelectorAll('.folder').length,
    }}));
    """.format(url=library_server['url'])
    out = run_browser(library_server, script)
    assert out['errors'] == []
    assert out['value']['title'] == 'Library'
    assert out['value']['count'] >= 2
    assert 'tester' in out['value']['folders']
    assert out['value']['postersLoaded'] is True


def test_opening_a_folder_loads_the_player(library_server):
    script = """
    await page.goto('{url}#/library/tester/CB', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3500));
    const result = await page.evaluate(() => {{
        const v = document.querySelector('.player-frame video');
        return {{
            hash: location.hash,
            title: document.querySelector('#view-title')?.textContent,
            cards: document.querySelectorAll('.video-card').length,
            src: v?.getAttribute('src'),
            poster: v?.getAttribute('poster'),
            playerTitle: document.querySelector('.player-title')?.textContent,
            durations: [...document.querySelectorAll('.video-thumb .duration')].map((d) => d.textContent),
            scrubbable: document.querySelectorAll('.video-thumb.scrubbable').length,
            previews: document.querySelectorAll('.video-thumb .preview').length,
        }};
    }});
    """.format(url=library_server['url'])
    out = run_browser(library_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['hash'] == '#/library/tester/CB'
    assert value['title'] == 'tester · CB'
    assert value['cards'] == 3
    assert value['src'] and '/stream' in value['src']
    assert value['poster'] and '/poster' in value['poster']
    assert value['playerTitle'].endswith('.mp4')
    assert len(value['durations']) == 3
    # The storyboard replaced the animated hover preview.
    assert value['scrubbable'] == 3
    assert value['previews'] == 0


def test_sorting_reorders_the_list(library_server):
    script = """
    await page.goto('{url}#/library/tester/CB', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3000));
    const result = await page.evaluate(async () => {{
        const select = document.querySelector('select[aria-label="Sort recordings"]');
        const out = {{}};
        for (const value of ['size:asc', 'size:desc', 'name:asc']) {{
            select.value = value;
            select.dispatchEvent(new Event('change', {{ bubbles: true }}));
            await new Promise((r) => setTimeout(r, 1800));
            out[value] = [...document.querySelectorAll('.video-name')].map((n) => n.textContent);
        }}
        return out;
    }});
    """.format(url=library_server['url'])
    out = run_browser(library_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['size:asc'] != value['size:desc']
    assert value['name:asc'] == sorted(value['name:asc'])


def test_seek_works_in_the_player(library_server):
    script = """
    await page.goto('{url}#/library/tester/CB', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3500));
    const result = await page.evaluate(async () => {{
        const v = document.querySelector('.player-frame video');
        v.currentTime = 0;
        await new Promise((r) => setTimeout(r, 400));
        const before = v.currentTime;
        v.currentTime = 2;
        await new Promise((r) => setTimeout(r, 900));
        return {{ readyState: v.readyState, duration: v.duration, seeked: v.currentTime, before }};
    }});
    """.format(url=library_server['url'])
    out = run_browser(library_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['readyState'] >= 2
    assert value['duration'] > 0
    assert value['seeked'] > value['before']


def test_favorite_toggle_and_view(library_server):
    script = """
    await page.goto('{url}#/library/tester/CB', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3000));
    const starred = await page.evaluate(async () => {{
        const star = document.querySelector('.video-card .star');
        const before = star.classList.contains('on');
        star.click();
        await new Promise((r) => setTimeout(r, 1800));
        return {{ before, after: document.querySelector('.video-card .star').classList.contains('on') }};
    }});
    await page.goto('{url}#/library?favorites=1', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3000));
    const view = await page.evaluate(() => ({{
        cards: document.querySelectorAll('.video-card').length,
        names: [...document.querySelectorAll('.video-name')].map((n) => n.textContent),
    }}));
    const result = {{ starred, view }};
    """.format(url=library_server['url'])
    out = run_browser(library_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['starred']['before'] != value['starred']['after']
    assert value['view']['cards'] == 1


def test_delete_asks_for_confirmation(library_server):
    script = """
    await page.goto('{url}#/library/tester/CB', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3000));
    const before = await page.evaluate(() => document.querySelectorAll('.video-card').length);
    await page.evaluate(() => {{
        document.querySelector('.video-card .btn-ghost[aria-label^="Remove recording"]').click();
    }});
    await new Promise((r) => setTimeout(r, 900));
    const opened = await page.evaluate(() => ({{
        open: document.getElementById('modal').open,
        title: document.getElementById('modal-title').textContent,
        confirm: document.getElementById('modal-confirm').textContent,
    }}));
    await page.click('#modal-cancel');
    await new Promise((r) => setTimeout(r, 1500));
    const afterCancel = await page.evaluate(() => document.querySelectorAll('.video-card').length);
    const result = {{ before, opened, afterCancel }};
    """.format(url=library_server['url'])
    out = run_browser(library_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['opened']['open'] is True
    assert '.mp4' in value['opened']['title']
    # Cancelling must not delete anything.
    assert value['afterCancel'] == value['before']
