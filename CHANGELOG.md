# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Continuous integration hardened: least-privilege `permissions: contents: read`
  on every workflow (OpenSSF Scorecard *Token-Permissions*).

## [v0.1.0] - 2026-08-06

### Added
- Long-range autonomous coding agent orchestration and governance platform.
- Loop engine with checkpoint / resume, budget guardrails and full audit trail.
- FastAPI control plane, React console and CLI.
- Model-agnostic provider layer (self-hosted or commercial LLM APIs).

### Security
- CodeQL static analysis enabled.
- Least-privilege GitHub Actions token permissions on all workflows.

[Unreleased]: https://github.com/huzjie/loopforge/compare/v0.1.0...HEAD
[v0.1.0]: https://github.com/huzjie/loopforge/releases/tag/v0.1.0
