// Small DOM and formatting helpers shared by the views.

import { t } from './i18n.js';

/** Create an element. Text is always set via textContent, never innerHTML. */
export function el(tag, props = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(props)) {
        if (value === null || value === undefined || value === false) continue;
        if (key === 'class') node.className = value;
        else if (key === 'text') node.textContent = value;
        else if (key === 'html') throw new Error('raw html is not allowed');
        else if (key === 'dataset') Object.assign(node.dataset, value);
        else if (key === 'style') Object.assign(node.style, value);
        else if (key.startsWith('on') && typeof value === 'function') {
            node.addEventListener(key.slice(2).toLowerCase(), value);
        } else node.setAttribute(key, value === true ? '' : value);
    }
    for (const child of [].concat(children)) {
        if (child === null || child === undefined || child === false) continue;
        node.append(child.nodeType ? child : document.createTextNode(String(child)));
    }
    return node;
}

/** An inline SVG that shows one symbol of the sprite in the page. */
export function svgUse(name, className = '', viewBox = '') {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', className);
    if (viewBox) svg.setAttribute('viewBox', viewBox);
    svg.setAttribute('aria-hidden', 'true');
    const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
    use.setAttribute('href', `#${name}`);
    svg.append(use);
    return svg;
}

/** An icon. Decorative by default. */
export function icon(name, className = '') {
    return svgUse(name, `i i-${name} ${className}`.trim());
}

/**
 * An empty state: an illustration, a title and an optional hint.
 *
 * `art` is the symbol id, one of empty-folders, empty-videos,
 * empty-favorites, filmstrip.
 */
export function emptyState(art, title, hint = '', className = '') {
    return el('div', { class: `empty-state ${className}`.trim() }, [
        svgUse(art, 'empty-art', '0 0 96 64'),
        el('div', { class: 'empty-title', text: title }),
        hint ? el('div', { class: 'empty-hint', text: hint }) : null,
    ]);
}

export function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
    return node;
}

/** Format a byte count. */
export function bytes(value) {
    const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
    let size = Number(value) || 0;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) {
        size /= 1024;
        unit += 1;
    }
    const digits = unit === 0 ? 0 : size < 10 ? 2 : 1;
    return `${size.toFixed(digits)} ${units[unit]}`;
}

/** Format a duration in seconds as H:MM:SS. */
export function duration(seconds) {
    // Only an unknown duration gets the placeholder, a length of zero is 0:00.
    if (seconds === null || seconds === undefined || Number.isNaN(seconds) || seconds < 0) {
        return '--:--';
    }
    const total = Math.round(seconds);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

/** Relative time, e.g. "5 min temu", in the chosen language. */
export function relativeTime(timestamp) {
    if (!timestamp) return '—';
    const seconds = Date.now() / 1000 - timestamp;
    if (seconds < 60) return t('timeNow');
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return t('timeMinutes', minutes);
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return t('timeHours', hours);
    const days = Math.floor(hours / 24);
    if (days < 30) return t('timeDays', days);
    const months = Math.floor(days / 30);
    if (months < 12) return t('timeMonths', months);
    return t('timeYears', Math.floor(months / 12));
}

/** Debounce a function. */
export function debounce(fn, wait = 300) {
    let timer = null;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), wait);
    };
}

/** Map a status number to a css class and an icon name. */
export function statusClass(status) {
    const map = {
        200: 'st-public',
        404: 'st-offline',
        410: 'st-offline',
        403: 'st-private',
        1403: 'st-restricted',
        3: 'st-error',
        429: 'st-ratelimit',
        400: 'st-notexist',
        2: 'st-notrunning',
        1: 'st-unknown',
    };
    return map[status] || 'st-unknown';
}
