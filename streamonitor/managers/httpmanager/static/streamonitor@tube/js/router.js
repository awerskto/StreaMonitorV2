// Hash based router.
//
// Routes:
//   #/accounts                              list of accounts
//   #/accounts?site=CB&status=offline&q=x   filtered accounts
//   #/library                               all folders
//   #/library?favorites=1                   favourites only
//   #/library/<user>/<site>                 one folder
//   #/library/<user>/<site>?video=<file>    with a video selected
//
// The hash is the source of truth, so the browser back and forward buttons
// work without any extra handling.

const listeners = new Set();

function parse() {
    const raw = location.hash.replace(/^#/, '') || '/accounts';
    const [pathPart, queryPart] = raw.split('?');
    const segments = pathPart.split('/').filter(Boolean);
    const params = new URLSearchParams(queryPart || '');

    return {
        path: `/${segments.join('/')}`,
        segments,
        params,
        name: segments[0] || 'accounts',
        user: segments[1] || null,
        site: segments[2] || null,
    };
}

let current = parse();

export function onRoute(fn) {
    listeners.add(fn);
    fn(current);
    return () => listeners.delete(fn);
}

export function currentRoute() {
    return current;
}

/** Navigate. Adds a history entry, so back returns to the previous view. */
export function navigate(path, params = {}) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') search.set(key, value);
    }
    const query = search.toString();
    location.hash = `${path}${query ? `?${query}` : ''}`;
}

/** Replace the current entry, without adding a history entry. */
export function replace(path, params = {}) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null && value !== '') search.set(key, value);
    }
    const query = search.toString();
    const url = `${location.pathname}${location.search}#${path}${query ? `?${query}` : ''}`;
    history.replaceState(null, '', url);
    current = parse();
    emit();
}

function emit() {
    for (const fn of listeners) fn(current);
}

window.addEventListener('hashchange', () => {
    current = parse();
    emit();
});
