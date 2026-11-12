// Accounts view: the list of monitored streamers.
//
// Auto refresh pulls fresh data from the API and patches the DOM in place,
// so the scroll position and the focus are not lost.

import { api } from '../api.js';
import { bytes, clear, debounce, el, emptyState, icon, relativeTime, statusClass } from '../dom.js';
import { t } from '../i18n.js';
import { replace } from '../router.js';
import { addStreamerControls, confirmAction, toast } from '../ui.js';

const rowCache = new Map();
let timer = null;
// The columns the header can sort by.
const SORTABLE = new Set(['username', 'site', 'status', 'last', 'recordings', 'size']);

let state = { site: '', status: '', q: '', sort: 'username', dir: 'asc', data: null };

export function title() {
    return t('accountsTitle');
}

/** Build the toolbar. Returns the element. */
export function toolbar(container) {
    const search = el('input', {
        type: 'search',
        placeholder: t('searchAccounts'),
        'aria-label': t('searchAccounts'),
        value: state.q,
        oninput: debounce((event) => {
            state.q = event.target.value.trim();
            syncUrl();
            load();
        }, 300),
    });

    const siteSelect = el('select', {
        id: 'site-filter',
        'aria-label': `${t('thSite')}: ${t('navBySite')}`,
        onchange: (event) => {
            state.site = event.target.value;
            syncUrl();
            load();
        },
    });

    const statusSelect = el('select', {
        class: 'status',
        'aria-label': t('anyStatus'),
        onchange: (event) => {
            state.status = event.target.value;
            syncUrl();
            load();
        },
    });

    const chips = el('div', { class: 'chips' });

    const add = addStreamerControls(() => reload());

    // The form is the bar: the filters, the bulk actions and the add form
    // share one row next to the title.
    const bar = el('form', { class: 'toolbar' }, [
        el('label', { class: 'field search' }, [icon('search'), search]),
        el('label', { class: 'field' }, [statusSelect]),
        chips,
        el('span', { class: 'spacer' }),
        el('label', { class: 'field add-user' }, [add.username]),
        el('label', { class: 'field' }, [add.site]),
        add.submit,
        el('button', {
            class: 'btn',
            type: 'button',
            onclick: () => load(true),
        }, [icon('refresh'), t('refresh')]),
        el('button', {
            class: 'btn',
            type: 'button',
            onclick: () => bulk(true),
        }, [icon('play'), t('start')]),
        el('button', {
            class: 'btn',
            type: 'button',
            onclick: () => bulk(false),
        }, [icon('pause'), t('stop')]),
    ]);
    add.wire(bar);

    container.append(bar);

    // The site filter lives in the sidebar ("By site"); the hidden select
    // keeps the URL state readable.
    siteSelect.classList.add('hidden');
    container.append(siteSelect);

    // Fill the selects once the metadata is known.
    api.meta().then((meta) => {
        clear(statusSelect);
        statusSelect.append(el('option', { value: '', text: t('anyStatus') }));
        for (const [key, label] of Object.entries(meta.filterable_status)) {
            statusSelect.append(el('option', { value: key, text: statusLabel(key) }));
        }
        statusSelect.value = state.status;

        // Quick filters for the interesting states.
        clear(chips);
        for (const key of ['online', 'notrunning', 'offline', 'error']) {
            chips.append(
                el('button', {
                    class: `chip ${state.status === key ? 'active' : ''}`,
                    type: 'button',
                    dataset: { status: key },
                    onclick: () => {
                        state.status = state.status === key ? '' : key;
                        statusSelect.value = state.status;
                        syncUrl();
                        load();
                    },
                }, statusLabel(key)),
            );
        }
    }).catch(() => { /* the list itself reports the error */ });

    return bar;
}

/** Human label for a filterable status key. */
function statusLabel(key) {
    return {
        online: t('chipOnline'),
        offline: t('chipOffline'),
        longoffline: t('stLongOffline'),
        notrunning: t('stNotRunning'),
        private: t('stPrivate'),
        restricted: t('stRestricted'),
        error: t('chipErrors'),
        ratelimit: t('stRatelimit'),
        notexist: t('stNotexist'),
    }[key] || key;
}

/** The status of a streamer in Polish. */
function statusText(streamer) {
    const labels = {
        200: t('stPublic'),
        1403: t('stRestricted'),
        400: t('stNotexist'),
        403: t('stPrivate'),
        404: t('stOffline'),
        410: t('stLongOffline'),
        429: t('stRatelimit'),
        1: t('stUnknown'),
        2: t('stNotRunning'),
        3: t('stError'),
    };
    return labels[streamer.status] || streamer.status_label || '—';
}

/** Keep the URL in sync with the filters, so the view can be shared. */
function syncUrl() {
    replace('/accounts', { site: state.site, status: state.status, q: state.q,
                           sort: state.sort, dir: state.dir });
}

