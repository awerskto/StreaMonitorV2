// Library view.
//
// Left: the player and the list of recordings of the selected folder.
// Right: the list of folders (one per streamer) with the poster of the newest
// recording, a REC badge while a recording is running, a search field and
// sorting by name or by last activity.

import { api } from '../api.js';
import { bytes, clear, debounce, duration, el, emptyState, icon, relativeTime, svgUse } from '../dom.js';
import { navigate, replace } from '../router.js';
import { confirmAction, toast } from '../ui.js';
import { createPlayer } from '../player.js';
import { attachScrub } from '../scrub.js';
import { settings } from '../settings.js';
import { t } from '../i18n.js';

// The sorting of the recordings: key, label, the text of both directions.
const SORTS = [
    ['date', 'sortDate', 'dirNewest', 'dirOldest'],
    ['name', 'sortName', 'dirAsc', 'dirDesc'],
    ['size', 'sortSize', 'dirAsc', 'dirDesc'],
    ['duration', 'sortDuration', 'dirAsc', 'dirDesc'],
];

let state = {
    q: '',
    // The newest folders first: the recording the user looks for is usually
    // the one that was made last.
    sort: 'last_activity',
    dir: 'desc',
    videoSort: 'date',
    videoDir: 'desc',
    favoritesOnly: false,
};

// How many folders are loaded at once.
const FOLDER_PAGE_SIZE = 60;

let player = null;
let videoList = [];
let currentPage = 1;
let folderPage = 1;
let currentUser = null;
let currentSite = null;

// --- title ----------------------------------------------------------------

export function title(route) {
    if (route.user && route.site) return `${route.user} · ${route.site}`;
    return t('libraryTitle');
}

// --- toolbar --------------------------------------------------------------

export function toolbar(container) {
    const search = el('input', {
        type: 'search',
        placeholder: t('searchFolders'),
        'aria-label': t('searchFolders'),
        value: state.q,
        oninput: debounce((event) => {
            state.q = event.target.value.trim();
            folderPage = 1;
            loadFolders();
        }, 300),
    });

    const sortSelect = el('select', {
        'aria-label': t('sortFolders'),
        onchange: (event) => {
            const [sort, dir] = event.target.value.split(':');
            state.sort = sort;
            state.dir = dir;
            folderPage = 1;
            loadFolders();
        },
    });
    sortSelect.append(el('option', { value: `name:${t('dirAsc')}`, text: t('sortName') }));
    sortSelect.append(el('option', { value: `last_activity:${t('dirDesc')}`, text: t('sortDate') }));
    sortSelect.value = `${state.sort}:${state.dir}`;

    container.append(el('div', { class: 'toolbar' }, [
        el('label', { class: 'field search' }, [icon('search'), search]),
        el('label', { class: 'field' }, [sortSelect]),
    ]));

    return container;
}

// --- render ---------------------------------------------------------------

export async function render(container, route) {
    state.q = route.params.get('q') || state.q;
    if (route.params.get('sort') === 'recent') {
        state.sort = 'last_activity';
        state.dir = 'desc';
    }
    state.videoSort = route.params.get('sort') && SORTS.some((s) => s[0] === route.params.get('sort'))
        ? route.params.get('sort') : state.videoSort;
    state.videoDir = route.params.get('dir') || state.videoDir;
    state.favoritesOnly = route.params.get('favorites') === '1';

    const layout = el('div', { class: 'library-layout' });
    container.append(layout);

    const detail = el('section', { class: 'panel-detail' });
    const folders = el('aside', { class: 'panel-folders' });
    layout.append(detail, folders);

    player = createPlayer(detail, onPlayerEvent);

    currentUser = route.user;
    currentSite = route.site;

    await loadFolders();

    if (state.favoritesOnly && !currentUser) {
        // The favourites view collects videos from every folder.
        detail.append(videoListToolbar());
        await loadFavorites();
        return;
    }

    if (currentUser && currentSite) {
        detail.append(videoListToolbar());
        await loadVideos(true);
    } else {
        detail.append(emptyState('empty-folders', t('libraryTitle'), t('chooseFolderHint'), 'wide'));
    }
}

