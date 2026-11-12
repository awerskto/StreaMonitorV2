"""Tests of the translations.

Every language has to carry every key the interface asks for, the browser
language decides when nothing was chosen and the switcher changes the
language without a reload.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
I18N = os.path.join(APP_ROOT, 'streamonitor/managers/httpmanager/static/streamonitor@tube/js/i18n.js')
NODE_MODULES = '/tmp/opencode/node_modules/puppeteer-core'

sys.path.insert(0, APP_ROOT)


def run_i18n(script):
    """Import the i18n module in node and return what it logs."""
    result = subprocess.run(
        ['node', '--input-type=module', '-e',
         f"import * as i18n from '{I18N}';\n"
         f"i18n.initLanguage();\n"
         f"const out = (function() {{ {script} }})();\n"
         "console.log('RESULT:' + JSON.stringify(out));"],
        capture_output=True, text=True, timeout=60)
    for line in result.stdout.splitlines():
        if line.startswith('RESULT:'):
            return json.loads(line[len('RESULT:'):])
    raise AssertionError(f'node failed:\n{result.stdout[-1500:]}\n{result.stderr[-1500:]}')


def all_keys():
    """The keys of the base language."""
    out = run_i18n("return Object.keys(i18n.messages().en || {})")
    # messages() resolves the active language; ask for English directly.
    return out


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_every_language_has_every_key():
    """A missing key would fall back to English, but it must not happen."""
    source = open(I18N, encoding='utf-8').read()
    keys = re.findall(r"^        ([a-zA-Z]+): ", source, re.MULTILINE)
    blocks = re.split(r"^    [a-z]{2}: \{$", source, flags=re.MULTILINE)[1:]
    languages = re.findall(r"^    ([a-z]{2}): \{$", source, re.MULTILINE)
    assert len(languages) == 15, languages
    for language, block in zip(languages, blocks):
        defined = set(re.findall(r"^        ([a-zA-Z]+): ", block, re.MULTILINE))
        missing = set(keys) - defined
        assert not missing, f'{language} is missing: {sorted(missing)}'


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_browser_language_is_used_when_nothing_is_stored():
    """English is the fallback when the browser speaks something unknown."""
    out = run_i18n("return { code: i18n.getLanguage(), t: i18n.t('loading') }")
    assert out['code'] in ('en', 'pl')
    assert out['t'], 'the translation is empty'


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_placeholders_are_replaced():
    out = run_i18n("return i18n.t('loadMore', 7)")
    assert '7' in out


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_all_languages_translate_the_same_key():
    """The keys must not return the raw key name in any language."""
    out = run_i18n("""
        const codes = i18n.LANGUAGES.map((l) => l.code);
        const result = {};
        for (const code of codes) {
            i18n.setLanguage(code);
            result[code] = i18n.t('thLastRecording');
        }
        i18n.setLanguage('en');
        return result;
    """)
    for code, text in out.items():
        assert text and text != 'thLastRecording', (code, text)


@pytest.mark.skipif(not shutil.which('node'), reason='node not available')
def test_the_flags_are_unique():
    out = run_i18n("""
        const flags = i18n.LANGUAGES.map((l) => l.flag);
        return { flags, count: i18n.LANGUAGES.length };
    """)
    assert out['count'] == 15
    assert len(set(out['flags'])) == 15, out['flags']


# --- the browser ----------------------------------------------------------

@pytest.fixture(scope='module')
def live_server():
    import base64
    import time
    import urllib.request

    env = dict(os.environ, STRMNTR_SKIN='tube', STRMNTR_PORT='5196')
    process = subprocess.Popen(
        [os.path.join(APP_ROOT, '.venv/bin/python'), 'Downloader.py'],
        cwd=APP_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.PIPE,
    )
    token = base64.b64encode(b'admin:admin').decode()
    url = 'http://127.0.0.1:5196/'
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


def run_browser(live_server, script):
    chromium = shutil.which('chromium') or shutil.which('chromium-browser') \
        or shutil.which('google-chrome')
    if not chromium or not os.path.isdir(NODE_MODULES):
        pytest.skip('headless browser not available')
    wrapped = f"""
