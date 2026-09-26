# CLAUDE.md

Guidance for AI coding assistants working on this repository. Human contributors: see `CONTRIBUTING.md` once it exists.

## What this is

A Home Assistant custom integration for the ABUS Secvest alarm panel, installed via HACS as a custom repository. It talks to the panel's local REST API — the one the official app uses. That API is documented in the separate repository [`secvest-api`](https://github.com/thomasdanz/secvest-api) ("the specification"), which is expected as a sibling checkout at `../secvest-api` (e.g. for `scripts/sync_fixtures.py`).

## Read first

- `docs/architecture.md`: layers, modules, request handling, data flow, error handling, entity model.
- `docs/adr/`: decisions (HTTP client with TLS session resumption, API client as a subpackage, supported Home Assistant versions, fixtures, tested firmware).
- `docs/glossary.md`: use the manufacturer's terms (partition/Teilbereich, zone, omit/ausblenden, …).
- The backlog is in GitHub issues: epics with stories as sub-issues, milestones v0.1 → v1.0. Work on one story at a time.

## Hard rules

The panel is security equipment and fragile. These rules are not negotiable:

- **One connection, strictly sequential requests**, TLS session resumption on every reconnect (ADR 0001). No parallel requests, no `Connection: close`.
- **Never poll faster than the official app:** status interval at least 24 s, log rarely and incrementally.
- **Never retry after a 401.** Stop all requests and start reauthentication; failed logins may count towards a code tamper alarm.
- **Verify, don't assume:** after every state change, read the resulting state. Arming can fail with 409 or silently with 200; both are the same failure for the user.
- **No automatic retries of commands**, except the one documented case in the architecture.
- **No guessed requests:** only calls documented in the specification.
- **Logic never relies on panel texts** (`desc`, `ui-string`, names), except the documented entry delay detection.
- **The API client subpackage imports nothing from Home Assistant** (ADR 0002); a test enforces this.
- Unknown values from the panel are kept raw and logged once, never a crash.

## Working rules

- Everything in English: code, comments, docs, commits, issues.
- One story per branch and pull request. Definition of Done: tests pass and cover the change; README updated for user-visible changes; UI texts in English and German using the glossary; changelog entry.
- When a decision changes the design, update `docs/architecture.md` or add an ADR, **and** update the affected issues in the same step.
- Tests run against the specification's fixtures (copied by `scripts/sync_fixtures.py`, ADR 0004) and a fake panel; never against a real panel in CI.
- This repository is public: no installation-specific data (host names, IPs, codes, serial numbers, zone or room names), no credentials.

## Commands

To be added with the project skeleton (#45).