/** Sorting and filtering for the list of recordings. */
function videoListToolbar() {
    const sortSelect = el('select', {
        'aria-label': t('sortRecordings'),
        onchange: (event) => {
            const [sort, dir] = event.target.value.split(':');
            state.videoSort = sort;
            state.videoDir = dir;
            loadVideos(true);
        },
    });
    for (const [key, label, down, up] of SORTS) {
        // The arrows are not in every font, so the direction is spelled out.
        sortSelect.append(el('option', { value: `${key}:desc`, text: `${t(label)} (${t(down)})` }));
        sortSelect.append(el('option', { value: `${key}:asc`, text: `${t(label)} (${t(up)})` }));
    }
    sortSelect.value = `${state.videoSort}:${state.videoDir}`;

    const favorites = el('input', {
        type: 'checkbox',
        id: 'favorites-only',
        checked: state.favoritesOnly,
        onchange: (event) => {
            state.favoritesOnly = event.target.checked;
            loadVideos(true);
        },
    });

    return el('div', { class: 'toolbar' }, [
        el('label', { class: 'field' }, [sortSelect]),
        el('label', { class: 'btn btn-sm btn-ghost', for: 'favorites-only' }, [
            favorites, t('onlyFavorites'),
        ]),
    ]);
}

/** The favourites view: videos from every folder. */
async function loadFavorites() {
    const panel = document.querySelector('.panel-detail');
    if (!panel) return;
    let grid = panel.querySelector('.video-grid');
    if (!grid) {
        grid = el('div', { class: 'video-grid' });
        panel.append(grid);
    }
    clear(grid);

    let data;
    try {
        data = await api.favorites();
    } catch (error) {
        grid.append(emptyState('empty-videos', t('errorPrefix', error.message)));
        return;
    }

    if (!data.videos.length) {
        grid.append(emptyState('empty-favorites', t('emptyFavorites')));
        return;
    }

    videoList = data.videos;
    for (const video of videoList) {
        // The favourites endpoint does not know about the artifacts, so the
        // urls are built here.
        const encoded = video.path.split('/').map(encodeURIComponent).join('/');
        grid.append(videoCard({
            ...video,
            stream_url: `/api/v2/videos/${encoded}/stream`,
            poster_url: video.has_poster ? `/api/v2/videos/${encoded}/poster` : null,
            sprite_url: video.has_sprite ? `/api/v2/videos/${encoded}/sprite` : null,
        }));
    }
}

// --- folders --------------------------------------------------------------

async function loadFolders(reset = true) {
    const panel = document.querySelector('.panel-folders');
    if (!panel) return;

    if (reset) {
        folderPage = 1;
        clear(panel).append(el('div', { class: 'loading', text: t('loading') }));
    }

    let data;
    try {
        data = await api.folders({
            q: state.q,
            sort: state.sort,
            page: folderPage,
            per_page: FOLDER_PAGE_SIZE,
        });
    } catch (error) {
        clear(panel).append(el('div', { class: 'empty', text: t('errorPrefix', error.message) }));
        return;
    }

    // The list survives a refresh, so the loaded pages are not lost.
    let list = panel.querySelector('.folder-list');
    if (!list) {
        clear(panel);
        list = el('div', { class: 'folder-list' });
        panel.append(list);
    }
    if (!data.folders.length) {
        if (!list.childElementCount) {
            clear(panel).append(emptyState('empty-folders', t('emptyFolders')));
        }
        return;
    }

    for (const folder of data.folders) {
        const active = folder.username === currentUser && folder.site === currentSite;
        const recording = (folder.recording_files || []).length > 0;

        // Only the poster of the newest recording, the sidebar is a list and
        // not a place to scrub.
        const thumb = el('div', { class: 'folder-thumb' }, [
            folder.poster
                ? el('img', { src: assetUrl(folder.poster), alt: '', loading: 'lazy' })
                : icon('folder'),
            recording ? el('span', { class: 'badge badge-rec' }, [el('span', { class: 'dot' }), 'REC']) : null,
        ]);

        list.append(el('a', {
            class: `folder ${active ? 'active' : ''}`,
            href: `#/library/${encodeURIComponent(folder.username)}/${encodeURIComponent(folder.site)}`,
            onclick: (event) => {
                event.preventDefault();
                navigate(`/library/${encodeURIComponent(folder.username)}/${encodeURIComponent(folder.site)}`);
            },
        }, [
            thumb,
            el('div', { class: 'folder-meta' }, [
                el('div', { class: 'folder-name', text: folder.username }),
                el('div', { class: 'folder-site', text: folder.site }),
                el('div', { class: 'faint', text: `${folder.count} · ${bytes(folder.total_size)}` }),
                el('div', { class: 'faint', text: relativeTime(folder.last_activity) }),
            ]),
        ]));
    }

    // More folders than one page: the button loads the next page.
    let more = panel.querySelector('.load-more');
    const left = data.total - folderPage * FOLDER_PAGE_SIZE;
    if (left > 0) {
        if (!more) {
            more = el('div', { class: 'load-more' });
            panel.append(more);
        }
        clear(more).append(el('button', {
            class: 'btn btn-sm',
            type: 'button',
            onclick: async (event) => {
                event.target.disabled = true;
                folderPage += 1;
                await loadFolders(false);
            },
        }, t('moreFolders', left)));
    } else if (more) {
        more.remove();
    }
}

