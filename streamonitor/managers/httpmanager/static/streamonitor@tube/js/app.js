// Application shell: sidebar, routing and the shared chrome.

import { api } from './api.js';
import { clear, el, icon } from './dom.js';
import { navigate, onRoute } from './router.js';
import { applyMeta } from './settings.js';
import { initLanguage, t } from './i18n.js';
import { initLanguageSwitcher } from './language.js';
import * as accounts from './views/accounts.js';
import * as library from './views/library.js';

const VIEWS = { accounts, library };

const app = document.getElementById('app');
const navRoot = document.getElementById('sidebar-nav');
const main = document.getElementById('main');
const titleNode = document.getElementById('view-title');
const actionsRoot = document.getElementById('topbar-actions');
const backdrop = document.getElementById('backdrop');

let meta = null;
let lastRoute = null;

// --- theme ---------------------------------------------------------------

const THEME_KEY = 'sm.tube.theme';

function storedTheme() {
    try {
        return localStorage.getItem(THEME_KEY);
    } catch (error) {
        return null;
    }
}

function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const button = document.getElementById('theme-toggle');
    if (button) {
        clear(button).append(icon(theme === 'dark' ? 'sun' : 'moon'));
        button.setAttribute('aria-label', theme === 'dark'
            ? t('themeLight')
            : t('themeDark'));
    }
}

function initTheme() {
    // Dark is the default; the stored preference wins when there is one.
    applyTheme(storedTheme() || 'dark');
    document.getElementById('theme-toggle').addEventListener('click', () => {
        const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
        applyTheme(next);
        try {
            localStorage.setItem(THEME_KEY, next);
        } catch (error) {
            /* private mode, the choice just does not persist */
        }
    });
}

// --- sidebar -------------------------------------------------------------

function navLink(label, iconName, route, options = {}) {
    const link = el('a', {
        class: `nav-link ${options.active ? 'active' : ''}`,
        href: `#${route}`,
        dataset: { nav: options.key || route },
    }, [icon(iconName), el('span', { class: 'nav-label', text: label })]);
    if (options.count !== undefined && options.count !== null) {
        link.append(el('span', { class: 'count', text: String(options.count) }));
    }
    return link;
}

/** Build the navigation. The site list is dynamic. */
function renderNav(route) {
    clear(navRoot);

    const accountsGroup = el('div', { class: 'nav-group' }, [
        el('span', { class: 'nav-group-title', text: t('navAccounts') }),
        el('div', { class: 'nav-list' }, [
            navLink(t('navAllAccounts'), 'video', '/accounts', {
                key: 'accounts',
                active: route.name === 'accounts' && !route.params.get('site')
                    && !route.params.get('status'),
            }),
        ]),
    ]);

    // Sites present in the current account list.
    const siteList = el('div', { class: 'nav-list' });
    const sites = (meta && meta.sites) || {};
    for (const [slug, name] of Object.entries(sites)) {
        siteList.append(
            navLink(name, 'video', `/accounts?site=${encodeURIComponent(slug)}`, {
                key: `site-${slug}`,
                active: route.name === 'accounts' && route.params.get('site') === slug,
            }),
        );
    }
    accountsGroup.append(el('span', { class: 'nav-group-title', text: t('navBySite') }), siteList);

    const libraryGroup = el('div', { class: 'nav-group' }, [
        el('span', { class: 'nav-group-title', text: t('navLibrary') }),
        el('div', { class: 'nav-list' }, [
            navLink(t('navAllFolders'), 'folder', '/library', {
                key: 'library',
                active: route.name === 'library' && !route.params.get('favorites')
                    && !route.user,
            }),
            navLink(t('navFavorites'), 'star', '/library?favorites=1', {
                key: 'favorites',
                active: route.name === 'library' && route.params.get('favorites') === '1',
            }),
            navLink(t('navRecent'), 'refresh', '/library?sort=recent', {
                key: 'recent',
                active: route.name === 'library' && route.params.get('sort') === 'recent',
            }),
        ]),
    ]);

    navRoot.append(accountsGroup, libraryGroup);
}

