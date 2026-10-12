# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Web UI no longer freezes on a network failure or a non-JSON error page: errors are shown in the conversation, the "…" placeholder is always removed, and Send is disabled while a request is in flight (no double submits).
- `/api/chat` rejects blank messages and messages over 2,000 characters with a 422 before any model call; validation errors now use the same `{"error": ...}` shape as other API errors, so the UI shows them.
- Accessibility: the message box has a label, the conversation is an ARIA live log, the status line is a live status, visible focus rings, and the log scrolls inside its box instead of growing the page. It also has an empty state.

### Security

- Web UI: `refund_payment` and `invoice_send` are held as pending actions and only run after an explicit human confirm (`/api/confirm`, `/api/cancel`).
- Optional `PAYPILOT_API_KEY` bearer auth on `/api/chat`, `/api/confirm`, `/api/cancel`; a real (sandbox/live) PayPal backend is refused without it.
- `paypilot.web` refuses non-loopback binds without a key (mock-only demo opt-in `PAYPILOT_ALLOW_UNAUTHENTICATED_DEMO=1`); `paypilot.mcp_server` refuses non-loopback binds unless `PAYPILOT_MCP_ALLOW_REMOTE=1`. systemd unit now uses `python -m paypilot.web` (host from env).

### Added

- CI workflow running pytest (mock PayPal backend) on pushes to `main` and on pull requests.
- Dependabot configuration for pip and GitHub Actions (weekly).
- `requirements.lock`: fully pinned, platform-independent dependency set generated with `uv pip compile --universal`.
- `.env.example` listing the environment variables PayPilot reads (names only).
- `.gitignore` for Python caches, virtualenvs and local `.env` files.
- SECURITY.md describing private vulnerability reporting.
- This CHANGELOG.