/** An absolute URL without credentials. */
function assetUrl(path) {
    return new URL(path, window.location.origin).toString();
}

// --- videos ---------------------------------------------------------------

async function loadVideos(reset = false) {
    if (!currentUser || !currentSite) return;
    const panel = document.querySelector('.panel-detail');
    if (!panel) return;

    if (reset) {
        currentPage = 1;
        videoList = [];
        let grid = panel.querySelector('.video-grid');
        if (!grid) {
            grid = el('div', { class: 'video-grid' });
            panel.append(el('div', {}, [grid]));
        }
        clear(grid);
    }

    const grid = panel.querySelector('.video-grid');
    if (!grid) return;

    let data;
    try {
        data = await api.videos(currentUser, currentSite, {
            sort: state.videoSort,
            dir: state.videoDir,
            favorites: state.favoritesOnly ? 1 : '',
            page: currentPage,
            per_page: 40,
        });
    } catch (error) {
        grid.append(emptyState('empty-videos', t('errorPrefix', error.message)));
        return;
    }

    // The refresh replaces the cards of the page it just fetched. The pages
    // that the user loaded before stay, and so does the selection.
    const fresh = new Map(data.videos.map((v) => [v.path, v]));
    const kept = [];
    for (const old of videoList) {
        kept.push(fresh.get(old.path) || old);
        fresh.delete(old.path);
    }
    for (const video of fresh.values()) kept.push(video);
    videoList = kept;

    const loaded = new Set([...grid.querySelectorAll('.video-card')].map((card) => card.dataset.path));
    for (const video of data.videos) {
        if (loaded.has(video.path)) continue;
        grid.append(videoCard(video));
    }
    // Update the cards that are on the screen, in place.
    for (const card of grid.querySelectorAll('.video-card')) {
        const video = videoList.find((v) => v.path === card.dataset.path);
        if (video) refreshCard(card, video);
    }

    if (!videoList.length) {
        grid.append(emptyState('empty-videos', t('emptyVideos')));
    }

    // The load more button lives under the grid.
    let more = panel.querySelector('.load-more');
    if (data.videos.length && data.total > videoList.length) {
        if (!more) {
            more = el('div', { class: 'load-more' });
            panel.append(more);
        }
        clear(more).append(el('button', {
            class: 'btn',
            type: 'button',
            onclick: async (event) => {
                event.target.disabled = true;
                currentPage += 1;
                await loadVideos(false);
            },
        }, t('loadMore', data.total - videoList.length)));
    } else if (more) {
        more.remove();
    }

    // Preselect a video of the folder. A recording that is still running has
    // no playable file yet, so it is skipped.
    if (reset && videoList.length && !player.current) {
        const ready = videoList.find((v) => !v.recording) || videoList[0];
        selectVideo(ready);
    }
}

