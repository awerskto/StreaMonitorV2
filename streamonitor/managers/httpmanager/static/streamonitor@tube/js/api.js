// Thin wrapper around the v2 JSON API.
//
// The URL is built from location.origin on purpose: a relative URL would
// inherit the credentials of a page opened as http://user:pass@host/, and
// fetch() rejects URLs that carry credentials. The browser still sends the
// cached Basic auth header for the same origin.

function url(path) {
    return new URL(path, window.location.origin).toString();
}

async function request(path, options = {}) {
    const response = await fetch(url(path), {
        credentials: 'same-origin',
        // The header marks the request as coming from this application. A
        // form or an image of a foreign page cannot set it, so the server
        // rejects a request that tries to change something without it.
        headers: {
            'X-Requested-With': 'strea',
            ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        },
        ...options,
    });

    if (response.status === 204) return null;

    let payload = null;
    const type = response.headers.get('content-type') || '';
    if (type.includes('application/json')) {
        payload = await response.json();
    }

    if (!response.ok) {
        const message = (payload && payload.error) || `HTTP ${response.status}`;
        const error = new Error(message);
        error.status = response.status;
        throw error;
    }
    return payload;
}

function query(params) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(params || {})) {
        if (value !== undefined && value !== null && value !== '') {
            search.set(key, value);
        }
    }
    const text = search.toString();
    return text ? `?${text}` : '';
}

export const api = {
    meta: () => request('/api/v2/meta'),

    streamers: (params) => request(`/api/v2/streamers${query(params)}`),
    addStreamer: (username, site) =>
        request('/api/v2/streamers', {
            method: 'POST',
            body: JSON.stringify({ username, site }),
        }),
    removeStreamer: (username, site) =>
        request(`/api/v2/streamers/${encodeURIComponent(username)}/${encodeURIComponent(site)}`, {
            method: 'DELETE',
        }),
    setStreamerRunning: (username, site, running) =>
        request(
            `/api/v2/streamers/${encodeURIComponent(username)}/${encodeURIComponent(site)}/${running ? 'start' : 'stop'}`,
            { method: 'POST' },
        ),
    setAllRunning: (running, streamers) =>
        request(`/api/v2/streamers/${running ? 'start' : 'stop'}`, {
            method: 'POST',
            body: JSON.stringify({ streamers: streamers || [] }),
        }),

    folders: (params) => request(`/api/v2/folders${query(params)}`),
    videos: (username, site, params) =>
        request(
            `/api/v2/folders/${encodeURIComponent(username)}/${encodeURIComponent(site)}/videos${query(params)}`,
        ),
    deleteVideo: (path) =>
        request(`/api/v2/videos/${encodePath(path)}`, { method: 'DELETE' }),
    toggleFavorite: (path) =>
        request(`/api/v2/favorites/${encodePath(path)}`, { method: 'POST' }),
    favorites: () => request('/api/v2/favorites'),
};

/** Percent-encode each segment of a relative path. */
function encodePath(path) {
    return String(path).split('/').map(encodeURIComponent).join('/');
}
