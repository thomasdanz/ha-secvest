# Architecture

This document describes the target structure of the ABUS Secvest integration for Home Assistant. Decisions with alternatives are recorded as ADRs in [`adr/`](adr/).

The panel's API is documented separately in the `secvest-api` repository ("the specification"). This document only describes how the integration uses it.

Terms follow the manufacturer's wording, see the [glossary](glossary.md).

**Tested panel:** Secvest Touch FUAA50500 with firmware v3.01.31. Other firmware versions are untested ([ADR 0005](adr/0005-supported-panel-firmware.md)).

## Guiding principles

1. **The panel is fragile.** It has a slow TLS handshake, copes badly with parallel requests, and can hang under load. Every design decision puts the panel's stability first: one connection, strictly sequential requests, never more load than the official app, and back off on trouble.
2. **Verify, don't assume.** The panel's behaviour depends on its configuration. The same command can succeed, be silently ignored (200 with the old state) or be rejected (409, 403). The integration therefore never infers the outcome from a status code alone: after every command — also after an error response — it first reads the real state, and only then reports success or an error.
3. **Fail safe on authentication.** Failed logins at the panel's web interface and at the keypad are known to raise a code tamper alarm. At the REST API, three failed authentications in a row raised no alarm on the reference panel; whether more count is unknown and deliberately not tested. As a precaution, a failed authentication stops all requests until the user provides new credentials — the integration never causes several failed logins in a row.
4. **Logic never relies on texts.** Texts from the panel (`desc`, `ui-string`, zone and partition names) come from its language pack and user settings; they are only displayed. Logic uses structured fields such as `type`, `id` and states. The single, documented exception is the optional entry delay detection, whose text pattern is configurable.
5. **No surprises for users.** Everything is configured in the UI. Every failure explains itself in the UI, in English or German.

## Layers

```text
┌──────────────────────────────────────────────────────────────┐
│ Home Assistant                                                │
│                                                               │
│  config_flow.py      alarm_control_panel.py  binary_sensor.py │
│  diagnostics.py      switch.py  button.py  sensor.py  event.py│
│  repairs.py                   │                               │
│        │                      ▼                               │
│        │               entity.py (base classes)               │
│        │                      │                               │
│        └──────────►  coordinator.py  ◄── __init__.py (setup)  │
│                              │                                │
└──────────────────────────────┼────────────────────────────────┘
                               │  only way to reach the panel
┌──────────────────────────────▼────────────────────────────────┐
│ API client (no Home Assistant imports)                        │
│                                                               │
│  client.py   high-level operations (get_partitions, arm, …)   │
│  transport.py  single connection, request queue, timeouts     │
│  models.py   dataclasses and enums parsed from JSON           │
│  errors.py   typed exceptions                                 │
│  parsing.py  lenient JSON, spelling variants, timestamps      │
└──────────────────────────────┬────────────────────────────────┘
                               │ HTTPS, Basic Auth
                               ▼
                         Secvest panel
```

### API client

The API client is a self-contained Python package without any Home Assistant dependency (see [ADR 0002](adr/0002-api-client-package.md)). It can be used and tested on its own.

