// The language switcher of the sidebar footer.
//
// A button with the flag of the active language opens a list of the
// languages. Choosing one stores it and the application draws itself again.

import { LANGUAGES, getLanguage, languageByCode, languages, setLanguage, t } from './i18n.js';
import { clear, el, svgUse } from './dom.js';

/** Build the list and the behaviour of the switcher. */
export function initLanguageSwitcher() {
    const root = document.getElementById('lang');
    const button = document.getElementById('lang-toggle');
    const menu = document.getElementById('lang-menu');
    const flag = document.getElementById('lang-flag');
    if (!root || !button || !menu || !flag) return;

    const renderFlag = () => {
        const language = languageByCode(getLanguage());
        // A drawn flag instead of the emoji: it renders the same everywhere.
        clear(flag).append(language ? svgUse(`flag-${language.code}`, 'flag-svg') : null);
        button.setAttribute('aria-label', t('language'));
        button.title = language ? language.name : 'English';
    };

    const renderMenu = () => {
        clear(menu);
        for (const language of languages()) {
            const item = el('button', {
                class: `lang-item ${language.code === getLanguage() ? 'active' : ''}`,
                type: 'button',
                role: 'menuitem',
                dataset: { code: language.code },
                lang: language.code,
                onclick: () => {
                    setLanguage(language.code);
                    close();
                },
            }, [
                el('span', { class: 'lang-item-flag' },
                    svgUse(`flag-${language.code}`, 'flag-svg')),
                el('span', { class: 'lang-item-name', text: language.name }),
            ]);
            menu.append(item);
        }
    };

    const open = () => {
        renderMenu();
        menu.hidden = false;
        button.setAttribute('aria-expanded', 'true');
    };

    const close = () => {
        menu.hidden = true;
        button.setAttribute('aria-expanded', 'false');
    };

    button.addEventListener('click', (event) => {
        event.stopPropagation();
        menu.hidden ? open() : close();
    });

    // A click outside closes the list.
    document.addEventListener('click', (event) => {
        if (!root.contains(event.target)) close();
    });

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') close();
    });

    renderFlag();
    // The label of the button follows the language.
    document.addEventListener('sm:language', renderFlag);
}

/** The available languages, for tests and for the list. */
export function availableLanguages() {
    return [...LANGUAGES];
}
