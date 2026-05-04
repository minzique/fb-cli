# Changelog

## 0.2.0

Self-managed auth that stays alive without you babysitting it.

### How Facebook auth actually works (now exploited end-to-end)

- Long-lived signed cookies (`xs`, `c_user`, `datr`, `fr`, `sb`) are the real
  persistent session — `xs` carries `Max-Age=31536000` (~1 year) when you
  check "Save login info" at login.
- `fb_dtsg` and `lsd` are page-bound CSRF tokens embedded in the HTML of every
  facebook.com page; they rotate every few hours but are trivially
  re-derivable from any successful page load using the long-lived cookies.

### Added

- **`fb-cli auth chrome (start|stop|status|login)`** — fb-cli now owns its
  own debug Chrome at `~/.fb-cli/chrome-profile/`, separate from the user's
  normal browser. `chrome login` opens FB's login page in it. One-time setup,
  good for ~1 year.
- **`fb-cli auth import-browser` auto-launches the managed Chrome** if none
  is reachable on `:9222`. New flags: `--no-launch`, `--copy-profile` (seed
  cookies from the user's default Chrome on first init).
- **`fb-cli auth refresh`** — pure-HTTPS token refresh. Single GET to
  `/marketplace/`, scrapes fresh `fb_dtsg` / `lsd` / `__rev` / `__hsi` /
  `__hs` from the HTML, recomputes `jazoest`. No browser needed. Handles ~95%
  of "auth expired" errors.
- **`fb-cli auth doctor`** — diagnoses auth state and prints the cheapest
  recovery action.
- **Transparent in-flight retry** in `client.graphql` — stale-token errors
  trigger a `cookie_refresh.refresh()` call and one retry, then surface a
  structured error agents can act on.

### Changed

- Skill (`skill/SKILL.md`) gained an explicit AUTH RECOVERY tree so agents
  follow `doctor → refresh → import-browser → ask user` instead of bailing
  on the first failure.
- Better error hints throughout — every auth error now points at the cheapest
  next command.

## 0.1.0

Initial release.
- HAR-imported cookie auth (`fb-cli auth import-har`)
- `search`, `listing`, `suggest`, `watch` (saved searches with new-listing diff)
- Stdlib-only Python; no runtime deps.