| Module | Responsibility |
|---|---|
| `transport.py` | Owns the single HTTPS connection and the TLS session cache (ADR 0001). Serialises every request through one queue. Applies timeouts, maps responses to typed errors, measures timings and counts full handshakes and resumptions. Blocks all requests after an authentication failure. |
| `client.py` | One method per API operation the integration uses (push registration is deliberately not included): `get_system`, `get_partitions`, `get_zones`, `get_alarms`, `get_faults`, `get_log`, `get_log_since`, `set_partition_state`, `set_zone_omitted`, plus `hold()` for command sequences. The only place that knows paths, bodies and the log filter; refuses states the app never sends (alarm states) and invalid ids without a request. Returns models, raises errors. No retries, no verification — that is the coordinator's job. |
| `models.py` | Frozen dataclasses (`System`, `Partition`, `Zone`, `PanelEvent` for faults and alarms, `LogEntry`, `LogEvent`) and enums. Partitions are identified by their one-based number everywhere, although the log counts them from zero. Outputs and cameras aren't modelled, since the integration doesn't read them. Faults and alarms share one event model, because the panel emits both in the same format; all fields except the identifying ones are optional. Unknown enum values are kept as raw strings instead of failing. |
| `parsing.py` | Lenient JSON parsing (control characters, ids as strings), both spellings of alarm states, conversion of log timestamps from panel local time. |
| `errors.py` | `SecvestError` and subclasses: `AuthenticationError` (401), `InstallerLockedError` (403 installer), `NotAllowedError` (403 empty), `NotFoundError` (404), `InvalidRequestError` (400), `ArmingBlockedError` (409, carries the faults), `CommunicationError` (timeouts, connection errors, unexpected responses) with its subclass `ConnectionLostError` (the connection broke after a command was sent, so its outcome is unknown). |

**Later (web interface epic):** reading data from the panel's web interface (e.g. signal strength, components such as repeaters) is a different interface with its own login, session and safety rules. It will live in a separate subpackage `webui/` next to the REST client, so the REST client stays unaffected and the feature can be disabled or left out entirely.

### Home Assistant integration

| Module | Responsibility |
|---|---|
| `__init__.py` | Sets up the client and coordinator per config entry, forwards platforms, unloads cleanly. |
| `coordinator.py` | The only user of the client. Schedules polling rounds and log polling, executes commands and verifies them, applies backoff and pause, tracks the installer lock and the authentication state. Holds the latest `PanelState`. |
| `entity.py` | Base entity classes: device info for the panel and per zone, availability rules, common attributes. |
| `alarm_control_panel.py` | One panel per selected partition. Maps the partition state to Home Assistant states; an alarm is detected from the partition state itself (`*-alarm`, `acknowledged`), `/alarms/` only adds details. Arm/disarm call `commands.py`. |
| `commands.py` | The command sequences (arm, disarm; later switching modes, acknowledging, omitting): hold the queue, send through `SecvestCoordinator.async_command`, judge by the verified state, and turn failures into one translated error plus the `secvest_arming_failed` event. |
| `binary_sensor.py` | Zone open/closed, zone problem, arming blocked per partition, installer lock. |
| `switch.py` | Omit switch per omittable zone. |
| `button.py` | Acknowledge alarm per partition. |
| `sensor.py` | Faults count with details. |
| `event.py` | Log entries as events. |
| `config_flow.py` | Setup, partition/zone selection, options, zone groups (subentry flow), reauthentication. |
| `groups.py` | Zone groups as stored in their subentries; what a change of which reloads the entry. |
| `repairs.py` | Repair issues for maintenance faults and for selected partitions that are empty or missing. |
| `diagnostics.py` | Redacted diagnostics download. |
| `log_patterns.py` | Text patterns for the optional entry delay detection, one per panel language, plus the user's custom pattern. The only place where logic depends on panel texts (see principle 4). |
| `translations/` | `en.json`, `de.json`. |
| `brand/` | `icon.png` and `icon@2x.png`, rendered from `assets/icon.svg` by `scripts/render_icon.py`; Home Assistant (2026.8 and later) serves them itself and falls back to the icon for the logo and dark mode. Own design, not ABUS artwork. |

## Request handling

All communication with one panel goes through one queue in `transport.py`:

