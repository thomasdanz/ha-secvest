# ABUS Secvest for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Release](https://img.shields.io/github/v/release/thomasdanz/ha-secvest)](https://github.com/thomasdanz/ha-secvest/releases)
[![CI](https://github.com/thomasdanz/ha-secvest/actions/workflows/ci.yml/badge.svg)](https://github.com/thomasdanz/ha-secvest/actions/workflows/ci.yml)

A Home Assistant custom integration for the ABUS Secvest alarm panel. It talks to the panel's local REST API, the one the official app uses, as documented in [`secvest-api`](https://github.com/thomasdanz/secvest-api).

> **Status:** v1.0 "Hardened": shows partitions, zones and faults, arms and disarms (acknowledging an alarm on the way, or omitting open zones once), omits zones and zone groups, shows the panel's log as events and in the logbook, verifies the panel's certificate, changes its connection without setting up again, and offers diagnostics. Data from the panel's web interface follows with v1.1 (see the [milestones](https://github.com/thomasdanz/ha-secvest/milestones)).

> **Disclaimer:** This is an unofficial community project, not affiliated with or endorsed by ABUS. The panel is security equipment: use this integration at your own risk.

## Supported versions

- **Panel:** tested with the Secvest Touch FUAA50500 running firmware v3.01.31. The API doesn't report the firmware version, so the integration can't check it; other models and firmware versions are untested. Unknown values from the panel are kept and logged once instead of breaking anything.
- **Reported to work with:** no other models or firmware versions yet. If it works (or doesn't) on yours, please open an issue with the panel model and firmware version.
- **Home Assistant:** tested with 2026.10 and 2026.9, the current and the previous release, and with 2026.8.0, the minimum. Older versions don't work: the zone devices need 2026.8.

## Safety notes

- The panel is security equipment in an inhabited building. Test automations that use it carefully.
- **Use a separate panel user** for Home Assistant: the level "normal user" is enough, with rights for exactly the partitions Home Assistant should operate. Every user sees all partitions, but the panel refuses commands on the others.
- **Polling limits:** the integration never polls more often than every 24 seconds, the official app's own cycle, and backs off when the panel doesn't answer. It keeps one connection, sends one request at a time and resumes TLS sessions, since a full handshake takes the panel about 6.5 seconds. More load than the official app's was deliberately not tested.
- **One rejected login stops everything:** failed logins may count towards a code tamper alarm, so the integration never causes a second one. After the first `401 Unauthorized` it sends nothing more with these credentials — no retry, no polling, also not after a restart of Home Assistant — its entities are unavailable, and Home Assistant asks you to reauthenticate (Settings → Devices & services). Only new credentials, checked with a single request, start it again. This holds for every 401, also one a reverse proxy in front of the panel answers.
- **The certificate is checked:** with "Verify certificate" on, nobody in your network can impersonate the panel to capture user code and password; they go out only to the trusted certificate. If the panel presents another one (a new certificate, a reverse proxy added or removed, or someone impersonating it), the integration handles it like a rejected login: it sends nothing more, also not after a restart, and Home Assistant asks you to confirm the new certificate, showing what changed and its fingerprint. Confirm only if you expect the change.
- **Set up each panel once.** The API reports no serial number, so the integration can't tell that two addresses (e.g. directly and through a reverse proxy) lead to the same panel; set up twice, it is polled twice.
- **Don't expose the panel's API to the internet unprotected.** The panel neither noticed nor limited failed logins at its REST API in tests (see [`secvest-api`](https://github.com/thomasdanz/secvest-api)).

## Installation

**HACS:** add this repository as a custom repository (HACS → ⋮ → Custom repositories, type "Integration"), install "ABUS Secvest" and restart Home Assistant.

**Manually:** copy the folder `custom_components/secvest` of a release into the `custom_components` folder of your Home Assistant configuration and restart Home Assistant. To update, replace the folder and restart again.

From a clone of this repository, with SSH access to Home Assistant (e.g. the "Advanced SSH & Web Terminal" add-on), this installs exactly the tagged version, without local changes or `__pycache__`:

```bash
git fetch --tags
ssh <user>@<home-assistant-host> 'ls /config'   # check host and folder first
ssh <user>@<home-assistant-host> 'rm -rf /config/custom_components/secvest'
git archive <tag> custom_components/secvest | ssh <user>@<home-assistant-host> 'tar -x -C /config'
ssh <user>@<home-assistant-host> 'grep version /config/custom_components/secvest/manifest.json'
```

Replace `<tag>` with a version such as `v0.1.1`. Removing the folder first makes sure files deleted in the new version don't stay behind. With the configuration folder mounted instead (e.g. the Samba add-on), `rsync -av --delete --exclude __pycache__ custom_components/secvest/ <mounted-config>/custom_components/secvest/` from a checkout of the tag does the same.

Replacing the files while Home Assistant runs is fine: the running code stays in memory until the restart. Restart right away, though, without setting up or reloading the integration in between, since Home Assistant loads some parts only when needed and could mix old and new files. If your configuration folder is a Git repository, add `custom_components/secvest/` to its `.gitignore`.

**Connection:** the integration works directly against the panel (its own HTTPS port 4433 with a self-signed certificate) and through a reverse proxy in front of it. The proxy may hold its own TLS session to the panel; the polling limits apply either way, since every request still reaches the panel.

## Setup

Add the integration in Home Assistant (Settings → Devices & services → Add integration → ABUS Secvest) and enter:

- **Address:** the panel's IP address or host name, optionally with a port (default 4433), or the https URL of a reverse proxy in front of it.
- **User code** and **password** of a panel user. Use a separate user for Home Assistant: the level "normal user" is enough, with rights for exactly the partitions Home Assistant should operate. The installer code doesn't work.
- **Verify certificate** (on by default): a certificate with a publicly trusted chain, e.g. of a reverse proxy with Let's Encrypt, is verified as usual, and renewals need nothing from you. A self-signed certificate, such as the panel's own, is shown next with its fingerprint; once you confirm it, only this certificate is accepted. Turn it off only if you know why: then nothing is verified.
- **Advanced → User-Agent:** only needed for a reverse proxy that filters by User-Agent.

The credentials are sent only after the certificate step, and checked with a single request. If the panel rejects them, nothing is retried automatically.

Then select the **partitions** Home Assistant should show and operate; their zones are added automatically. The panel doesn't reveal which partitions the user may operate, so all of them are listed; partitions without zones are deselected.

Finally choose what kind of detector each **zone** is (door, window, garage door, motion, …) and exclude zones you don't want in Home Assistant. The panel doesn't tell detector types apart, so this is up to you; you can change it later in the options.

**Changing the connection:** if the panel gets a new address, you put a new reverse proxy in front of it, or its password changes, use **Reconfigure** in the entry's menu (Settings → Devices & services → ABUS Secvest → ⋮) instead of removing and re-adding the integration. It changes address, user code, password (leave it empty to keep the current one) and certificate check, checks them with a single request, and keeps all entities, names and settings. Meanwhile polling waits, so there is never a second connection to the panel; if the check fails, nothing changes. For another panel, add a new entry instead: the panel can't be identified, so the integration doesn't notice.

## Options

In the integration's options (Settings → Devices & services → ABUS Secvest → Configure) you choose between the settings, the codes (see "Arming and disarming") and taking over names from the panel. Under the settings you can change the selected partitions, the status interval (at least 24 seconds), the log interval (how often new entries of the panel's log are read; 5 minutes by default, at least 2 minutes) and, under Advanced, the User-Agent. The second step lists the zones of the selected partitions: choose a device class per zone (door, window, motion, …; the panel doesn't tell detector types apart) and exclude zones you don't want in Home Assistant. Saving reloads the integration; nothing is sent to the panel while you change the settings or codes.

If a selected partition has no zones anymore (for example after the installer moved its detectors to another partition), a repair issue suggests deselecting it in the options; ignore it if the partition is meant to be empty. It goes away by itself once the partition has zones again.

## Zone groups

Zones that belong to one opening, such as the two wings of a window, can be combined into a zone group: on the integration's page choose **Add zone group**, give it a name, select at least two zones and choose how the group is shown (window, door, …, or "Same as the zones") and, optionally, its area. The group gets its own device "Zone group <name>" with a sensor that is on while any of its zones is open, e.g. `binary_sensor.alarmanlage_living_room`. The zones keep their own devices and entities. If at least one zone of the group can be omitted, the group device also gets an **Omit** switch: it omits all omittable zones of the group one after another (each checked) and includes them again; it is on while all of them are omitted and turns off by itself when the panel includes them at disarm. Zones that can't be omitted (e.g. entry doors) are never sent and are listed in its attribute `not_omittable_zones`. If omitting one zone fails, the switch stops there and names the zones that weren't changed; zones already omitted stay omitted. **Hide grouped zones** hides the zones' entities in Home Assistant; they keep working and can still be used in automations. Each group can be changed or deleted on the integration's page. If a zone of a group disappears (removed at the panel, or its partition deselected), the group sensor ignores it and a repair issue asks you to reconfigure the group. A zone group is a Home Assistant feature; the panel knows nothing about it.

## Entities

- **Alarm panel** per selected partition, named after the partition: disarmed, armed home (internally armed), armed away or triggered. An acknowledged alarm is still shown as triggered, with the attribute `acknowledged`; the attribute `panel_state` holds the panel's own state. While the panel reports an alarm, `alarm_type` names its kind in the panel's own terms (burglar alarm, fire alarm, hold-up alarm, …) and `alarm_zones` the zones that raised it; `omitted_zones` lists the partition's omitted zones, however they were omitted (switch, keypad, app or forced arming), so it shows which zones aren't guarded, also zones excluded in the options (they are omitted at the panel all the same); the alarm is detected from the partition's state itself, so it shows even if these details can't be read. You can arm (away or home, i.e. internally) and disarm it; see "Arming and disarming".
- **Zones:** each zone of the selected partitions is its own device below the panel device, named with its kind (e.g. "Wireless zone Cellar", in German "Funkzone Keller"; the kind is also shown as the model), so you can assign it to an area. Its binary sensor is on while the zone is open; other zone states (such as tamper) show as unknown, with the panel's value in the attribute `zone_state`. The API doesn't tell detector types apart, so the sensors have no device class until you choose one per zone in the options. An **Omit** switch per omittable zone omits it for one arming cycle ("ausblenden"); the panel includes it again at the next disarm, and the switch follows. A diagnostic **Problem** sensor per zone is on for such other states or while a fault (other than "zone open") affects the zone; its attribute `faults` lists those faults.
- **Faults** on the panel device: the number of current faults, all of them in the attribute `faults` and a readable list in `summary` (one line per fault). This includes faults of components the API doesn't list otherwise, such as a repeater's low battery. Open zones, which the panel also reports as faults (even when disarmed), are left out here and counted by **Open zones**.
- **Problem** on the panel device: on while **Faults** is above 0.
- **Open zones** per selected partition: the number of the partition's zones that are open and not omitted, listed in the attributes.
- **Arming** per selected partition (e.g. "House arming"): "Blocked" while **Open zones** is above 0 or a fault prevents arming the partition, "Free" otherwise; the attributes name the open zones and those faults. Open entry doors count too, since arming via the API fails while one is open, although the panel doesn't report it as a fault.
- **Installer lock** (diagnostic) on the panel device.
- **Polling and connection** (diagnostic) on the panel device: **Round duration** (how long the last polling round's reads took, without the log), **Failed rounds** (failed rounds in a row, 0 while polling works; attributes `paused` and `last_error`), and, disabled by default, **Connection setup** (the last connection's setup time: about 0.013 s when the TLS session is resumed, about 6.5 s for a full handshake) and **Full handshakes** (connection setups without session resumption since the start). Durations are in seconds with two significant digits; you can show them in ms in the entity's settings. They stay available while the panel isn't reachable and cost no extra request.
- **Log** on the panel device: an event entity that fires once for each new entry of the panel's log, with the event type `normal`, `alarm` or `trouble` and the attributes `text` (the panel's own text), `time` (when the panel wrote the entry), `user` and `user_name`, `partition` and `zone` where the entry names them. The log is read every 5 minutes by default, so entries arrive up to that much later. The first read takes the existing log as known and fires nothing, and a restart doesn't repeat entries. Each entry also appears in Home Assistant's logbook with its text and the panel's time, so you can see who armed or disarmed at the keypad or in the app without an automation; the logbook entry comes from the event `secvest_log_entry`, which carries the same data and the entity id.

  **Two logbook rows per entry:** the logbook shows each entry twice, first as "Normal" (or "Alarm", "Trouble"), then with its text. The first row is the log entity's own: Home Assistant writes a row for every entity that changes, and for an event entity that row only shows the event type, which an integration can't describe further. The second row comes from `secvest_log_entry` and is the readable one. Excluding the log entity from the recorder or the logbook doesn't leave only the readable row: since `secvest_log_entry` names the entity, Home Assistant drops both rows.

Entity ids start with the installation's name, followed by the partition or zone, e.g. `alarm_control_panel.alarmanlage_ground_floor` or `binary_sensor.alarmanlage_front_door`; the kind of zone isn't part of them. They are set once when the entities are created, with entity names in Home Assistant's language at that time (e.g. `sensor.alarmanlage_faults` in English, `sensor.alarmanlage_storungen` in German); you can rename them in Home Assistant.

**Names follow the panel, ids don't:** when a partition or zone is renamed at the panel, the next polling round notices it (by its number, not its name) and the integration reloads once, without another request: the alarm panel, the partition's sensors and the zone's device and entities show the new name. Renaming the installation at the panel isn't noticed by itself, since polling doesn't read it; **Configure** → **Take over names from the panel** reads it once and names the panel device after it. Entity ids, the entry's title on the integration page (the first part of the ids of entities added later) and names you set in Home Assistant stay.

To show the faults on a dashboard, use a Markdown card:

```yaml
type: markdown
title: Alarm panel faults
content: >
  {{ state_attr('sensor.alarmanlage_faults', 'summary') or 'No faults' }}
```

Replace `sensor.alarmanlage_faults` with the entity id of your faults sensor (in German, for example, `sensor.alarmanlage_storungen`).

Home Assistant keeps the mean, minimum and maximum of the round duration and the connection setup as long-term statistics. A statistics graph card shows them per day, for example:

```yaml
type: statistics-graph
title: Polling the panel
entities:
  - sensor.alarmanlage_round_duration
period: day
stat_types: [mean, min, max]
chart_type: line
```

For a value to use in automations (e.g. the mean of the last hour), add a statistics helper (Settings → Devices & services → Helpers → Statistics) with the round duration as its source.

## Arming and disarming

The alarm panel arms away (full set), arms home (part set, "intern aktivieren") and disarms, with a code once you have added one.

**Codes:** in the integration's options (**Configure** → **Codes**) you add, change and remove codes: a user name and a four-digit code each (the panel's codes have four digits too). Changing codes doesn't reload the integration. The alarm panel asks for a code to arm and to disarm and shows the user as the one who did it, until the state changes in another way (at the keypad, in the app or by an alarm). Everything else, like omitting zones, needs no code. You may use the same codes as at the keypad, but Home Assistant can't check them against the panel: a code changed at the keypad has to be changed here too. Codes are stored only as a salted hash; to change one, choose its user under **Change code** and enter a new one. The hash protects against casual reading of the configuration, not against someone with access to Home Assistant's storage: a four-digit code has only 10,000 possible values.

**Without codes:** as long as no code is added, the alarm panel asks for none, and no user is shown; a code sent anyway (e.g. by HomeKit Bridge) is ignored. Then everyone and everything with access to Home Assistant — its users, automations, HomeKit — can arm and disarm. Add a code to restrict that; with the first code, a code is needed for arming and disarming, and removing the last one lifts it again.

The panel doesn't switch directly between the two armed modes, so switching disarms first and then arms again; the alarm panel keeps showing the previous mode until the switch is done, and if disarming fails, the message says so. Every command is checked by reading the partition again afterwards, whatever the panel answered: it counts as done only if the partition really is in the requested state. If the partition already is in the requested state (e.g. armed at the keypad meanwhile), nothing is sent, and the user shown doesn't change.

If arming or disarming fails, the action fails with a message, shown in the UI and in automation traces, and the event `secvest_arming_failed` is fired once, for every failure, with `entry_id`, `partition`, `partition_name`, `requested` (`set`, `partset` or `unset`), `reason`, `step` (`command`, `disarm_first` when switching between the armed modes failed at disarming, or `acknowledge_first` when acknowledging an alarm before disarming failed), `zones` (ids), `zone_names`, `faults`, `user` (the name of the code that was entered), `entity_id` (the alarm panel), `can_omit_and_arm` (only open zones that can be omitted blocked arming, see "Omit open zones and arm") and `omit_and_arm` (the failure is one of that action). The event carries the calling action's context, so an automation can tell where the command came from: `trigger.event.context.user_id` is set when a user acted in Home Assistant, `parent_id` when an automation or script did, and neither for other callers such as HomeKit Bridge. The reasons:

| `reason` | Meaning |
|---|---|
| `blocked` | The panel refused and named the blocking zones or faults (certain) |
| `refused` | The panel refused without naming a reason (e.g. a partition without zones) |
| `no_permission` | The panel user has no rights for this partition (certain) |
| `likely_open_zones` | The panel answered but didn't arm; open zones that aren't omitted are the likely reason (e.g. an open entry door, depending on the panel's configuration) |
| `likely_faults` | As above, with faults that prevent arming as the likely reason |
| `error` | The panel answered with another error |
| `unknown` | The panel didn't change the state and gave no hint why |
| `unreachable` | The panel couldn't be reached before sending; nothing was sent, the state is unchanged |
| `not_verified` | The command was sent, but the result couldn't be read back (connection lost, timeout): the state is unclear, check it at the panel |
| `installer_locked` | The installer is logged in at the panel; nothing was changed |
| `arm_during_alarm` | Arming during an alarm isn't sent; disarm first |
| `auth_failed` | The panel rejected the credentials; Home Assistant asks to reauthenticate |
| `certificate_changed` | The panel presented another certificate than the trusted one; nothing was sent, Home Assistant asks to confirm the new one |
| `invalid_code` | The code entered doesn't match any configured code (`user` is empty); nothing was sent |
| `not_omittable` | Omit open zones and arm: something else than open zones that can be omitted blocks arming (`zones`, `faults`); nothing was sent |
| `omit_failed` | Omit open zones and arm: a zone couldn't be omitted (`zones`); zones omitted before were included again, nothing was armed |
| `still_omitted` | Omit open zones and arm: after arming failed (its own event), these zones couldn't be included again and stay omitted until the next disarm; check them |

One failure has no event: arming **without any code** while codes are configured. Home Assistant refuses it itself ("code required") before the integration is called. Home Assistant's UI always asks for the code; for HomeKit Bridge, set the code in its `entity_config`.

An automation can react to failed arming, for example with a notification. Home Assistant's own UI already shows the message, so this one only notifies for commands from elsewhere, such as the Apple Home app via HomeKit Bridge, which shows no reason:

```yaml
triggers:
  - trigger: event
    event_type: secvest_arming_failed
conditions:
  - "{{ trigger.event.context.user_id is none and trigger.event.context.parent_id is none }}"
actions:
  - action: notify.notify
    data:
      message: "Alarm not armed ({{ trigger.event.data.reason }}): {{ trigger.event.data.zone_names | join(', ') }}"
```

**Omit open zones and arm:** the action `secvest.omit_and_arm` (target: the alarm panel; `mode`: `away` or `home`; `code` as for arming) omits the open zones that block arming once, then arms, each step checked. It reads the partition, its zones and the faults first and sends nothing unless open zones that can be omitted are all that block it (`not_omittable` otherwise). The panel includes omitted zones again at the next disarm, so they are omitted for this arming only; if arming still fails, the zones are included again, since they would otherwise be unguarded at the next arming. For `home`, zones that aren't monitored when armed internally (`inner: false`) are left to the panel. The usual way to use it is a notification with an action when arming fails, e.g. from the Apple Home app, which shows no reason. This extends the example above (iOS companion app; replace the notify action and the code):

```yaml
triggers:
  - trigger: event
    event_type: secvest_arming_failed
conditions:
  # commands from elsewhere (e.g. HomeKit), or this automation's own follow-up
  - "{{ (trigger.event.context.user_id is none and trigger.event.context.parent_id is none) or trigger.event.data.omit_and_arm }}"
actions:
  - variables:
      d: "{{ trigger.event.data }}"
      action_id: "SECVEST_OMIT_AND_ARM_{{ context.id }}"
  - action: notify.mobile_app_my_iphone
    data:
      title: "Alarm not armed"
      message: "{{ d.reason }}: {{ (d.zone_names + d.faults) | join(', ') }}"
      data:
        actions: "{{ [{'action': action_id, 'title': 'Omit once and arm'}] if d.can_omit_and_arm else [] }}"
  - if: "{{ d.can_omit_and_arm }}"
    then:
      - wait_for_trigger:
          - trigger: event
            event_type: mobile_app_notification_action
            event_data:
              action: "{{ action_id }}"
        timeout: "00:05:00"
        continue_on_timeout: false
      - action: secvest.omit_and_arm
        target:
          entity_id: "{{ d.entity_id }}"
        data:
          mode: "{{ 'home' if d.requested == 'partset' else 'away' }}"
          code: !secret secvest_code
mode: parallel
```

A failure of the follow-up action fires the event again with `omit_and_arm: true` and is notified the same way, without offering the action once more. `!secret` works in automations kept in YAML files; an automation edited in the UI would have to contain the code itself.

**HomeKit:** HomeKit Bridge can't ask for a code, so it passes the one set in its configuration (`entity_config` → `code`). Adding a separate code named e.g. "HomeKit" shows HomeKit as the one who armed or disarmed, and can be removed on its own.

**Alarms:** disarming during an alarm acknowledges the alarm first and then disarms, each step checked; there is no separate acknowledge button. An alarm acknowledged elsewhere (keypad, app) shows as triggered with `acknowledged: true` until it is disarmed. Arming during an alarm isn't possible; disarm first. Resetting the panel after an alarm isn't possible through the API. Acknowledging hasn't been tested at a real panel, since that would need a real alarm; it follows the documented behaviour of the panel and the official app.

While the installer is logged in, commands fail with a message saying so; once a polling round has seen the lock, they fail at once without sending anything to the panel, and the entities keep showing their last state. If the result of a command can't be read back, the message says that too; check the state at the panel then.

## Exit and entry delays

The panel's API reports no transitional states, so the alarm panel never shows `arming` or `pending`:

- **Arming from Home Assistant** (or the official app) takes effect immediately, without an exit time, whatever exit mode the panel uses at the keypad. Leave the house before arming, or arm internally.
- **Arming at the keypad** with an exit time: the partition reports disarmed until the exit time is over, then armed. Home Assistant shows the same.
- **Entry delay:** when an entry door opens while armed, the partition keeps reporting its armed state until it is disarmed or the alarm goes off. Home Assistant can't tell that an entry delay is running.

The panel's log does record the start of an entry delay, but a "pending" state based on it isn't planned: the entry time is typically well under a minute, while the log is read only every few minutes and a log request takes about 6 seconds, so the state would almost always show up too late.

## How it works

After setup the integration polls the panel every 30 seconds, never more often than every 24 seconds (the official app's own cycle). To poll on demand, use the action `homeassistant.update_entity` with any of the integration's entities; the same limit applies. If the panel doesn't answer, the integration waits longer after each failed attempt (up to 5 minutes) and pauses for 15 minutes after 5 failures in a row. Entities keep their last state through one or two failed rounds and are unavailable from the third one on (about 3 minutes), so Home Assistant and HomeKit don't show an outdated state for long; the first successful round brings them back.

While the installer is logged in at the panel, its API is locked. The diagnostic sensor **Installer lock** on the panel device is on meanwhile; the other entities keep their last state, and each polling round costs a single request until the installer has logged out.

If the panel later rejects the credentials (for example after the password was changed at the panel), the integration stops sending anything after that single 401, also after a restart of Home Assistant, and asks you to reauthenticate: enter user code and password again; they are checked with a single request. If a reverse proxy answered the 401, fix the proxy first, then reauthenticate.

## Diagnostics

For a bug report, download the diagnostics: Settings → Devices & services → ABUS Secvest → ⋮ → **Download diagnostics**. The file holds the panel's answers of the last polling round, the connection and polling state and the settings. The address, credentials and codes, and all names and texts (installation, partitions, zones, zone groups, users, fault texts) are replaced by `**REDACTED**`; ids and states stay. Downloading sends nothing to the panel. Look through the file before sharing it anyway.

## Limitations

- **Delay:** changes show up with the next polling round, by default within 30 seconds.
- **No exit or entry delay states:** see "Exit and entry delays".
- **Arming** (blocked/free) covers open zones and the faults the panel reports as preventing arming; the panel may still refuse arming for reasons it reports only when arming is requested.
- **Faults:** the sensor shows the list the panel returns; whether the panel shortens very long lists is unknown.
- **Omitting** goes through the first selected partition that lists the zone. If the panel user has no rights there, omitting fails with "no permission", even if the user may operate another selected partition with the same zone.
- **Log:** the panel's log is read once in full and then only its new entries, every 5 minutes. Entries written after a panel restart before its clock is set (dated 1 January 2019), and more than 600 new entries between two reads, can be missed. The panel stores its local time without a time zone; the integration reads it in Home Assistant's time zone, so set both to the same one.
- **Entities are tied to the config entry:** the API reports no serial number, so removing and re-adding the integration creates new entities (their entity ids can be renamed back in Home Assistant). For a new address or password use **Reconfigure** instead (see "Setup").

## Documentation

- [Architecture](docs/architecture.md)
- [Decision records](docs/adr/)
- [Glossary](docs/glossary.md)
- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## License

[MIT](LICENSE)
