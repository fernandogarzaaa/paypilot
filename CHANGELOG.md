# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- CI workflow running pytest (mock PayPal backend) on pushes to `main` and on pull requests.
- Dependabot configuration for pip and GitHub Actions (weekly).
- `requirements.lock`: fully pinned, platform-independent dependency set generated with `uv pip compile --universal`.
- `.env.example` listing the environment variables PayPilot reads (names only).
- `.gitignore` for Python caches, virtualenvs and local `.env` files.
- SECURITY.md describing private vulnerability reporting.
- This CHANGELOG.
