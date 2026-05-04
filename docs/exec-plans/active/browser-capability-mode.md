# Browser Capability Mode ExecPlan

This ExecPlan is a living document. Update Progress, Surprises & Discoveries, Decision Log, and Outcomes as work proceeds.

## Purpose / Big Picture

Agents using `fb-cli` should not be limited to the reverse-engineered GraphQL subset. After this change, an agent can ask `fb-cli` to drive the same managed Chrome profile that a human browser uses: open Marketplace URLs, run searches through the web UI, scroll, read visible listings, inspect the current page, and run constrained JavaScript for diagnostics. The primary visible behavior is a new browser-backed command surface that works even when GraphQL filters such as sort encodings are unknown.

The tool remains read-first and agent-safe. It should not send seller messages, create listings, buy items, or perform irreversible Facebook actions.

## Progress

- [x] (2026-05-04 08:04Z) Fetch current project/auth status and create this plan.
- [ ] Add a reusable stdlib Chrome DevTools Protocol layer for page selection, navigation, evaluation, scrolling, and screenshots.
- [ ] Add `fb-cli browser ...` commands for Marketplace UI search, current-page extraction, navigation, scrolling, screenshots, and safe JavaScript evaluation.
- [ ] Document the new browser fallback in README, skill docs, changelog, and update version metadata.
- [ ] Validate with local compile checks and a live Marketplace smoke test against the managed Chrome profile.

## Surprises & Discoveries

- The repository has no `docs/PLANS.md`; this plan follows the local ExecPlan skill format directly.
- Current local status before implementation: branch `main` was clean and aligned with `origin/main`; package version is `0.2.1`; `fb-cli auth doctor` says cookies are valid and tokens refresh successfully; managed Chrome exists but was not running.

## Decision Log

- Decision: implement browser fallback as a separate `browser` command group instead of overloading `search` immediately. Rationale: keep the reliable GraphQL path stable while exposing the richer browser/UI path explicitly for agents.
- Decision: keep stdlib-only. Rationale: the project deliberately has no runtime dependencies; reuse/extract the existing minimal CDP client rather than adding Playwright/Selenium.
- Decision: default browser actions to read-only and require explicit `--unsafe` for arbitrary JavaScript. Rationale: this uses the user's real Facebook account.

## Outcomes & Retrospective

(fill when complete)

## Context and Orientation

Repository root: `/Users/minzi/Developer/fb-cli`.

Current relevant files:

- `fb_cli/cli.py`: argparse entrypoint and command handlers for `auth`, `search`, `listing`, `suggest`, and `watch`.
- `fb_cli/browser_auth.py`: existing Chrome DevTools Protocol implementation embedded in auth import. It includes target discovery, a private `_CDPClient`, page evaluation, and GraphQL request capture.
- `fb_cli/chrome_launcher.py`: managed Chrome lifecycle using `~/.fb-cli/chrome-profile/` and port `9222`.
- `fb_cli/parser.py`: flattens GraphQL listing responses.
- `fb_cli/format.py`: table/json/jsonl output helpers.
- `skill/SKILL.md`: agent-facing usage instructions mirrored into the Pi skill.
- `README.md` and `CHANGELOG.md`: user docs and release notes.

Definitions:

- CDP means Chrome DevTools Protocol, the JSON-RPC-ish browser automation protocol exposed by Chrome when launched with `--remote-debugging-port`.
- Managed Chrome means the dedicated browser profile owned by `fb-cli` at `~/.fb-cli/chrome-profile/`, separate from the user's normal Chrome profile.
- Browser fallback means extracting what the loaded Marketplace page shows instead of relying on direct `/api/graphql/` calls and captured `doc_id`s.

## Plan of Work

Milestone 1: Extract or create reusable CDP helpers. The goal is to let non-auth code connect to the managed Chrome without duplicating websocket framing. Move the minimal `_CDPClient` and target discovery concepts out of `browser_auth.py` into a reusable module or wrap them cleanly, while preserving auth behavior.

Milestone 2: Add browser commands. The goal is an agent-facing command group. Implement commands such as:

- `fb-cli browser open <url>` or `fb-cli browser search <query>` to navigate Marketplace.
- `fb-cli browser extract` to return visible Marketplace listing cards as JSON/JSONL/table.
- `fb-cli browser scroll --steps N` to load more UI results.
- `fb-cli browser screenshot [path]` for visual debugging.
- `fb-cli browser eval <expr> --unsafe` for constrained diagnostics only.

Milestone 3: Documentation and version. Explain when to use GraphQL vs browser fallback, how agents should avoid write actions, and how to recover auth/browser failures.

Milestone 4: Validation. Run Python compile checks and live commands against the managed Chrome. Validate that browser search can load `235/60R18` Marketplace results and return structured records.

## Concrete Steps

1. From repo root, confirm clean branch:

   ```bash
   git status --short --branch
   ```

   Expected: `## feat/browser-capability-mode` with only planned changes.

2. Implement reusable CDP/browser modules and CLI wiring.

3. Run static validation:

   ```bash
   python -m compileall fb_cli
   fb-cli --version
   fb-cli browser --help
   ```

   Expected: compile succeeds, version prints, help lists browser subcommands.

4. Run live smoke:

   ```bash
   fb-cli auth doctor
   fb-cli browser search "235/60R18" --limit 10 --format jsonl
   fb-cli browser scroll --steps 2
   fb-cli browser extract --limit 20 --format jsonl
   ```

   Expected: auth OK; browser search opens/uses managed Chrome; extraction returns JSONL listing records with URLs/titles/prices when visible.

## Validation and Acceptance

Acceptance criteria:

- Existing GraphQL commands still work.
- `fb-cli browser search "235/60R18" --format jsonl --limit 10` returns machine-readable records or a clear browser/auth recovery error.
- `fb-cli browser screenshot` writes a PNG file for visual debugging.
- README and skill docs tell agents to use browser fallback when GraphQL misses UI capabilities such as sort.
- No command sends Marketplace messages or performs purchases/listing creation.

## Idempotence and Recovery

- Managed Chrome startup is idempotent through `chrome_launcher.start()`, which no-ops if DevTools is already reachable.
- If a live Facebook tab is missing, browser commands should navigate to Marketplace automatically.
- If cookies are dead, commands should print the existing recovery tree: `fb-cli auth doctor`, `fb-cli auth refresh`, `fb-cli auth import-browser`, then ask the user to log in.
- Rollback: delete new files and remove CLI registrations, or reset the feature branch before merge. The auth file and Chrome profile are external state and must not be modified except through existing auth commands.
