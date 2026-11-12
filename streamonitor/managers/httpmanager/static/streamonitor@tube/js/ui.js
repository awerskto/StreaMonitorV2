// Shared UI pieces: toasts, the confirmation dialog and the add streamer form.

import { api } from './api.js';
import { clear, el, icon } from './dom.js';
import { t } from './i18n.js';

/** Show a transient message. */
export function toast(message, kind = 'info') {
    const stack = document.getElementById('toast-stack');
    if (!stack) return;
    const node = el('div', { class: `toast ${kind}`, text: message });
    stack.append(node);
    setTimeout(() => node.remove(), kind === 'error' ? 8000 : 4000);
}

/**
 * Ask the user to confirm something.
 * Resolves true when the action is confirmed.
 */
export function confirmAction(title, text = '', confirmLabel = t('confirm')) {
    const modal = document.getElementById('modal');
    if (!modal) return Promise.resolve(window.confirm(title));

    document.getElementById('modal-title').textContent = title;
    document.getElementById('modal-text').textContent = text;
    document.getElementById('modal-confirm').textContent = confirmLabel || t('confirm');
    document.getElementById('modal-cancel').textContent = t('cancel');

    return new Promise((resolve) => {
        const onClose = () => {
            modal.removeEventListener('close', onClose);
            resolve(modal.returnValue === 'confirm');
        };
        modal.addEventListener('close', onClose);
        modal.returnValue = 'cancel';
        modal.showModal();
    });
}

/**
 * The controls of the "add a streamer" form.
 *
 * They are placed into the toolbar of the accounts view, so the top bar
 * stays one row. `wire(form)` attaches the submit behaviour to the form
 * element that holds them.
 */
export function addStreamerControls(onAdded) {
    const username = el('input', {
        type: 'text',
        placeholder: t('username'),
        'aria-label': t('username'),
        required: true,
        minlength: '2',
    });
    // The placeholder option stays in place, so the form never submits a
    // site the user did not pick.
    const site = el('select', { 'aria-label': t('add') });
    const submit = el('button', { class: 'btn btn-primary', type: 'submit' }, [
        icon('plus'), t('add'),
    ]);

    const placeholder = el('option', { value: '', text: t('chooseSite') });
    site.append(placeholder);
    api.meta().then((meta) => {
        clear(site);
        site.append(placeholder);
        for (const [slug, name] of Object.entries(meta.sites)) {
            site.append(el('option', { value: slug, text: name }));
        }
    }).catch(() => { /* the form still submits whatever is selected */ });

    const wire = (form) => form.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (!username.value.trim() || !site.value) {
            toast(t('needUserAndSite'), 'error');
            return;
        }
        submit.disabled = true;
        try {
            await api.addStreamer(username.value.trim(), site.value);
            toast(`${t('add')}: ${username.value.trim()}`, 'success');
            username.value = '';
            if (onAdded) onAdded();
        } catch (error) {
            toast(t('failedToAdd', error.message), 'error');
        } finally {
            submit.disabled = false;
        }
    });

    return { username, site, submit, wire };
}