/** Render the list into `container`. */
export async function render(container, route) {
    // The URL is the source of truth, otherwise a filter would survive a
    // click on a navigation link that does not say anything about filters.
    state.site = route.params.get('site') || '';
    state.status = route.params.get('status') || '';
    state.q = route.params.get('q') || '';
    state.sort = SORTABLE.has(route.params.get('sort'))
        ? route.params.get('sort') : state.sort;
    state.dir = ['asc', 'desc'].includes(route.params.get('dir'))
        ? route.params.get('dir') : state.dir;

    const list = el('div', { id: 'accounts-list' });
    const disk = el('div', { class: 'disk' });
    container.append(disk, list);

    await load();
}

async function load(manual = false) {
    const list = document.getElementById('accounts-list');
    if (!list) return;
    if (manual) {
        list.setAttribute('aria-busy', 'true');
    }

    let data;
    try {
        data = await api.streamers({ site: state.site, status: state.status, q: state.q,
                                     sort: state.sort, dir: state.dir });
    } catch (error) {
        clear(list).append(el('div', { class: 'empty', text: t('failedToLoad', error.message) }));
        list.removeAttribute('aria-busy');
        return;
    }

    state.data = data;
    renderDisk(data.free_space);
    renderSiteFilter(data.streamers);
    renderRows(list, data.streamers);
    list.removeAttribute('aria-busy');
}

/** Only the sites that the accounts actually use belong in the filter. */
function renderSiteFilter(streamers) {
    const select = document.getElementById('site-filter');
    if (!select) return;
    const used = [...new Set((streamers || []).map((s) => s.siteslug))];
    const first = el('option', { value: '', text: t('allSites') });
    clear(select).append(first);
    for (const slug of used) {
        select.append(el('option', { value: slug, text: siteName(slug) }));
    }
    select.value = state.site;
    if (select.value !== state.site) {
        // The selected site has no account any more.
        select.value = '';
        state.site = '';
    }
}

/** The readable name of a site slug. */
function siteName(slug) {
    const names = {
        ATV: 'AmateurTV', BC: 'BongaCams', C4: 'Cam4', CC: 'CamsCom',
        CS: 'CamSoda', CB: 'Chaturbate', CHTV: 'Cherry.tv', DC: 'DreamCam',
        DCVR: 'DreamCamVR', FL: 'FanslyLive', F4F: 'Flirt4Free',
        MV: 'ManyVids', MFC: 'MyFreeCams', SCHU: 'SexChatHU', SM: 'StreaMate',
        SC: 'StripChat', SCVR: 'StripChatVR', XLC: 'XLoveCam',
    };
    return names[slug] || slug;
}

/** The free disk space bar. */
function renderDisk(free) {
    const bar = document.querySelector('.disk');
    if (!bar || !free) return;
    const percent = Math.max(0, Math.min(100, parseFloat(free.percentage) || 0));
    const level = percent < 10 ? 'crit' : percent < 25 ? 'warn' : '';
    clear(bar).append(
        icon('video'),
        el('span', { text: t('diskFree', free.absolute) }),
        el('span', { class: 'disk-bar' }, [
            el('span', { class: level, style: { width: `${percent}%` } }),
        ]),
        el('span', { class: 'mono', text: `${percent.toFixed(1)}%` }),
        el('span', { class: 'faint', text: t('diskOf', free.total) }),
    );
}

/** A header cell of the table: a button that sorts the column. */
function th(label, key, extra = '') {
    const active = state.sort === key;
    const arrow = active ? (state.dir === 'asc' ? ' ↑' : ' ↓') : '';
    const next = active ? (state.dir === 'asc' ? 'desc' : 'asc') : 'asc';
    return el('th', {
        class: `sortable ${active ? 'sorted' : ''} ${extra}`.trim(),
    }, [el('button', {
        type: 'button',
        'aria-label': `${t(label)} (${next === 'asc' ? t('dirAsc') : t('dirDesc')})`,
        onclick: () => {
            state.sort = key;
            state.dir = next;
            syncUrl();
            load();
        },
    }, `${t(label)}${arrow}`)]);
}


/**
 * Patch the table with the new data.
 *
 * Rows are keyed by username and site. Existing rows are updated in place so
 * that the DOM is not rebuilt on every refresh.
 */
function renderRows(list, streamers) {
    let table = list.querySelector('table.grid');
    if (!table) {
        clear(list);
        table = el('table', { class: 'grid' });
        list.append(el('div', { class: 'card table-scroll' }, [table]));
    }

    const head = el('thead', {}, [
        el('tr', {}, [
            th('thAccount', 'username'),
            th('thSite', 'site'),
            th('thStatus', 'status'),
            th('thLastRecording', 'last', 'hide-sm'),
            th('thRecordings', 'recordings', 'num'),
            th('thSize', 'size', 'num hide-sm'),
            el('th', { class: 'actions', text: '' }),
        ]),
    ]);
    const body = el('tbody');
    table.replaceChildren(head, body);

    if (!streamers.length) {
        body.append(el('tr', {}, [
            el('td', { colspan: '7' }, [emptyState('empty-folders', t('emptyAccounts'))]),
        ]));
        rowCache.clear();
        return;
    }

    const seen = new Set();
    for (const streamer of streamers) {
        const key = `${streamer.username}|${streamer.siteslug}`;
        seen.add(key);
        const row = rowCache.get(key);
        if (row) {
            updateRow(row, streamer);
            body.append(row);
        } else {
            const created = createRow(streamer);
            rowCache.set(key, created);
            body.append(created);
        }
    }

    for (const key of [...rowCache.keys()]) {
        if (!seen.has(key)) rowCache.delete(key);
    }
}

