// ~/.goracle/config.json — single source of truth for the user's key + tier.
// Wraps `conf` so commands don't care about the underlying file path.

import Conf from "conf";

const store = new Conf({
  projectName: "goracle",
  // Lock the schema so a stale config from a future version doesn't crash an
  // older binary. Missing fields are filled at read time with defaults.
  defaults: {
    apiKey: null,        // the plaintext bearer token, shown ONCE at signup
    keyPrefix: null,     // human-readable prefix (e.g. "grad_abc12345")
    tier: "free",        // resolved tier — refreshed by `goracle me`
    tierLabel: null,     // pretty label for display ("Builder", "Pro")
    expiresAt: null,     // unix epoch; null = token-holder / no time bound
    apiBase: "https://graduateoracle.fun",  // overridable for self-hosted
    lastIntent: null,    // remember the last unfinished intent memo, so a
                          // crashed signup can be resumed with `goracle resume`
  },
});

export const config = {
  get(key) { return store.get(key); },
  set(key, value) { store.set(key, value); return value; },
  setAll(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
  clear() { store.clear(); },
  path() { return store.path; },
};
