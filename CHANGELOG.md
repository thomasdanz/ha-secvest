# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.2.0] - 2026-09-28

Control: arming, disarming, acknowledging alarms and omitting zones, each checked by reading the panel again.

### Added

- Arming (away and home) and disarming from the alarm panel, each checked by reading the partition again; failures raise one message with the reason and fire the event `secvest_arming_failed` (#16, #17).
- Switching between armed away and armed home disarms first and then arms again, each step checked; the alarm panel shows the previous mode until the switch is done (#18).
- Alarm details on the alarm panel: the alarm type (in the panel's own terms, e.g. "Burglar alarm" / "Einbruchalarm") and the zones that raised it; a failing alarm list no longer fails the whole polling round (#19).
- Disarming during an alarm acknowledges it first and then disarms, each step checked; arming during an alarm isn't sent. Not tested at a real panel, since that would need an alarm (#20).
- Before a command sequence the partition is read again, so what is sent first depends on its current state, not on the last polling round.
- Omit switch per omittable zone, checked by reading the zone again; the message tells a zone that can't be omitted from missing rights (#27).
- README: why the alarm panel shows no arming or pending state (#22).

### Upgrade notes

- Nothing to do. New entities appear by themselves: an omit switch per omittable zone (hidden for grouped zones if the group hides its zones).

## [0.1.10] - 2026-09-28

### Changed

- Zone groups: no zone is preselected anymore, and the type defaults to "Same as the zones", which takes the type the zones show and follows it. A zone group can be given an area when it is added or reconfigured.
- Changing options or zone groups reloads at once, using the last round's result, instead of waiting up to 24 seconds for a new round; the next group can be added right away.

## [0.1.9] - 2026-09-27

### Added

- Zone groups: combine zones of one opening into a group with its own device and a sensor that is on while any of them is open; optionally hide the grouped zones' entities. Added, changed and deleted on the integration's page; a repair issue reports zones of a group that no longer exist (#67).

### Upgrade notes

- Nothing to do. If you built such groups yourself (e.g. group helpers), delete them before adding the same group here, so the new sensor gets the same entity id.

## [0.1.8] - 2026-09-27

### Added

- Updates without setting up again are tested: the entry as each released version stored it loads with the current code and keeps every entity. Stored data is versioned and migrated; entities of kinds that no longer exist are removed (#103).

### Upgrade notes

- Nothing to do: entries of earlier versions are migrated at the first start.

## [0.1.7] - 2026-09-27

### Added

- Setup ends with the zones step of the options: a device class per zone and excluded zones (#101).

## [0.1.6] - 2026-09-27

### Added

- Open zones sensor per selected partition: the number of open, not omitted zones.

### Changed

- Faults no longer count open zones, which the panel lists as faults even when disarmed; Problem is on while Faults is above 0.
- Arming blocked shows "Blocked" / "Possible" instead of "Problem" / "OK", and is on while open zones is above 0 (open entry doors included, which the panel doesn't list as faults) or another fault prevents arming.

## [0.1.5] - 2026-09-27

### Changed

- The panel device shows "Secvest" as its model; the API doesn't report the exact model, serial number or firmware.

## [0.1.4] - 2026-09-27

### Changed

- The repair issue for a selected partition now also covers a partition without zones, the case that can happen on the panel (it always reports its four partitions); an empty partition's zone list is no longer requested.

## [0.1.3] - 2026-09-27

### Added

- Own icon for the integration: a shield with a keypad, shipped in `brand/` so Home Assistant shows it without the brands repository (#96).

## [0.1.2] - 2026-09-27

### Changed

- Zone devices are named with their kind, from the documented zone numbering: "Wireless zone …", "Wired zone …", "IP zone …" (German: Funkzone, Drahtzone, IP-Zone), and the kind is shown as the device's model. Entity ids stay without it.

### Added

- README: step-by-step manual installation from a clone via SSH or a mounted configuration folder.

## [0.1.1] - 2026-09-27

### Changed

- Zone entity ids start with the installation's name like the other entities, e.g. `binary_sensor.alarmanlage_front_door` instead of `binary_sensor.front_door`. Only affects zones added from now on.

## [0.1.0] - 2026-09-27

First version, read-only: the state of partitions, zones and faults.

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
- Arming blocked sensor per selected partition, naming the blocking zones and faults (#30).
- Options: partitions, status interval, User-Agent, excluded zones and a device class per zone; a repair issue for a selected partition the panel no longer reports; devices of zones no longer selected are removed (#40).
- README for v0.1: supported versions, safety notes, installation, connection, how it works and limitations (#49).
- Tests: English and German texts are complete and use the glossary terms (#42); credentials never appear in logs or entity states, also across a 401 and the reauthentication (#50).

[Unreleased]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.10...v0.2.0
[0.1.10]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.9...v0.1.10
[0.1.9]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.8...v0.1.9
[0.1.8]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/thomasdanz/ha-secvest/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/thomasdanz/ha-secvest/tree/v0.1.0
