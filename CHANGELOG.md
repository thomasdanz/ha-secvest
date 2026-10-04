# Changelog

All notable changes to this project are documented in this file. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- Names follow renames at the panel: a renamed partition or zone shows its new name after the next polling round (alarm panel, partition sensors, zone devices and entities), without another request. **Configure** → **Take over names from the panel** reads the installation's name once and names the panel device after it. Entity ids, the entry's title and names set in Home Assistant stay (#137).

### Changed

- The minimum Home Assistant version is 2026.8.0 (declared 2026.3.0 before, but the zone devices need 2026.8; checked against every release from 2026.3 on). CI tests the minimum as a third version (#150).
- Without any code configured, the alarm panel asks for none: arming and disarming work without a code (a code sent anyway, e.g. by HomeKit Bridge, is ignored), and no user is shown. With the first code added, codes are required as before. The reason `no_codes` of `secvest_arming_failed` is gone (#131).
- README: arming without any code while codes are configured fires no `secvest_arming_failed` event, since Home Assistant refuses it before the integration is called (#143).
- Codes for arming and disarming are managed in the integration's options (**Configure** → **Codes**: add, change, remove) instead of as entries on the integration's page, which showed an empty section per code. Changing codes no longer reloads the integration (#141).

### Upgrade notes

- Installations without codes can arm and disarm without one after the update. Add a code under **Configure** → **Codes** if arming and disarming should stay restricted.
- Existing codes move into the options by themselves at the first start (stored data 1.3) and keep working; they are now under **Configure** → **Codes**. Going back to 0.3.x afterwards finds no codes there, so arming and disarming fail until they are added again.

## [0.3.1] - 2026-10-03

### Fixed

- The logbook shows the text of each new log entry with the panel's time. In 0.3.0 these rows were missing and the log had "Error with secvest describe event" for each entry; entries recorded since then get their rows too (#167).

## [0.3.0] - 2026-10-03

### Added

- The panel's log is read incrementally: once in full as the baseline, then every 5 minutes only the entries since an hour before the newest known one, recognised by their content and stored across restarts. The log interval is a new option (at least 2 minutes). New entries become visible with the log event entity (#11).
- Times of log entries are converted from the panel's local time in Home Assistant's time zone, which has to match the panel's (#8).
- A **Log** event entity on the panel device fires once for each new log entry, with its type (normal, alarm, trouble), text, time, user, partition and zone. Each entry also appears in Home Assistant's logbook with its text and the panel's time (#34).
- The alarm panel's attribute `omitted_zones` lists the partition's omitted zones, however they were omitted, so it shows which zones aren't guarded (#119).
- An **Omit** switch per zone group (on the group's device) omits or includes all omittable zones of the group with one action, one zone after the other, each verified; zones that can't be omitted are listed, and a failure names the zones not changed (#136).

### Fixed

- Checking a code for arming or disarming no longer blocks Home Assistant (about 70 ms per stored code): the hashing runs in the executor, also in the code flow. Codes store their hash parameters; codes stored before keep working (#139).
- `changed_by` of the alarm panel is cleared when the partition's state changes without a command from this entity (keypad, app, alarm), instead of naming the last Home Assistant user (#144).

### Upgrade notes

- The new entities (**Log** on the panel device, **Omit** on each zone group's device) appear by themselves; nothing has to be set up again.
- The panel's log is now read every 5 minutes, one filtered request that takes the panel about 6 seconds; the log interval can be changed in the options. The first read after the update fetches the full log once as the baseline and fires nothing.
- Log times are read in Home Assistant's time zone: set it to the panel's.

## [0.2.9] - 2026-10-03

### Changed

- Arming into the mode a partition is already in, or disarming a disarmed partition, sends nothing to the panel: the state read right before is shown, the command succeeds, and `changed_by` doesn't change. Like the official app, the integration no longer sends a state the partition already has (#142).

## [0.2.8] - 2026-10-03

### Added

- The problem sensor of a zone lists the faults affecting the zone in its attribute `faults`, so the problem is readable without the panel's faults sensor (#146).

### Changed

- README: limitations name omitting through the first selected partition that lists the zone; the safety notes advise setting up each panel only once (the API has no serial number to detect a second address) and describe the panel's load limits with measured facts. The planned "pending" state from the log was dropped (#36). Architecture documentation corrected (#146).
- Test fixtures follow the specification, whose example zone names were redacted (#146).

## [0.2.7] - 2026-10-03

### Fixed

- Changing options or zone groups right after a polling round that found the installer logged in, or that failed, no longer shows the state from before it: the installer lock stays shown, and polling keeps its backoff instead of starting afresh (#145).
- A command whose connection broke twice, first before it went out and then after, is now treated like any lost connection: verified, and sent once more if the panel didn't take it, instead of failing with "the panel answered with an error" (#145).
- A repair issue for an empty or missing partition is only raised or cleared by a complete polling round, not by one that fails afterwards (#145).
- A request after the connection was closed (e.g. a late refresh while the entry unloads) fails with a connection error instead of an unexpected error in the log (#145).

## [0.2.6] - 2026-09-29

### Changed

- The arming blocked sensor is named "<partition> arming" ("<Teilbereich> Aktivierung") and shows "Blocked" / "Free" ("Blockiert" / "Frei") instead of "Blocked" / "Possible" (#134).

### Fixed

- Reauthenticating an entry that was running when the 401 came reloaded it in a way Home Assistant warns about and will refuse from 2026.12 ("has an update listener and should use it for scheduling a reload"); its update listener reloads it now (#134).

### Upgrade notes

- Existing installations keep the entity id of the arming sensor (e.g. `binary_sensor.<installation>_<partition>_arming_blocked`); only its name and state texts change. Rename the entity id in Home Assistant if you like.

## [0.2.5] - 2026-09-29

### Fixed

- Entities didn't show as unavailable when the panel stayed unreachable: Home Assistant only updates them at the first failed round of a series, so the unavailability from the third failed round on (0.2.4) — and before that, during the 15-minute pause — was never shown (#132).

### Changed

- README: the safety notes explain prominently that a single rejected login (401) stops all requests until you reauthenticate, also after a restart and also for a 401 from a reverse proxy.

## [0.2.4] - 2026-09-29

### Changed

- While the panel can't be reached, entities are unavailable from the third failed round in a row (about 3 minutes) instead of showing the last state until polling pauses (about 12 minutes); backoff and pause are unchanged (#128).
- A command whose read before sending fails with a connection problem reports the new reason `unreachable` ("nothing was sent") instead of `not_verified` (#128).

### Upgrade notes

- Automations on `secvest_arming_failed` that translate reasons may add `unreachable`.

## [0.2.3] - 2026-09-28

### Changed

- The event `secvest_arming_failed` is fired for every failed arming or disarming, not only when the panel's state explains it: new reasons `not_verified` (the result couldn't be read back; check the panel), `installer_locked`, `arm_during_alarm`, `auth_failed`, `invalid_code` and `no_codes`. A notification from it now also covers these cases, e.g. for the Apple Home app via HomeKit Bridge, which shows no message (#126).

### Upgrade notes

- Automations on `secvest_arming_failed` receive the new reasons; map them if they translate reasons into texts.

## [0.2.2] - 2026-09-28

### Changed

- While the installer is logged in, arming, disarming and omitting fail at once with the installer message, without sending anything to the panel; the entities keep showing their last state. Until a polling round has seen the lock, the panel's answer gives the same message (#123).
- Disarming from Home Assistant turns the omit switches off at once, since the panel includes omitted zones again; now covered by a test.

## [0.2.1] - 2026-09-28

### Added

- The event `secvest_arming_failed` carries the calling action's context, so automations can tell a command from Home Assistant's UI from one from elsewhere (e.g. HomeKit Bridge), and has two more keys: `zone_names` and `user`, the name of the code that was entered (#120).
- README: a notification only for failed commands from outside Home Assistant's UI, and how to use a code with HomeKit Bridge.

## [0.2.0] - 2026-09-28

Control: arming and disarming with a code, acknowledging alarms on the way and omitting zones, each checked by reading the panel again.

### Added

- Codes for arming and disarming: a user name and a four-digit code each, added on the integration's page and stored only as a salted hash; the alarm panel shows who armed or disarmed. Omitting zones needs no code (#116).
- Arming (away and home) and disarming from the alarm panel, each checked by reading the partition again; failures raise one message with the reason and fire the event `secvest_arming_failed` (#16, #17).
- Switching between armed away and armed home disarms first and then arms again, each step checked; the alarm panel shows the previous mode until the switch is done (#18).
- Alarm details on the alarm panel: the alarm type (in the panel's own terms, e.g. "Burglar alarm" / "Einbruchalarm") and the zones that raised it; a failing alarm list no longer fails the whole polling round (#19).
- Disarming during an alarm acknowledges it first and then disarms, each step checked; arming during an alarm isn't sent. Not tested at a real panel, since that would need an alarm (#20).
- Before a command sequence the partition is read again, so what is sent first depends on its current state, not on the last polling round.
- Omit switch per omittable zone, checked by reading the zone again; the message tells a zone that can't be omitted from missing rights (#27).
- README: why the alarm panel shows no arming or pending state (#22).

### Upgrade notes

- Arming and disarming need a code now: add one on the integration's page ("Add code"), otherwise the alarm panel can't arm or disarm.
- New entities appear by themselves: an omit switch per omittable zone (hidden for grouped zones if the group hides its zones).

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

[Unreleased]: https://github.com/thomasdanz/ha-secvest/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/thomasdanz/ha-secvest/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.9...v0.3.0
[0.2.9]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.8...v0.2.9
[0.2.8]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.7...v0.2.8
[0.2.7]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.6...v0.2.7
[0.2.6]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.5...v0.2.6
[0.2.5]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.4...v0.2.5
[0.2.4]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/thomasdanz/ha-secvest/compare/v0.2.0...v0.2.1
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