/** A row for one streamer. */
function createRow(streamer) {
    const row = el('tr', { dataset: { key: `${streamer.username}|${streamer.siteslug}` } });
    updateRow(row, streamer);
    return row;
}

/** Refresh the contents of a row. */
function updateRow(row, streamer) {
    // The name leads to the recordings of the streamer.
    const name = el('div', { class: 'cell-user' }, [
        streamer.country_flag
            ? el('span', { title: streamer.country_name || '', text: streamer.country_flag })
            : null,
        el('a', {
            class: 'name',
            href: `#/library/${encodeURIComponent(streamer.username)}/${encodeURIComponent(streamer.siteslug)}`,
            text: streamer.username,
        }),
        streamer.recording ? el('span', { class: 'badge badge-rec' }, [el('span', { class: 'dot' }), 'REC']) : null,
    ]);

    const status = el('span', {
        class: `badge ${statusClass(streamer.status)}`,
    }, [icon(streamer.status_icon || 'help'), statusText(streamer)]);

    const site = el('a', {
        href: streamer.url,
        target: '_blank',
        rel: 'noopener noreferrer',
        text: streamer.site,
    });

    const toggle = el('button', {
        class: `btn btn-sm ${streamer.running ? '' : 'btn-primary'}`,
        type: 'button',
        'aria-label': streamer.running
            ? t('monitorOff')
            : t('monitorOn'),
        onclick: () => toggleStreamer(streamer),
    }, [icon(streamer.running ? 'pause' : 'play')]);

    const remove = el('button', {
        class: 'btn btn-sm btn-ghost',
        type: 'button',
        'aria-label': t('removeTitle', streamer.username),
        onclick: () => removeStreamer(streamer),
    }, [icon('trash')]);

    row.replaceChildren(
        el('td', {}, [name]),
        el('td', {}, [site]),
        el('td', {}, [status]),
        el('td', { class: 'hide-sm muted', text: relativeTime(streamer.last_stream) }),
        el('td', { class: 'num', text: String(streamer.video_count) }),
        el('td', { class: 'num hide-sm', text: bytes(streamer.total_size) }),
        el('td', { class: 'actions' }, [toggle, remove]),
    );
}

/** Start or stop the monitoring of one streamer. */
async function toggleStreamer(streamer) {
    try {
        await api.setStreamerRunning(streamer.username, streamer.siteslug, !streamer.running);
        toast(streamer.running ? `Zatrzymano ${streamer.username}` : `Uruchomiono ${streamer.username}`, 'success');
        load();
    } catch (error) {
        toast(t('failedToChange', error.message), 'error');
    }
}

/** Start or stop every filtered streamer. */
async function bulk(running) {
    if (!state.data || !state.data.streamers.length) {
        toast(t('emptyBulk'), 'error');
        return;
    }
    const verb = running ? t('start') : t('stop');
    const confirmed = await confirmAction(
        `${verb} ${state.data.streamers.length}`,
        t('bulkBody'),
    );
    if (!confirmed) return;

    const targets = state.data.streamers.map((s) => `${s.username}|${s.siteslug}`);
    try {
        const result = await api.setAllRunning(running, targets);
        if (result.errors && result.errors.length) {
            toast(t('failedToChange', result.errors.join(', ')), 'error');
        } else {
            toast(`${verb}: ${result.changed}`, 'success');
        }
        load();
    } catch (error) {
        toast(t('failedToChange', error.message), 'error');
    }
}

/** Remove a streamer, after confirmation. Its recordings are kept. */
async function removeStreamer(streamer) {
    const confirmed = await confirmAction(
        t('removeTitle', streamer.username),
        t('removeBody'),
    );
    if (!confirmed) return;

    try {
        await api.removeStreamer(streamer.username, streamer.siteslug);
        toast(t('removed', streamer.username), 'success');
        load();
    } catch (error) {
        toast(t('failedToRemove', error.message), 'error');
    }
}

/** Start the auto refresh. */
export function startAutoRefresh(seconds) {
    stopAutoRefresh();
    if (!seconds) return;
    timer = setInterval(() => {
        // Do not fight the user while they are typing.
        if (document.activeElement && document.activeElement.tagName === 'INPUT') return;
        load();
    }, seconds * 1000);
}

export function stopAutoRefresh() {
    if (timer) clearInterval(timer);
    timer = null;
}

/** Reload the list, used after a streamer was added. */
export function reload() {
    return load();
}
