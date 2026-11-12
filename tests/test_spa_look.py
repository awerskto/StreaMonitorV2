"""Tests of the look of the "tube" skin.

They cover the bugs the click through in the running application showed:
the icons were invisible, the open dropdown had a light background on a
dark theme, the placeholder of the loading state stayed on the screen and
the header of the table covered the first row.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(APP_ROOT, 'streamonitor/managers/httpmanager/static/streamonitor@tube')
JS = os.path.join(STATIC, 'js')
SKIN = os.path.join(APP_ROOT, 'streamonitor/managers/httpmanager/skins/tube')

sys.path.insert(0, APP_ROOT)


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


# --- the sources ---------------------------------------------------------

def test_the_icon_sprite_is_not_referenced_as_a_file():
    """A browser cannot resolve a file reference in a CSS mask.

    The icons would simply not be painted, so the sprite has to be part of
    the document.
    """
    css = read(os.path.join(STATIC, 'tube.css'))
    assert 'mask-image' not in css
    assert 'icons.svg#' not in css


def test_the_icons_are_inline_svg_with_a_symbol():
    dom = read(os.path.join(JS, 'dom.js'))
    assert "createElementNS('http://www.w3.org/2000/svg', 'svg')" in dom
    assert "setAttribute('href'" in dom


def test_the_template_contains_the_sprite():
    template = read(os.path.join(SKIN, 'templates', 'index.html.jinja'))
    assert 'icon_sprite' in template
    # The static icons have to be svg as well.
    assert '<span class="i i-' not in template


def test_the_theme_declares_the_colour_scheme():
    """The native dropdowns follow the theme only with a colour scheme."""
    css = read(os.path.join(STATIC, 'tube.css'))
    assert 'color-scheme: dark' in css
    assert 'color-scheme: light' in css


def test_the_options_carry_the_colours():
    """The list of a select is painted by the browser, not by the page."""
    css = read(os.path.join(STATIC, 'tube.css'))
    assert re.search(r'option\s*\{[^}]*background-color', css)
    assert re.search(r'option:checked\s*\{[^}]*background-color', css)


def test_the_topbar_offset_is_published():
    """The sticky table header has to know the real height of the bar."""
    app = read(os.path.join(JS, 'app.js'))
    assert '--topbar-offset' in app
    css = read(os.path.join(STATIC, 'tube.css'))
    assert '--topbar-offset' in css


# --- the running application ---------------------------------------------

@pytest.fixture(scope='module')
def live_server():
    """The application, started with the tube skin."""
    import base64
    import time
    import urllib.request

    env = dict(os.environ, STRMNTR_SKIN='tube', STRMNTR_PORT='5197')
    process = subprocess.Popen(
        [os.path.join(APP_ROOT, '.venv/bin/python'), 'Downloader.py'],
        cwd=APP_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.PIPE,
    )
    token = base64.b64encode(b'admin:admin').decode()
    url = 'http://127.0.0.1:5197/'
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


def run_script(live_server, script):
    """Run a snippet in the browser and return what it logs."""
    chromium = shutil.which('chromium') or shutil.which('chromium-browser') \
        or shutil.which('google-chrome')
    node_modules = '/tmp/opencode/node_modules/puppeteer-core'
    if not chromium or not os.path.isdir(node_modules):
        pytest.skip('headless browser not available')

    wrapped = f"""
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
    await page.setExtraHTTPHeaders({{
        Authorization: 'Basic ' + Buffer.from('admin:admin').toString('base64'),
    }});
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.goto('{live_server}' + '/#/accounts', {{ waitUntil: 'networkidle2', timeout: 30000 }});
    await new Promise((r) => setTimeout(r, 3500));
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


