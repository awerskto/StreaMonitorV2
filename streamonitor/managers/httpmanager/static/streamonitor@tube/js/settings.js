// The settings the interface reads once from the meta endpoint.
//
// A module with a mutable object: the views read it at call time, so no
// window global and no reload is needed.

export const settings = {
    scrub: true,
    scrubZone: 0.30,
    listFrequency: 30,
};

/** Copy the values of a meta response into the module. */
export function applyMeta(meta) {
    if (!meta) return settings;
    const from = meta.settings || {};
    if (typeof from.scrub === 'boolean') settings.scrub = from.scrub;
    if (typeof from.scrub_zone === 'number' && from.scrub_zone > 0
            && from.scrub_zone <= 1) {
        settings.scrubZone = from.scrub_zone;
    }
    if (typeof from.list_frequency === 'number') {
        settings.listFrequency = from.list_frequency;
    }
    return settings;
}
