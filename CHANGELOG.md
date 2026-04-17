# Changelog

All notable changes to Helix Agent will be documented here.

## [Unreleased]

## [1.1.7] - 2026-04-16

### Fixed
- **Haiku model ID** — changed from `claude-haiku-4-5-20251022` to `claude-haiku-4-5` to match the naming convention of all other models (`claude-sonnet-4-6`, `claude-opus-4-6`). The date-suffixed ID was causing haiku to fail silently.

## [1.1.6] - 2026-04-16

### Changed
- **Platform support** — macOS removed as a supported platform. Too many native dependency build issues without significant friction. Linux (Ubuntu/Debian) is the primary supported platform. Windows via WSL is untested but should work.
- Removed Mac-specific troubleshooting entries from README

## [1.1.5] - 2026-04-16

### Fixed
- **`No module named 'argon2'` on Mac** — lowered `argon2-cffi` version pin from `>=23.1.0` to `>=21.3.0` for broader Mac wheel compatibility, same root cause as the v1.1.4 cryptography fix.

### Docs
- Combined `cryptography` and `argon2` into a single Mac troubleshooting entry in README

## [1.1.4] - 2026-04-16

### Fixed
- **`No module named 'cryptography'` on Mac** — lowered `cryptography` version pin from `>=46.0.0` to `>=41.0.0`. v46 is very new and lacks pre-built wheels for many macOS + Python combinations, causing pip to attempt a source build that fails without Rust/Xcode tools. v41+ has broad Mac wheel coverage and is fully compatible.

### Docs
- Added `No module named 'cryptography'` troubleshooting entry to README with fix steps and Xcode CLI tools note

## [1.1.3] - 2026-04-16

### Changed
- **`/status`** — removed max output tokens line (static model cap, not useful as a runtime metric)

## [1.1.2] - 2026-04-16

### Added
- **Context window tracking in `/status`** — `/status` now shows real-time context window usage (`17,234 / 200,000 (8.6%)`) and max output tokens, extracted from the Claude CLI's `modelUsage` response on every turn. Previously only showed a static estimated token count. Useful for knowing when you're approaching context limits and should `/compact`.

### Changed
- `cli_backend.py` — `call_claude()` now returns a 3-tuple `(response_text, session_id, usage_dict)` containing per-model token stats from the CLI JSON response
- `session_manager.py` — added `context_window` and `max_output_tokens` columns (auto-migrated); `update_activity()` now accepts and persists usage data
- `agent_loop.py` — passes usage dict from `call_claude()` through to `update_activity()`

## [1.1.1] - 2026-04-16

### Fixed
- **Model dropdown confirmation message** — switching models via the web UI dropdown now displays a `Model switched to \`{alias}\`` confirmation message in the chat, matching the feedback already shown when using the `/model` slash command. Previously the switch happened silently with no visible indication beyond running `/status`.

## [1.1.0] - 2026-04-16

### Added
- **Discord status indicators** — messages now show `thinking...` immediately on receipt, switching to `working on it...` after 4 seconds, then replaced with the final reply. Native Discord "[agent name] is typing..." indicator fixed and running alongside.
- **File attachment support (Discord + Telegram)** — drop any file into Discord or Telegram and the agent receives it. 5MB cap, blacklist for large disk images (`.iso`, `.dmg`, `.vmdk`, etc.). All code and script types including `.sh`, `.exe`, `.py` are fully supported.
- **New `core/file_handler.py`** — centralized file validation, save-to-temp, cleanup, and context injection. Temp files written to `/tmp/helix_uploads/` and cleaned up automatically after each response.
- **Preflight checks in `start.sh`** — on every start, Helix now verifies: venv exists, Python 3.10+, Claude Code CLI installed, all `requirements.txt` packages present and correct version. Clear error messages with exact fix commands on any failure.

### Changed
- Discord slash commands (`/status`, `/model`, `/help`, etc.) skip status indicators and respond instantly — no `thinking...` message for commands that don't hit the agent loop.
- On `NO_REPLY` the `thinking...` placeholder is silently deleted rather than left in the channel.

## [1.0.6] - 2026-04-15

### Fixed
- **Model dropdown alias resolution** — the dropdown was showing the wrong model because the session stores a full model ID (e.g. `claude-haiku-4-5-20251022`) but the dropdown uses aliases (e.g. `haiku`). Now resolves the session model ID against the roster to find the correct alias before syncing the dropdown.
- **Config/session load race condition** — `loadConfig()` and `loadSessions()` were running in parallel, so the model roster wasn't available when alias resolution ran. Config now loads first, then sessions resolves correctly against the populated roster.

## [1.0.5] - 2026-04-15

### Fixed
- **Model dropdown not reflecting current session model** — the dropdown was always initialized from the config default, ignoring what model the session was actually running on. Now reads the session's current model after load and syncs the dropdown to match on every page load.

## [1.0.4] - 2026-04-15

### Fixed
- **`/compact → /model` context loss bug** — `set_model()` was unconditionally overwriting `pending_context` with a fresh transcript-derived block, destroying any compaction summary that `/compact` had just stored. Now checks for existing `pending_context` first and only falls back to `_build_context_block()` when none exists. The recommended workflow (`/compact` then `/model`) now works as documented.

## [1.0.3] - 2026-04-14

### Added
- **`/compact` slash command** — compresses the current session history into a concise summary using the lightest available model (haiku). The summary is stored as pending context and injected on the next turn so the agent picks up exactly where it left off. Works in Discord, Telegram, and the web chat — no terminal required. Overwrites the local transcript with the summary and increments the compaction counter.

## [1.0.2] - 2026-04-14

### Added
- **Context preservation on model switch** — when switching models via `/model` or the web UI dropdown, Helix now captures the last 30 user/assistant exchanges and injects them as context on the first turn of the new session. Previously switching models would silently drop all in-conversation context.

### Changed
- `CONTEXT_EXCHANGES = 30` constant in `session_manager.py` controls how many exchange pairs are carried forward (configurable)

## [1.0.1] - 2026-04-14

### Fixed
- **Slash commands in web UI** — `/help`, `/model`, `/status` and all other slash commands now work in the web chat. Previously they were passed to the agent as plain text instead of being intercepted by the command handler.
- **Model switcher dropdown** — Selecting a model in the web UI chat now actually switches the model for that session. Previously the dropdown sent a per-message override that was ignored when `--resume` was active. The fix persists the model to the session and clears the Claude Code session ID so the next turn starts fresh with the selected model.
- **Disconnected status flash** — Removed the yellow "disconnected" indicator that flashed briefly during normal message/reply turns. The WebSocket connection state is still tracked internally but no longer shown in the UI.

## [1.0.0] - 2026-04-13

### Added
- Initial public release
- Claude Code CLI backend — no API key required, uses your Claude Pro/Max subscription via OAuth
- Discord and Telegram adapters
- Web admin UI with real-time chat, session management, config editor, log viewer, and cron scheduler
- Persistent agent identity via workspace files (SOUL.md, IDENTITY.md, USER.md, MEMORY.md, etc.)
- Memory system — daily logs + long-term MEMORY.md
- Custom slash commands via `commands/` directory
- APScheduler-backed cron system for scheduled tasks and heartbeats
- Prompt caching + `--resume` for token-efficient session continuity
- Encrypted secrets store (Fernet + argon2id)
- Allowlist-based auth for Discord and Telegram
- Rate limiting (20/min, 200/hr per user)
- Prompt injection detection
- Interactive first-run setup wizard
- MIT license