def test_the_icons_are_painted(live_server):
    script = """
    const result = await page.evaluate(() => {
        const icons = [...document.querySelectorAll('svg.i')];
        const dbg = { selects: [...document.querySelectorAll('select')].map(s => s.getAttribute('aria-label')) };
        const first = icons[0];
        const rect = first?.getBoundingClientRect();
        return {
            count: icons.length,
            width: rect ? Math.round(rect.width) : 0,
            height: rect ? Math.round(rect.height) : 0,
            hasUse: !!first?.querySelector('use'),
            spriteInPage: !!document.querySelector('symbol#menu'),
            dbg,
        };
    });
    """
    out = run_script(live_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['count'] > 0
    # The sprite is part of the document.
    assert value['spriteInPage'] is True
    # The icons have a size, they are not collapsed.
    assert value['width'] > 8
    assert value['height'] > 8
    assert value['hasUse'] is True


def test_the_open_list_of_a_select_is_dark(live_server):
    script = """
    const result = await page.evaluate(() => {
        const select = [...document.querySelectorAll('select')]
            .find((s) => [...s.options].some((o) => /Any status|Każdy status/.test(o.textContent)));
        const option = select.options[1];
        const scheme = getComputedStyle(document.documentElement).colorScheme;
        return {
            scheme,
            optionBg: getComputedStyle(option).backgroundColor,
            optionColor: getComputedStyle(option).color,
            checkedBg: getComputedStyle(select.options[0]).backgroundColor,
        };
    });
    """
    out = run_script(live_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['scheme'] == 'dark'
    # A white list on a dark theme is unreadable.
    assert value['optionBg'] != 'rgba(0, 0, 0, 0)'
    background = [int(part) for part in re.findall(r'\d+', value['optionBg'])[:3]]
    assert sum(background) < 300, value['optionBg']
    # The text has to be readable on it.
    text = [int(part) for part in re.findall(r'\d+', value['optionColor'])[:3]]
    assert sum(text) > 400, value['optionColor']
    # The selected entry is highlighted, so it has to stand out.
    checked = [int(part) for part in re.findall(r'\d+', value['checkedBg'])[:3]]
    assert sum(checked) > 300, value['checkedBg']


def test_the_light_theme_has_a_light_list(live_server):
    script = """
    await page.evaluate(() => {
        const btn = document.querySelector('#theme-toggle');
        if (btn) btn.click();
    });
    await new Promise((r) => setTimeout(r, 1200));
    const result = await page.evaluate(() => {
        const select = [...document.querySelectorAll('select')]
            .find((s) => [...s.options].some((o) => /Any status|Każdy status/.test(o.textContent)));
        return {
            theme: document.documentElement.dataset.theme,
            scheme: getComputedStyle(document.documentElement).colorScheme,
            optionBg: getComputedStyle(select.options[1]).backgroundColor,
        };
    });
    """
    out = run_script(live_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['theme'] == 'light'
    assert value['scheme'] == 'light'
    background = [int(part) for part in re.findall(r'\d+', value['optionBg'])[:3]]
    assert sum(background) > 400, value['optionBg']


def test_the_loading_placeholder_is_gone(live_server):
    script = """
    const result = await page.evaluate(() => ({
        placeholders: document.querySelectorAll('#main > .loading').length,
    }));
    """
    out = run_script(live_server, script)
    assert out['errors'] == []
    assert out['value']['placeholders'] == 0


def test_the_table_header_does_not_cover_the_first_row(live_server):
    script = """
    const result = await page.evaluate(() => {
        const head = document.querySelector('table.grid thead th');
        const row = document.querySelector('table.grid tbody tr');
        if (!head || !row) return { error: 'no table' };
        const h = head.getBoundingClientRect();
        const r = row.getBoundingClientRect();
        return { headBottom: Math.round(h.bottom), rowTop: Math.round(r.top) };
    });
    """
    out = run_script(live_server, script)
    assert out['errors'] == []
    value = out['value']
    assert 'error' not in value, value
    # The first row must start where the header ends, not behind it.
    assert value['rowTop'] >= value['headBottom'] - 2, value


def test_the_topbar_grows_with_its_content(live_server):
    """The bar holds a form, so it must not have a fixed height."""
    script = """
    const result = await page.evaluate(() => {
        const topbar = document.querySelector('.topbar');
        const style = getComputedStyle(topbar);
        const offset = getComputedStyle(document.documentElement)
            .getPropertyValue('--topbar-offset');
        return {
            height: Math.round(topbar.getBoundingClientRect().height),
            cssHeight: style.height,
            offset: offset.trim(),
            actions: document.querySelectorAll('#topbar-actions form').length,
        };
    });
    """
    out = run_script(live_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['actions'] >= 1
    # The offset has to follow the bar.
    assert value['offset'].endswith('px')
    assert abs(int(value['offset'].rstrip('px')) - value['height']) <= 2, value