// --- mobile navigation ---------------------------------------------------

function initMobileNav() {
    const menuButton = document.getElementById('menu-btn');
    const toggle = document.getElementById('sidebar-toggle');
    // The labels come from the template empty, the language is known here.
    toggle.setAttribute('aria-label', t('sidebarToggle'));
    document.querySelector('.sidebar').setAttribute('aria-label', t('navAccounts'));

    const setOpen = (open) => {
        app.classList.toggle('nav-open', open);
        backdrop.hidden = !open;
        menuButton.setAttribute('aria-expanded', String(open));
    };

    menuButton.addEventListener('click', () => setOpen(!app.classList.contains('nav-open')));
    backdrop.addEventListener('click', () => setOpen(false));
    navRoot.addEventListener('click', (event) => {
        if (event.target.closest('a') && window.matchMedia('(max-width: 860px)').matches) {
            setOpen(false);
        }
    });

    // The sidebar toggle collapses to icons on desktop.
    toggle.addEventListener('click', () => {
        if (window.matchMedia('(max-width: 860px)').matches) {
            setOpen(!app.classList.contains('nav-open'));
            return;
        }
        const collapsed = app.classList.toggle('collapsed');
        toggle.setAttribute('aria-expanded', String(!collapsed));
        try {
            localStorage.setItem('sm.tube.collapsed', collapsed ? '1' : '0');
        } catch (error) {
            /* ignore */
        }
    });

    try {
        if (localStorage.getItem('sm.tube.collapsed') === '1') app.classList.add('collapsed');
    } catch (error) {
        /* ignore */
    }
}

// --- routing -------------------------------------------------------------

async function show(route) {
    const view = VIEWS[route.name];
    if (!view) {
        navigate('/accounts');
        return;
    }

    lastRoute = route;
    titleNode.textContent = view.title ? view.title(route) : 'StreaMonitor';

    clear(actionsRoot);
    clear(main).append(el('div', { class: 'loading', text: t('loading') }));

    renderNav(route);

    if (view.startAutoRefresh) view.startAutoRefresh(meta && meta.settings.list_frequency);
    else if (view.stopAutoRefresh) view.stopAutoRefresh();

    // The view builds its own toolbar and appends it to the given container.
    if (view.toolbar) {
        view.toolbar(actionsRoot);
    }

    try {
        await view.render(main, route);
    } catch (error) {
        clear(main).append(el('div', { class: 'empty', text: t('errorPrefix', error.message) }));
    } finally {
        // The placeholder belongs to the content area, the panels of a view
        // have their own.
        main.querySelectorAll(':scope > .loading').forEach((node) => node.remove());
    }
}

// --- start ---------------------------------------------------------------

/**
 * Publish the height of the top bar.
 *
 * The bar grows when a view puts a form into it, so the sticky table header
 * cannot rely on a fixed offset.
 */
function watchTopbar() {
    const topbar = document.querySelector('.topbar');
    if (!topbar) return;

    const publish = () => {
        const height = Math.round(topbar.getBoundingClientRect().height);
        document.documentElement.style.setProperty('--topbar-offset', `${height}px`);
    };
    publish();

    if (typeof ResizeObserver === 'function') {
        new ResizeObserver(publish).observe(topbar);
    } else {
        window.addEventListener('resize', publish);
    }
}

async function start() {
    initLanguage();
    initTheme();
    initLanguageSwitcher();
    initMobileNav();
    watchTopbar();

    try {
        meta = await api.meta();
        applyMeta(meta);
    } catch (error) {
        meta = null;
    }

    onRoute((route) => {
        show(route);
    });

    // A new language re-labels everything, so the view is drawn again.
    document.addEventListener('sm:language', () => {
        if (lastRoute) show(lastRoute);
        renderNav(lastRoute);
    });
}

start();
