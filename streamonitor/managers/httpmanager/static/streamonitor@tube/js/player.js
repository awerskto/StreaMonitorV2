// The video player.
//
// Keyboard: space play/pause, arrows seek, f fullscreen, m mute,
// up/down volume. The volume is remembered between videos.

import { api } from './api.js';
import { bytes, clear, duration, el, icon, relativeTime, svgUse } from './dom.js';
import { t } from './i18n.js';
import { confirmAction, toast } from './ui.js';

const VOLUME_KEY = 'sm.tube.volume';
const SPEED_KEY = 'sm.tube.speed';

// Removes the keyboard listener of the player that is being replaced.
let activeCleanup = null;

function storedNumber(key, fallback) {
    try {
        const value = localStorage.getItem(key);
        const parsed = parseFloat(value);
        return Number.isFinite(parsed) ? parsed : fallback;
    } catch (error) {
        return fallback;
    }
}

function storeNumber(key, value) {
    try {
        localStorage.setItem(key, String(value));
    } catch (error) {
        /* private mode */
    }
}

/**
 * The player panel.
 * `onChange` is called when the current video changes.
 */
export function createPlayer(container, onChange) {
    const video = el('video', {
        controls: true,
        preload: 'metadata',
        playsinline: true,
    });

    const placeholder = el('div', { class: 'placeholder' }, [
        svgUse('empty-videos', 'placeholder-art', '0 0 96 64'),
        el('span', { class: 'placeholder-text', text: t('pickRecording') }),
    ]);

    const frame = el('div', { class: 'player-frame' }, [placeholder]);

    const title = el('div', { class: 'player-title', text: '' });
    const subtitle = el('div', { class: 'player-sub', text: '' });

    const favorite = el('button', {
        class: 'btn btn-sm',
        type: 'button',
        onclick: () => toggleFavorite(),
    }, [icon('star'), t('favorites')]);

    const remove = el('button', {
        class: 'btn btn-sm btn-ghost',
        type: 'button',
        onclick: () => removeCurrent(),
    }, [icon('trash')]);

    const download = el('a', {
        class: 'btn btn-sm',
        download: '',
        href: '#',
    }, [icon('refresh'), t('download')]);

    const quality = el('select', { 'aria-label': t('quality') });
    const speed = el('select', { 'aria-label': t('speed') });

    const timeLabel = el('span', { class: 'player-time', text: '0:00 / 0:00' });

    const autoplay = el('input', {
        type: 'checkbox',
        id: 'autoplay-next',
        checked: true,
    });

    const bar = el('div', { class: 'player-bar' }, [
        el('div', {}, [title, subtitle]),
        el('span', { class: 'spacer' }),
        el('div', { class: 'player-controls' }, [
            timeLabel,
            quality,
            speed,
            favorite,
            download,
            remove,
            el('label', { class: 'btn btn-sm btn-ghost', for: 'autoplay-next' }, [
                autoplay, t('autoplay'),
            ]),
        ]),
    ]);

    const panel = el('div', { class: 'player' }, [frame, bar]);
    container.append(panel);

    let current = null;

    // --- speed ------------------------------------------------------------
    for (const value of [0.5, 0.75, 1, 1.25, 1.5, 2]) {
        speed.append(el('option', { value: String(value), text: `${value}x` }));
    }
    const savedSpeed = storedNumber(SPEED_KEY, 1);
    speed.value = String(savedSpeed);
    video.playbackRate = savedSpeed;
    speed.addEventListener('change', () => {
        video.playbackRate = parseFloat(speed.value) || 1;
        storeNumber(SPEED_KEY, video.playbackRate);
    });

    // --- volume -----------------------------------------------------------
    video.volume = storedNumber(VOLUME_KEY, 1);
    video.addEventListener('volumechange', () => {
        storeNumber(VOLUME_KEY, video.volume);
    });

    // --- keyboard ---------------------------------------------------------
    // The shortcuts listen on the document: the video element swallows the
    // keys, because it draws its own controls.
    const onKeydown = (event) => {
        const target = event.target;
        if (target && (target.matches('input, select, textarea') ||
                       target.isContentEditable)) {
            return;
        }
        if (event.ctrlKey || event.altKey || event.metaKey) return;

        const step = event.shiftKey ? 60 : 10;
        switch (event.key) {
            case ' ':
            case 'k':
                event.preventDefault();
                togglePlay();
                break;
            case 'ArrowLeft':
                event.preventDefault();
                seekBy(-step);
                break;
            case 'ArrowRight':
                event.preventDefault();
                seekBy(step);
                break;
            case 'f':
                event.preventDefault();
                toggleFullscreen();
                break;
            case 'm':
                event.preventDefault();
                video.muted = !video.muted;
                break;
            case 'ArrowUp':
                event.preventDefault();
                video.volume = Math.min(1, video.volume + 0.05);
                break;
            case 'ArrowDown':
                event.preventDefault();
                video.volume = Math.max(0, video.volume - 0.05);
                break;
            default:
                break;
        }
    };
    document.addEventListener('keydown', onKeydown);
    // Only one player may listen, so the previous one has to stop.
    if (activeCleanup) activeCleanup();
    activeCleanup = () => document.removeEventListener('keydown', onKeydown);

    video.addEventListener('timeupdate', () => {
        timeLabel.textContent = `${duration(video.currentTime)} / ${duration(video.duration)}`;
    });

    // The duration is known before the playback starts.
    video.addEventListener('loadedmetadata', () => {
        timeLabel.textContent = `${duration(video.currentTime)} / ${duration(video.duration)}`;
    });

    video.addEventListener('ended', () => {
        if (autoplay.checked && onChange) onChange('next');
    });

    function togglePlay() {
        if (video.paused) video.play().catch(() => { /* autoplay may be blocked */ });
        else video.pause();
    }

    function seekBy(delta) {
        video.currentTime = Math.max(0, Math.min(video.duration || 0, video.currentTime + delta));
    }

    function toggleFullscreen() {
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
        else frame.requestFullscreen().catch(() => {});
    }

    async function toggleFavorite() {
        if (!current) return;
        try {
            const state = await api.toggleFavorite(current.path);
            current.favorite = state.favorite;
            updateFavoriteButton();
            if (onChange) onChange('favorite');
        } catch (error) {
            toast(t('failedToSaveFav', error.message), 'error');
        }
    }

    function updateFavoriteButton() {
        const on = Boolean(current && current.favorite);
        favorite.classList.toggle('btn-primary', on);
        favorite.setAttribute('aria-label', on
            ? 'Usuń z ulubionych'
            : 'Dodaj do ulubionych');
        clear(favorite).append(icon('star'), t('favorites'));
    }

    async function removeCurrent() {
        if (!current) return;
        const confirmed = await confirmAction(
            t('deleteTitle', current.filename),
            'Operacja jest nieodwracalna: plik, miniatura, podgląd i proxy zostaną skasowane.',
            'Usuń',
        );
        if (!confirmed) return;
        try {
            await api.deleteVideo(current.path);
            toast(t('deleted', current.filename), 'success');
            if (onChange) onChange('deleted');
        } catch (error) {
            toast(t('failedToDelete', error.message), 'error');
        }
    }

    /** Show a video. */
    function load(item, list, autoplay = false) {
        current = item;
        list = list || [];

        clear(frame).append(video);
        video.src = item.stream_url;
        video.poster = item.poster_url || '';
        video.load();
        if (autoplay) {
            // The browser may refuse, then the user has to press play.
            video.play().catch(() => { /* the autoplay may be blocked */ });
        }

        title.textContent = item.filename;
        subtitle.textContent = [
            relativeTime(item.mtime),
            duration(item.duration),
            bytes(item.size),
            item.width && item.height ? `${item.width}x${item.height}` : null,
        ].filter(Boolean).join(' · ');

        download.href = item.stream_url;
        download.setAttribute('download', item.filename);

        // Quality: original or proxy when one exists.
        clear(quality);
        quality.append(el('option', { value: 'original', text: t('original') }));
        if (item.proxy_url) {
            quality.append(el('option', { value: 'proxy', text: t('proxy360') }));
        }
        quality.value = item.proxy_url && item.useProxy ? 'proxy' : 'original';
        const applyQuality = () => {
            const useProxy = quality.value === 'proxy' && item.proxy_url;
            video.src = useProxy ? item.proxy_url : item.stream_url;
            video.load();
        };
        quality.onchange = applyQuality;

        updateFavoriteButton();

        if (onChange) onChange('loaded', item);
    }

    return {
        load,
        element: panel,
        get current() { return current; },
        /** True when the next recording should start on its own. */
        autoplayEnabled: () => autoplay.checked,
    };
}