- **One connection, resumed TLS sessions.** One HTTPS connection at a time. The panel closes idle connections after 10–30 s, so most polling rounds need a new connection; each reconnect resumes the previous TLS session (about 13 ms instead of a 6.5 s handshake). This needs a blocking client run in the executor (see [ADR 0001](adr/0001-http-client.md)).
- **Fresh connection when idle.** A connection idle for more than a few seconds, or already closed by the panel, is replaced before the next request, so a command is rarely sent into a connection the panel is just closing. Reads within a polling round share one connection.
- **Strict order.** A request starts only after the previous one has finished. User commands are placed ahead of pending polling requests but never interrupt a running request.
- **Timeouts.** A generous connect timeout covers the slow TLS handshake; shorter read timeouts afterwards; a longer one for the log.
- **Headers.** Every request carries Basic Auth (preemptively, the panel sends no challenge), `Accept` and `Content-Type: application/json` and a User-Agent: `ha-secvest/<version>` unless the user overrides it (e.g. for a reverse proxy that filters by User-Agent). No `Connection: close`.
- **Authentication gate.** After a 401 the transport rejects every further request, also those already queued, without sending them. New credentials mean a new transport: the entry is reloaded after reauthentication.
- **Direct access and reverse proxy.** The integration must work both directly against the panel (the design case) and through a TLS-terminating reverse proxy in front of it. A proxy holds its own TLS session to the panel, so the slow handshake doesn't occur on that path; session resumption towards the proxy is harmless. The load rules apply unchanged, since every request still reaches the panel.

### Don'ts

Other Secvest integrations were reviewed for this design. These patterns load the panel unnecessarily or act on guesses, and are not allowed here:

- **No `Connection: close`** and no new connection per request — every new connection without session resumption costs a 6.5 s handshake.
- **No path variants.** One request per operation with the exact path; no trying `/x/` and then `/x` on failure.
- **No automatic retries of commands** (arm, disarm, omit, acknowledge). The outcome is determined by the verification refresh. Single exception: if the connection was lost after sending and the verification shows the target was not reached, the command is sent once more.
- **No retries after a failed login**, neither REST nor web interface.
- **No full log download per polling round.** The full log is fetched once, to set the baseline; after that only incrementally and rarely.
- **No guessed requests.** Only calls documented in the specification (observed on a panel or defined by the official app) are sent.

## Data flow

### Polling

```text
every status interval (default 30 s, minimum 24 s):
    partitions → alarms → faults → zones of each selected partition
every log interval (default 5 min):
    log entries from one hour before the newest known one
```

**Log polling without gaps:**

- **Baseline:** on the first start, and whenever the stored log state is missing, the full log (up to 600 entries) is fetched once. Its newest entry is the baseline; these entries fire no events. This doesn't depend on the panel clock matching Home Assistant's.
- **Increments:** `$filter=timestamp ge <newest known timestamp − 1 h>`. The overlap returns already known entries again on purpose: it covers entries written later within the same second, and the hour the panel's local clock repeats when daylight saving time ends.
- **De-duplication by content:** an entry counts as known if `id`, timestamp, text and event fields all match. The `id` alone isn't enough, since it seems to be derived from the timestamp and could repeat when the clock goes back.
- **Persistence:** the newest timestamp and the entries of the overlap window are stored, so a Home Assistant restart neither replays nor skips entries.
- **Limits (documented):** entries written after a panel restart before its clock is set (dated 2019-01-01), and more than 600 new entries between two log polls, can be missed.

The partition state alone tells whether a partition is in alarm, so an alarm is detected even if `/alarms/` fails. `/alarms/` is still part of the round for the alarm type and other details.

The coordinator merges the results into one immutable `PanelState` and notifies the entities. A round never overlaps with another round; if a round takes longer than the interval, the next one starts late instead of piling up.

