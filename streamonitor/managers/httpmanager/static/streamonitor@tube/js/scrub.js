// The scrub storyboard.
//
// One sprite holds a frame every 5% of the recording. The lower part of a
// thumbnail is the active zone: moving the pointer through it shows the
// frame of that position, outside of it the poster is shown.
//
// The position is always measured across the whole width of the card, so a
// portrait video with empty pillars scrubs the same way as a landscape one.

/** The frame index for a horizontal position. */
export function frameIndex(x, width, frames) {
    if (width <= 0 || frames <= 0) return 0;
    let index = Math.floor((x / width) * frames);
    if (Number.isNaN(index)) return 0;
    return Math.max(0, Math.min(frames - 1, index));
}

/**
 * The background position of one cell of the sprite.
 *
 * The edges have to land exactly on 0% and 100%, otherwise the last column
 * or row would be cut off. With 5 columns the positions are 0, 25, 50, 75
 * and 100.
 */
export function cellPosition(index, cols, rows) {
    const col = index % cols;
    const row = Math.floor(index / cols);
    const x = cols > 1 ? (col / (cols - 1)) * 100 : 50;
    const y = rows > 1 ? (row / (rows - 1)) * 100 : 50;
    return { x, y };
}

/** A readable time, 1:02:03 or 02:03. */
export function clock(seconds) {
    if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '--:--';
    const total = Math.max(0, Math.round(seconds));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    const pad = (n) => String(n).padStart(2, '0');
    return h ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/**
 * Attach the scrub behaviour to a thumbnail element.
 *
 * `video` carries the sprite: {sprite_url, sprite_cols, sprite_rows,
 * cell_w, cell_h, display_w, display_h, duration, poster_url}.
 * Returns the element that shows the frames.
 */
export function attachScrub(thumb, video, zone = 0.30) {
    const frames = Math.max(1, video.sprite_cols * video.sprite_rows);
    const cols = video.sprite_cols;
    const rows = video.sprite_rows;
    const duration = video.duration || 0;

    const frame = document.createElement('div');
    frame.className = 'scrub-frame';
    if (video.display_w && video.display_h) {
        frame.style.aspectRatio = `${video.display_w} / ${video.display_h}`;
    }

    const bar = document.createElement('div');
    bar.className = 'scrub-bar';
    const segments = [];
    for (let i = 0; i < frames; i += 1) {
        const segment = document.createElement('span');
        bar.append(segment);
        segments.push(segment);
    }

    const label = document.createElement('span');
    label.className = 'scrub-label';

    const overlay = document.createElement('div');
    overlay.className = 'scrub-overlay';
    overlay.append(frame, bar, label);
    overlay.hidden = true;

    const image = new Image();
    image.decoding = 'async';
    let ready = false;
    let loading = false;
    const preload = () => {
        if (ready || loading || !video.sprite_url) return;
        loading = true;
        image.onload = () => { ready = true; };
        image.onerror = () => { loading = false; };
        image.src = video.sprite_url;
    };

    let state = { overlay, preload, index: 0 };

    // The style of the frame: the sprite is shifted so that the wanted cell
    // fills the box.
    /** The frame keeps the proportions of the video and fits above the bar. */
    const sizeFrame = () => {
        const rect = thumb.getBoundingClientRect();
        // The bar and the label have their final size here: the text of the
        // label is set before this runs.
        const barHeight = bar.getBoundingClientRect().height || 4;
        const labelHeight = label.getBoundingClientRect().height || 18;
        // The padding of the overlay and the two gaps between the children.
        const chrome = barHeight + labelHeight + 16;
        const availableHeight = Math.max(0, rect.height - chrome);
        const availableWidth = rect.width - 4;
        const aspect = (video.display_w && video.display_h)
            ? video.display_w / video.display_h : (video.cell_w / video.cell_h);
        let width = availableHeight * aspect;
        if (width > availableWidth) width = availableWidth;
        if (!(width > 0)) width = rect.width;
        frame.style.width = `${Math.round(width)}px`;
        frame.style.height = `${Math.round(width / aspect)}px`;
    };

    const show = (index) => {
        state.index = index;
        const { x, y } = cellPosition(index, cols, rows);
        frame.style.backgroundImage = `url("${video.sprite_url}")`;
        frame.style.backgroundSize = `${cols * 100}% ${rows * 100}%`;
        frame.style.backgroundPosition = `${x}% ${y}%`;
        overlay.hidden = false;
        const start = (index / frames) * duration;
        const end = ((index + 1) / frames) * duration;
        label.textContent = `${clock(start)} – ${clock(end)}`;
        // The frame is sized after the label, its height depends on it.
        sizeFrame();
        for (let i = 0; i < segments.length; i += 1) {
            segments[i].classList.toggle('on', i === index);
        }
    };

    let hideTimer = null;
    const hide = () => {
        clearTimeout(hideTimer);
        hideTimer = setTimeout(() => { overlay.hidden = true; }, 100);
    };
    const cancelHide = () => {
        if (hideTimer) clearTimeout(hideTimer);
        hideTimer = null;
    };

    /** Where inside the card the pointer is, and is it in the zone. */
    const locate = (event) => {
        const rect = thumb.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const y = event.clientY - rect.top;
        return {
            x,
            width: rect.width,
            inZone: y >= rect.height * (1 - zone),
            outside: x < 0 || y < 0 || x > rect.width || y > rect.height,
        };
    };

    let dragging = false;
    let moved = 0;
    let startX = 0;
    let startY = 0;

    const onPointerDown = (event) => {
        const { inZone, outside } = locate(event);
        if (outside || !inZone) return;
        event.preventDefault();
        dragging = true;
        moved = 0;
        startX = event.clientX;
        startY = event.clientY;
        thumb.setPointerCapture(event.pointerId);
        onPointerMove(event);
    };

    const onPointerMove = (event) => {
        const { x, width, inZone, outside } = locate(event);
        if (outside) { hide(); return; }
        if (dragging) {
            moved = Math.max(moved,
                Math.hypot(event.clientX - startX, event.clientY - startY));
            show(frameIndex(x, width, frames));
            return;
        }
        if (!inZone) { hide(); return; }
        preload();
        cancelHide();
        show(frameIndex(x, width, frames));
    };

    const onPointerUp = (event) => {
        if (!dragging) return;
        dragging = false;
        try { thumb.releasePointerCapture(event.pointerId); } catch (error) { /* released */ }
        // A drag is not a click: the navigation has to stay out of it.
        if (moved > 8) {
            event.preventDefault();
            event.stopPropagation();
            hide();
        }
    };

    const onPointerLeave = () => { hide(); };

    const onKeydown = (event) => {
        if (document.activeElement !== thumb) return;
        if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
        event.preventDefault();
        const current = Number(thumb.dataset.scrubIndex || 0);
        const next = Math.max(0, Math.min(frames - 1,
            current + (event.key === 'ArrowRight' ? 1 : -1)));
        thumb.dataset.scrubIndex = String(next);
        preload();
        show(next);
    };

    thumb.addEventListener('pointerdown', onPointerDown);
    thumb.addEventListener('pointermove', onPointerMove);
    thumb.addEventListener('pointerup', onPointerUp);
    thumb.addEventListener('pointercancel', onPointerUp);
    thumb.addEventListener('pointerleave', onPointerLeave);
    thumb.addEventListener('keydown', onKeydown);

    // The tests read the chosen frame from here.
    thumb.scrub = {
        get index() { return state.index; },
        show(i) { state.index = i; show(i); },
        hide,
        preload,
        remove() {
            thumb.removeEventListener('pointerdown', onPointerDown);
            thumb.removeEventListener('pointermove', onPointerMove);
            thumb.removeEventListener('pointerup', onPointerUp);
            thumb.removeEventListener('pointercancel', onPointerUp);
            thumb.removeEventListener('pointerleave', onPointerLeave);
            thumb.removeEventListener('keydown', onKeydown);
            overlay.remove();
        },
    };
    thumb.append(overlay);
    return state;
}