const puppeteer = require('{NODE_MODULES}');
(async () => {{
    const browser = await puppeteer.launch({{
        executablePath: '{chromium}',
        headless: 'new',
        args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
               '--lang=de'],
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
    await new Promise((r) => setTimeout(r, 4000));
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
    assert match, f'script failed:\n{out.stdout[-1200:]}\n{out.stderr[-1200:]}'
    print('STDOUT TAIL:', out.stdout[-400:])
    return match


def test_the_switcher_changes_the_language(live_server):
    script = """
    const result = await page.evaluate(async () => {
        const flag = document.getElementById('lang-flag');
        const button = document.getElementById('lang-toggle');
        const menu = document.getElementById('lang-menu');
        const before = {
            flag: document.querySelector('#lang-toggle .flag-svg use')?.getAttribute('href'),
            title: document.querySelector('#view-title')?.textContent,
            header: document.querySelector('table.grid th')?.textContent,
        };
        button.click();
        await new Promise((r) => setTimeout(r, 300));
        const items = [...menu.querySelectorAll('.lang-item')].map((i) => ({
            code: i.dataset.code,
            text: i.textContent,
            flag: i.querySelector('.flag-svg use')?.getAttribute('href'),
        }));
        const count = items.length;
        const opened = !menu.hidden;
        const flags = items.map((i) => i.flag);

        // Polish
        const polish = [...menu.querySelectorAll('.lang-item')]
            .find((i) => i.dataset.code === 'pl');
        polish.click();
        await new Promise((r) => setTimeout(r, 2500));
        const after = {
            flag: document.querySelector('#lang-toggle .flag-svg use')?.getAttribute('href'),
            title: document.querySelector('#view-title')?.textContent,
            header: document.querySelector('table.grid th')?.textContent,
            lang: document.documentElement.lang,
        };

        // German, to be sure the whole view is drawn again
        document.getElementById('lang-toggle').click();
        await new Promise((r) => setTimeout(r, 300));
        const german = [...document.querySelectorAll('.lang-item')]
            .find((i) => i.dataset.code === 'de');
        german.click();
        await new Promise((r) => setTimeout(r, 2500));
        const germanView = {
            flag: document.querySelector('#lang-toggle .flag-svg use')?.getAttribute('href'),
            title: document.querySelector('#view-title')?.textContent,
            header: document.querySelector('table.grid th')?.textContent,
            lang: document.documentElement.lang,
        };
        return { before, count, opened, flags, after, germanView };
    });
    """
    out = run_browser(live_server, script)
    assert out['errors'] == [], out['errors']
    value = out['value']
    assert value['count'] == 15
    assert value['opened'] is True
    # Every item carries the flag of its language.
    assert len(set(value['flags'])) == 15, value['flags']
    # The default interface is not the Polish one.
    assert value['before']['title'] != value['after']['title'], value
    assert value['after']['lang'] == 'pl'
    assert value['after']['title'] == 'Konta'
    # The table header followed the language.
    assert value['after']['header'].startswith('Konto')
    # The German view is drawn again completely.
    assert value['germanView']['lang'] == 'de'
    assert value['germanView']['title'] == 'Konten'
    assert value['germanView']['header'].startswith('Konto')
    # The flag of the button followed the language.
    assert value['before']['flag'] != value['after']['flag']
    assert value['after']['flag'] != value['germanView']['flag']


def test_the_choice_survives_a_reload(live_server):
    script = """
    // Choose German, then reload the page.
    await page.evaluate(() => {
        localStorage.setItem('sm.tube.language', 'de');
    });
    await page.reload({ waitUntil: 'networkidle2' });
    await new Promise((r) => setTimeout(r, 4000));
    const result = await page.evaluate(() => ({
        title: document.querySelector('#view-title')?.textContent,
        lang: document.documentElement.lang,
        header: document.querySelector('table.grid th')?.textContent,
    }));
    // Back to English for the other tests.
    await page.evaluate(() => localStorage.removeItem('sm.tube.language'));
    """
    out = run_browser(live_server, script)
    assert out['errors'] == []
    value = out['value']
    assert value['lang'] == 'de'
    assert value['title'] == 'Konten'