- **Setup** runs the first round; the entry is loaded only once it succeeded. A 401 there stops the entry without a retry (reauthentication, see "Error handling"); other failures let Home Assistant retry the setup later.
- **Quick reloads:** a reload (changed options or zone groups) within the minimum spacing takes the last round's result instead of waiting for a new round, as long as the selected partitions are the same; nothing is sent, and the next round keeps the spacing.
- **Minimum spacing:** a round never starts sooner than 24 s after the previous one started, whatever triggers it (interval, a manual refresh, setup retry, reload after an options change). The time of the last round is kept outside the coordinator, so a new coordinator after a reload or setup retry keeps the spacing; a round that comes too early waits.
- **Manual refresh:** there is no refresh button; Home Assistant's `homeassistant.update_entity` action on any of the integration's entities runs a round, within the same minimum spacing.
- **Commands go first:** a round doesn't hold the request queue, so a command can go ahead between two of its reads.
- **Zones:** only the zone lists of the selected partitions that have zones (according to `/system/partitions/`) are read; a zone in several selected partitions is kept once. A selected partition without zones raises a repair issue (it shows only its state and can't be armed), which leads to the options to deselect it and can be ignored if the partition is empty on purpose; it disappears once the partition has zones again or is deselected. A selected partition the panel doesn't report at all (not expected: the tested panel always reports its four partitions) is skipped and raises the same kind of issue. The issue is logged once per change.

### Commands and verification

```text
entity action (e.g. arm away)
  → coordinator.arm(partition, target)
      0. the whole sequence holds the request queue — no polling round in between,
         the entity keeps showing its previous state until the sequence ends
      1. if switching between armed modes: disarm first (verified);
         if disarming during an alarm (*-alarm): acknowledge first (verified)
      2. PUT partition state; keep the response (200, 409 with faults, 403, …)
      3. verification refresh: partition (+ faults, zones) — always, also after
         an error response
      4. target state reached?  yes → done (even if step 2 answered an error)
                                no  → arming_failed; the reason comes from the
                                      error response and the fresh state
                                      (see "Failed arming")
```

The verification refresh replaces the next regular polling round, so a command doesn't add a burst of extra requests: the schedule starts again from it, and the minimum spacing counts from it. A regular round that read part of the state before the command and finishes after it discards its result, so it can't overwrite the verified state with an older one. The verification is a full round (partitions, alarms, faults, zones), which covers every kind of command with one mechanism.

**Error responses are verified too.** An error response is never reported directly: the fresh state decides. If the target state was reached anyway (e.g. someone armed at the keypad at the same moment), the command counts as successful. The same applies to omitting zones: after a 403, the zone is read again, and its `omitted` and `omittable` decide the outcome and the message. Exceptions are the responses after which no further request is sent: a 401 (authentication gate) and the installer lock (every request fails); there the error is reported with the last known state.

**Disarming during an alarm:** the official app only allows `unset` from `set`/`partset` or from `acknowledged`. Disarming a partition in an alarm state (`set-alarm`, `partset-alarm`, `unset-alarm`) therefore first acknowledges the alarm and then disarms, each step verified, like switching between armed modes. A direct `unset` from an alarm state is never sent (see "No guessed requests").

**Connection lost after sending a command** (`ConnectionLostError`): the command may or may not have reached the panel. The integration first runs the verification refresh; only if the target state was not reached, it sends the command **once** more (verified again). This is the single exception to "no automatic retries of commands" and is safe because a state change the panel already applied is not applied twice.

### Failed arming

The panel reports a refused arming in several ways: `409` with the blocking faults, `200` with the unchanged state ("silently ignored", depending on its configuration), or an empty `403` when the user may not operate the partition. All are **the same failure for the user** and are reported the same way:

- The action fails with one error type (`arming_failed`), shown in the UI like any failed action and visible in automation traces.
- An `arming_failed` event is fired with partition, requested state and reason, so automations can react (e.g. send a notification).
- The message always has the same structure: *"Partition X was not armed: <reason>"*, translated.

Only the reason differs:

| Panel answer | Reason in the message |
|---|---|
| `409` with faults | The blocking zones and faults from the response (certain). An empty list (partition without zones): "the panel refused without naming a reason". |
| `403`, empty body | The user has no permission for this partition (certain). |
| `200`, state unchanged | Derived in the background (likely): the zones of the partition that are open and not omitted; if there are none, the current faults with `prevents-set`; otherwise "the panel did not arm; reason unknown". The message marks this as the likely reason. |

## Error handling

| Error | Integration behaviour |
|---|---|
| `AuthenticationError` | Stop all requests, start the reauthentication flow (Home Assistant shows it prominently, so no separate repair issue); entities become unavailable. Never retried (precaution, see principle 3). The 401 is remembered in the config entry (`auth_failed`), so a restart of Home Assistant doesn't send the rejected credentials again: while it is set, setup starts the reauthentication without any request. The reauthentication asks for user code and password and checks them with exactly one request (`GET /system/`); only its success clears the flag and reloads the entry with a new transport. |
| `InstallerLockedError` | Keep last known states, mark the panel as locked (attribute and binary sensor), commands fail with a clear message. The polling round stops at the first request that reports the lock; the interval stays unchanged. While the lock lasts, each round therefore costs a single request, and the first round after the lock is lifted refreshes everything. The lock doesn't count towards the backoff and keeps the entities available. During setup it lets Home Assistant retry the setup later. |
| `ArmingBlockedError` | Command fails; the message lists the blocking faults and zones. |
| `NotAllowedError` | Command fails. The panel gives the same empty 403 for a zone that isn't omittable and for a partition the user may not operate (omitting zones and changing the partition state); for zones the message tells the two apart by the zone's `omittable`. |
| `InvalidRequestError`, `NotFoundError` | Indicate a bug or a panel that differs from the specification. The command fails, the response is logged; during polling they are handled like a `CommunicationError`. |
| `CommunicationError` | Backoff: after each failed round the delay before the next one doubles, starting from the status interval, up to 5 minutes; after 5 failed rounds in a row polling pauses for 15 minutes (one round per pause). The last state is kept; entities become unavailable only once the pause starts, so they don't flap. A manual refresh doesn't shorten the delay. A successful round resets everything. The backoff state is part of the diagnostics. A failed first round at setup uses Home Assistant's setup retry instead (its own growing delay), with the same minimum spacing. |
| Unknown values in responses | Kept raw, logged once, shown as attributes; never crash. |

## Entity model

The panel's partitions are independent of each other, so everything that belongs to a partition exists once per selected partition. Zones are detectors; a zone can belong to more than one partition, so zones are modelled on their own and reference their partitions.

**Panel device** (one per config entry; manufacturer ABUS, model "Secvest": the API reports no model, serial number or firmware, but only a Secvest speaks it)

| Entity | Platform | Content |
|---|---|---|
| Faults | sensor | Number of current faults **except "zone open"** (the panel lists every open omittable zone as a fault, even when disarmed; those are counted per partition as open zones); list and readable summary of the same faults as attributes |
| Problem | binary_sensor | On while the faults sensor is above 0, i.e. any fault other than an open zone is present |
| Installer lock | binary_sensor (diagnostic) | On while the installer is logged in at the panel |
| Log | event | New log entries |
| Diagnostics | sensor (diagnostic) | Last round duration, connection setup time, reconnects, backoff state |

**Per selected partition** (entities on the panel device, named after the partition)

| Entity | Platform | Content |
|---|---|---|
| Alarm panel | alarm_control_panel | disarmed / armed_home / armed_away / triggered (and optionally pending), unknown for an unknown state; named after the partition; attributes: panel state (raw, the app's underscore spelling normalised), acknowledged, alarm type (#19). Unavailable while the panel doesn't report the partition. |
| Open zones | sensor | Number of the partition's zones that are open and not omitted; ids and names as attributes |
| Arming blocked | binary_sensor | "Blocked" / "Possible" (no device class: open windows are a state, not a problem). On while open zones is above 0, or a fault other than an open zone with `prevents-set` affects the partition; attributes: the open zones and those faults. Open zones count whether or not the panel lists them as faults: an open entry door is no fault on the reference panel, but arming via the API fails then (configuration-dependent) |
| Acknowledge alarm | button | Available only while the partition is in alarm |

**Per selected zone** (one device per zone, named after the zone with its kind — "Funkzone Keller", "Wireless zone …" in English — with the kind as its model (in Home Assistant's language, German or else English, since the model can't be translated like the name), and linked to the panel device via its device id, so each detector can be assigned to an area; the panel device is registered at setup before the platforms)

| Entity | Platform | Content |
|---|---|---|
| Zone | binary_sensor | Open / closed, unknown for any other zone state; the device's main entity (named after the zone); device class from the options (`zone_device_classes`: door, window, garage door, motion, smoke, …), none by default since the API has no detector type; attributes: zone id, raw zone state, all partitions the zone belongs to, `omittable`, `omitted`, `inner` |
| Zone problem | binary_sensor (problem, diagnostic) | On for any zone state other than open/closed (tamper, fault, …) or a fault affecting the zone; "zone open" faults (type 5000) are ignored, since they appear for every open omittable zone and the zone sensor already shows them |
| Omit zone | switch | Only for omittable zones; turns off by itself when the panel includes the zone again at disarm |

**Zone groups** (optional, configured manually as config subentries, #67): several zones that belong to one opening, e.g. the two wings of a window. A zone group is a Home Assistant concept, not one of the panel, so it stays separate: the group gets its own device "Zone group <name>" / "Zonengruppe <name>" (model "Zone group" in Home Assistant's language, no manufacturer, linked to the panel device), and the member zones keep their devices, entities and names.

| Entity | Platform | Content |
|---|---|---|
| Group | binary_sensor | The group device's main entity, suggested as `binary_sensor.<installation>_<group>`; on while at least one member zone is open, off while all are closed, unknown otherwise (e.g. a member in tamper); device class from the group (window, door, …); attributes: member zone ids, open member zones. Belongs to the group's subentry |

A group has a name (unique), at least two zones of the selected partitions (none preselected) and a device class, by default "same as the zones": the class all member zones show (the user's "Show as" or the options), followed when it changes; if the zones differ, the user has to choose one; a zone belongs to at most one group. An area can be chosen: a new group device gets it; reconfiguring shows the device's current area and sets it, otherwise the device page manages it. "Hide grouped zones" marks the member zones' entities as hidden by the integration (they keep working); unticking it or removing a zone shows them again, while entities the user hid stay hidden. Adding, changing or deleting a group reloads the entry; deleting it removes its device and sensor. A member zone that the selected partitions no longer list (deleted at the panel, or its partition deselected) is ignored by the group sensor and raises a repair issue for the group, which points to reconfiguring it; the group is never changed automatically. A listed member that just wasn't read makes the sensor unknown instead, and without any listed member the sensor is unavailable. Groups are never formed from zone names (principle 4). The group flow sends nothing to the panel.

**Entity ids** (suggested at registration; users can rename them): `<domain>.<installation>_<partition>[_<entity>]` for partition entities, `<domain>.<installation>_<entity>` for the panel's own and `<domain>.<installation>_<zone>[_<entity>]` for zone entities, with the installation being the entry's title (the panel's system name) and all parts slugified. Home Assistant creates entity names, and so the `<entity>` part, in its language at the time (e.g. `sensor.alarmanlage_storungen` in German). The kind of zone in the device name (wireless, wired or IP zone, from the documented zone numbering 201–248, 301–304 and 101–106; other numbers keep the plain name) is not part of the entity id. Partition and panel entities get this from Home Assistant (panel device name plus entity name); zone entities, whose device is named after the zone, and group sensors suggest it explicitly (`<domain>.<installation>_<group>`).

**Unique ids:** `<config entry id>_partition_<partition id>_<entity>`, `<config entry id>_zone_<zone id>_<entity>` and `<config entry id>_group_<group id>_<entity>`. The group id is the subentry id. The API reports no serial number, so ids are tied to the config entry: removing and re-adding the integration creates new entities (entity ids can be renamed back in Home Assistant).

## Configuration

| Stored in | Content |
|---|---|
| Config entry data | Address, user code, password, certificate verification, User-Agent override (advanced; empty = `ha-secvest/<version>`), `auth_failed` after a 401 |
| Config entry options | Selected partitions (`partitions`), status interval (`scan_interval`), excluded zones (`excluded_zones`), device class per zone (`zone_device_classes`); later zone groups, the log interval and optional features |

**Address:** stored normalised as `https://host:port[/path]`. Without a scheme the panel's own port 4433 applies unless one is given; an https URL without a port means 443 (e.g. a reverse proxy). The normalised address (host, port and path) is the entry's unique id, since the API reports no serial number. Setup validates the credentials with exactly one request (`GET /system/`) and takes the entry's title from the installation name. Only once the credentials are accepted, it reads the partitions (`GET /system/partitions/`) and the zone lists of the partitions that have zones, on the same connection, for the selection and the zones step; nothing else is sent during setup.

The user selects **partitions**, not zones. The zones are derived from the selected partitions (union; a zone in several partitions is created once), so new detectors in a selected partition appear automatically after a reload. Partitions without zones are deselected by default; at least one has to be selected. The selection is stored in the options as partition numbers (`partitions`). Individual zones can be excluded in the advanced options.

**Panel user:** a separate panel user of level "normal user" is enough — with rights for a partition, it reads, omits zones, arms and disarms like an administrator. The panel's partition rights are not visible in reads (every user sees all partitions), so the flow can't hide partitions the user may not operate; a command there fails with an empty 403 and is reported as "no permission". The documentation recommends giving the Home Assistant user rights for exactly the partitions it should operate.

Changing options or zone groups reloads the entry: one update listener compares the options, the subentries and the User-Agent with the state at setup (Home Assistant doesn't allow its reloading options flow together with an update listener). The options flow sends nothing to the panel: it offers what the last polling round returned (zones of a newly selected partition are listed by id until the next round). It has two steps: partitions, status interval and (advanced) the User-Agent override, which is stored in the entry data; then the zones: excluded zones and a device class per zone (none by default). Setup ends with the same zones step (shared code), so zone types can be set right away; Home Assistant's own "Show as" still overrides the device class per entity. Device classes of zones that aren't shown, e.g. of a partition deselected for now, are kept.

Devices follow the configuration: at setup, zone devices that are no longer selected or are excluded are removed. This is decided from the partitions' zone lists, not from the zones read, so a zone the panel briefly doesn't report keeps its device and settings. Later, zones that disappear or are excluded also drop out of their group, and empty groups are removed (#67).

## Updates

Users install new versions over their existing config entry; an update never requires setting up again.

- **Stored data is versioned.** `VERSION` changes break compatibility, `MINOR_VERSION` changes don't. `async_migrate_entry` brings older entries up step by step (1.1 → 1.2 adds the zone settings' defaults, which setup stores since 0.1.7). Home Assistant refuses an entry of a newer major version itself, so a downgrade never misreads data; a newer minor version loads as it is.
- **Unique ids are stable** (see "Unique ids"); the entity registry test catches accidental changes.
- **Removed kinds of entities** are removed from the entity registry at setup, instead of staying as "no longer provided". The kinds are matched by unique id; entities of zones or partitions that are only temporarily missing are kept.
- **Devices** get their name and model at every start, so such changes need no migration.
- **Test set:** `tests/upgrade/stored_entries.json` holds the entry as each released version stored it, with the unique ids it registered. The update test loads each with the current code: it has to load, keep every entity and send nothing beyond a normal round.

## Testing

- **API client:** unit tests against the fixtures of the specification (see [ADR 0004](adr/0004-fixtures.md)), including every error response.
- **Integration:** tests with `pytest-homeassistant-custom-component` against a **fake panel**: a small HTTPS server that answers with the fixtures and simulates the observed behaviour (state changes and their rules, 409, ignored commands, omitted zones, alarms, installer lock, error responses, timeouts). It also closes idle connections and issues TLS session tickets, so session resumption is tested, and it fails a test if requests arrive in parallel. Requests whose answer the specification doesn't know (undocumented paths or filters, alarm states in a command, a direct `unset` during an alarm, repeating credentials after a 401, `Connection: close`) fail the test as well, instead of the fake guessing an answer. It lives in `tests/fake_panel.py`.
- **CI:** linting (ruff), type checks (mypy), tests, hassfest and HACS validation.
- **Real panel:** the maintainer's manual tests against the reference panel go through a reverse proxy. They don't exercise the panel's TLS handshake or session resumption; those are covered by the measurements in ADR 0001 and by the fake panel.

## Open points

- None at the moment.
