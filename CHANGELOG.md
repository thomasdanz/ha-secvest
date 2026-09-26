# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Project skeleton: integration package with the API client subpackage, HACS metadata and development tooling (#45).
- MIT license (#57).
- Continuous integration: linting, type checks, tests against Home Assistant 2026.9 and 2026.8, hassfest, HACS validation and a weekly run against the upcoming release (#47).
- API client: lenient parsing of the panel's responses; unknown states and types are kept and logged once (#5).
- API client: every known panel response is mapped to a result or a typed error, including the installer lock and refused arming with its blocking faults (#4).
- Tests: a simulated panel that behaves like the reference panel, including its connection behaviour, and fails tests that break the panel's rules (#65).
- API client: one HTTPS connection per panel with TLS session resumption, strictly sequential requests with commands ahead of polling, a User-Agent that can be overridden, and connection counters for diagnostics (#2).
- API client: after the panel rejects the credentials (401), no further request is sent with them, not even queued ones (#6).
- API client: one method per API operation the integration uses, returning models (#75).
- Setup in the UI: address, user code, password, certificate verification and an optional User-Agent, checked with a single request; English and German texts (#38).
- Setup in the UI: selection of the partitions; partitions without zones are deselected by default (#39).
- Status polling every 30 s (never below 24 s) of partitions, alarms, faults and the selected partitions' zones (#10).
- Backoff after failed polling rounds, doubling up to 5 minutes, and a 15-minute pause after 5 failures in a row; entities stay available until the pause starts (#7).
- Installer lock: a diagnostic binary sensor on the panel device; while the installer is logged in, entities keep their last state and each polling round costs a single request (#21).
- Reauthentication: after the panel rejects the credentials nothing is sent with them anymore, also after a restart, until new credentials are entered and checked with a single request (#41, #6).
- Alarm panel per selected partition showing its state, including triggered and acknowledged alarms; arming follows later (#15).
- One device per zone below the panel device, with a binary sensor for open/closed and the zone's details as attributes (#24, #26).
- Problem sensor per zone for tamper and fault states and for faults affecting the zone (#25).
- Faults sensor with all current faults as attributes and a readable summary, and a problem sensor on the panel device that ignores open zones (#29).