/** One video card. */
function videoCard(video) {
    const thumb = el('button', {
        class: 'video-thumb',
        type: 'button',
        'aria-label': t('playAria', video.filename),
        onclick: () => selectVideo(video),
    }, [
        video.poster_url
            ? el('img', { src: assetUrl(video.poster_url), alt: '', loading: 'lazy' })
            : el('span', { class: 'placeholder' }, svgUse('filmstrip', 'ph-filmstrip', '0 0 24 16')),
        video.duration ? el('span', { class: 'duration', text: duration(video.duration) }) : null,
        video.recording
            ? el('span', { class: 'badge badge-rec' }, [el('span', { class: 'dot' }), 'REC'])
            : null,
    ]);
    // The proportions the player should show, from the display matrix.
    if (video.display_w && video.display_h && video.display_w < video.display_h) {
        thumb.classList.add('portrait');
    }
    // The storyboard only exists for a finished recording with a sprite.
    if (video.sprite_url && video.sprite_cols) {
        thumb.classList.add('scrubbable');
        attachScrub(thumb, video, settings.scrubZone);
    }

    const star = el('button', {
        class: `btn btn-sm btn-ghost star ${video.favorite ? 'on' : ''}`,
        type: 'button',
        'aria-label': video.favorite
            ? t('favRemove', video.filename)
            : t('favAdd', video.filename),
        onclick: async (event) => {
            event.stopPropagation();
            try {
                const state = await api.toggleFavorite(video.path);
                video.favorite = state.favorite;
                star.classList.toggle('on', state.favorite);
                star.setAttribute('aria-label', state.favorite
                    ? t('favRemove', video.filename)
                    : t('favAdd', video.filename));
            } catch (error) {
                toast(t('failedToSaveFav', error.message), 'error');
            }
        },
    }, [icon('star')]);

    const trash = el('button', {
        class: 'btn btn-sm btn-ghost',
        type: 'button',
        'aria-label': t('removeRecording', video.filename),
        onclick: async (event) => {
            event.stopPropagation();
            const confirmed = await confirmAction(
                t('deleteTitle', video.filename),
                t('deleteBody'),
                t('delete'),
            );
            if (!confirmed) return;
            try {
                await api.deleteVideo(video.path);
                toast(t('deleted', video.filename), 'success');
                const card = thumb.closest('.video-card');
                if (card) card.remove();
            } catch (error) {
                toast(t('failedToDelete', error.message), 'error');
            }
        },
    }, [icon('trash')]);

    return el('div', { class: 'video-card', dataset: { path: video.path } }, [
        thumb,
        el('div', { class: 'video-info' }, [
            el('div', { class: 'video-name', text: video.filename, title: video.filename }),
            el('div', { class: 'video-sub' }, [
                el('span', { text: relativeTime(video.mtime) }),
                el('span', { text: bytes(video.size) }),
                video.width && video.height
                    ? el('span', { text: `${video.width}x${video.height}` })
                    : null,
            ]),
            el('div', { class: 'video-actions' }, [star, trash]),
        ]),
    ]);
}

/** Update one card in place, so a refresh does not reset the page. */
function refreshCard(card, video) {
    if (!card || !video) return;
    const img = card.querySelector('.video-thumb img');
    if (img && video.poster_url) img.src = assetUrl(video.poster_url);
    const badge = card.querySelector('.duration');
    if (badge && video.duration) badge.textContent = duration(video.duration);
    const rec = card.querySelector('.badge-rec');
    if (rec && !video.recording) rec.remove();
    const star = card.querySelector('.star');
    if (star) {
        star.classList.toggle('on', Boolean(video.favorite));
        star.setAttribute('aria-label', video.favorite
            ? t('favRemove', video.filename)
            : t('favAdd', video.filename));
    }
    const sub = card.querySelector('.video-sub');
    if (sub) {
        clear(sub).append(
            el('span', { text: relativeTime(video.mtime) }),
            el('span', { text: bytes(video.size) }),
            video.width && video.height
                ? el('span', { text: `${video.width}x${video.height}` })
                : null,
        );
    }
}

/** Show a video in the player. */
function selectVideo(video, autoplay = false) {
    if (!player) return;
    player.load(video, videoList, autoplay);
    for (const card of document.querySelectorAll('.video-card')) {
        card.classList.remove('selected');
    }
}

/** The player asked for something. */
function onPlayerEvent(event, payload) {
    if (event === 'next') {
        const index = videoList.findIndex((v) => player.current && v.path === player.current.path);
        const next = videoList[index + 1];
        // The autoplay has to carry over, otherwise the next recording only
        // waits for a click.
        if (next) selectVideo(next, player.autoplayEnabled());
    } else if (event === 'deleted') {
        loadVideos(true);
        loadFolders();
    } else if (event === 'favorite') {
        loadFolders();
    }
}

// --- auto refresh ---------------------------------------------------------

let timer = null;

export function startAutoRefresh(seconds) {
    stopAutoRefresh();
    if (!seconds) return;
    timer = setInterval(() => {
        if (document.activeElement && document.activeElement.tagName === 'INPUT') return;
        loadFolders(false);
        if (currentUser && currentSite) loadVideos(false);
    }, seconds * 1000);
}

export function stopAutoRefresh() {
    if (timer) clearInterval(timer);
    timer = null;
}
