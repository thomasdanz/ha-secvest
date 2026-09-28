# ABUS Secvest for Home Assistant

A Home Assistant custom integration for the ABUS Secvest alarm panel. It talks to the panel's local REST API, the one the official app uses, as documented in [`secvest-api`](https://github.com/thomasdanz/secvest-api).

> **Status:** v0.2: shows partitions, zones and faults, arms and disarms (acknowledging an alarm on the way) and omits zones. Log events follow with v0.3 (see the [milestones](https://github.com/thomasdanz/ha-secvest/milestones)).

> **Disclaimer:** This is an unofficial community project, not affiliated with or endorsed by ABUS. The panel is security equipment: use this integration at your own risk.

## Supported versions

- **Panel:** tested with the Secvest Touch FUAA50500 running firmware v3.01.31. The API doesn't report the firmware version, so the integration can't check it; other models and firmware versions are untested. Unknown values from the panel are kept and logged once instead of breaking anything.
- **Reported to work with:** no other models or firmware versions yet. If it works (or doesn't) on yours, please open an issue with the panel model and firmware version.
- **Home Assistant:** tested with 2026.9 and 2026.8, the current and the previous release. Older versions aren't tested.

## Safety notes

- The panel is security equipment in an inhabited building. Test automations that use it carefully.
- **Use a separate panel user** for Home Assistant: the level "normal user" is enough, with rights for exactly the partitions Home Assistant should operate. Every user sees all partitions, but the panel refuses commands on the others.
- **Polling limits:** the integration never polls more often than every 24 seconds, the official app's own cycle, and backs off when the panel doesn't answer. An overloaded panel can stop responding and may need a power cycle.
- **No automatic retries after failed logins:** failed logins may count towards a code tamper alarm, so the integration never repeats rejected credentials.
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
- **Verify certificate:** leave off for the panel's own self-signed certificate.
- **Advanced → User-Agent:** only needed for a reverse proxy that filters by User-Agent.

The credentials are checked with a single request. If the panel rejects them, nothing is retried automatically.

Then select the **partitions** Home Assistant should show and operate; their zones are added automatically. The panel doesn't reveal which partitions the user may operate, so all of them are listed; partitions without zones are deselected.

Finally choose what kind of detector each **zone** is (door, window, garage door, motion, …) and exclude zones you don't want in Home Assistant. The panel doesn't tell detector types apart, so this is up to you; you can change it later in the options.

## Options

In the integration's options (Settings → Devices & services → ABUS Secvest → Configure) you can change the selected partitions, the status interval (at least 24 seconds) and, under Advanced, the User-Agent. The second step lists the zones of the selected partitions: choose a device class per zone (door, window, motion, …; the panel doesn't tell detector types apart) and exclude zones you don't want in Home Assistant. Saving reloads the integration; nothing is sent to the panel while you change the options.

If a selected partition has no zones anymore (for example after the installer moved its detectors to another partition), a repair issue suggests deselecting it in the options; ignore it if the partition is meant to be empty. It goes away by itself once the partition has zones again.

## Zone groups

Zones that belong to one opening, such as the two wings of a window, can be combined into a zone group: on the integration's page choose **Add zone group**, give it a name, select at least two zones and choose how the group is shown (window, door, …, or "Same as the zones") and, optionally, its area. The group gets its own device "Zone group <name>" with a sensor that is on while any of its zones is open, e.g. `binary_sensor.alarmanlage_living_room`. The zones keep their own devices and entities. **Hide grouped zones** hides the zones' entities in Home Assistant; they keep working and can still be used in automations. Each group can be changed or deleted on the integration's page. If a zone of a group disappears (removed at the panel, or its partition deselected), the group sensor ignores it and a repair issue asks you to reconfigure the group. A zone group is a Home Assistant feature; the panel knows nothing about it.

## Entities

- **Alarm panel** per selected partition, named after the partition: disarmed, armed home (internally armed), armed away or triggered. An acknowledged alarm is still shown as triggered, with the attribute `acknowledged`; the attribute `panel_state` holds the panel's own state. While the panel reports an alarm, `alarm_type` names its kind in the panel's own terms (burglar alarm, fire alarm, hold-up alarm, …) and `alarm_zones` the zones that raised it; the alarm is detected from the partition's state itself, so it shows even if these details can't be read. You can arm (away or home, i.e. internally) and disarm it; see "Arming and disarming".
- **Zones:** each zone of the selected partitions is its own device below the panel device, named with its kind (e.g. "Wireless zone Cellar", in German "Funkzone Keller"; the kind is also shown as the model), so you can assign it to an area. Its binary sensor is on while the zone is open; other zone states (such as tamper) show as unknown, with the panel's value in the attribute `zone_state`. The API doesn't tell detector types apart, so the sensors have no device class until you choose one per zone in the options. An **Omit** switch per omittable zone omits it for one arming cycle ("ausblenden"); the panel includes it again at the next disarm, and the switch follows. A diagnostic **Problem** sensor per zone is on for such other states or while a fault (other than "zone open") affects the zone.
- **Faults** on the panel device: the number of current faults, all of them in the attribute `faults` and a readable list in `summary` (one line per fault). This includes faults of components the API doesn't list otherwise, such as a repeater's low battery. Open zones, which the panel also reports as faults (even when disarmed), are left out here and counted by **Open zones**.
- **Problem** on the panel device: on while **Faults** is above 0.
- **Open zones** per selected partition: the number of the partition's zones that are open and not omitted, listed in the attributes.
- **Arming blocked** per selected partition: "Blocked" while **Open zones** is above 0 or a fault prevents arming the partition, "Possible" otherwise; the attributes name the open zones and those faults. Open entry doors count too, since arming via the API fails while one is open, although the panel doesn't report it as a fault.
- **Installer lock** (diagnostic) on the panel device.

Entity ids start with the installation's name, followed by the partition or zone, e.g. `alarm_control_panel.alarmanlage_ground_floor` or `binary_sensor.alarmanlage_front_door`; the kind of zone isn't part of them. They are set once when the entities are created, with entity names in Home Assistant's language at that time (e.g. `sensor.alarmanlage_faults` in English, `sensor.alarmanlage_storungen` in German); you can rename them in Home Assistant.

To show the faults on a dashboard, use a Markdown card:

```yaml
type: markdown
title: Alarm panel faults
content: >
  {{ state_attr('sensor.alarmanlage_faults', 'summary') or 'No faults' }}
```

Replace `sensor.alarmanlage_faults` with the entity id of your faults sensor (in German, for example, `sensor.alarmanlage_storungen`).

## Arming and disarming

The alarm panel arms away (full set), arms home (part set, "intern aktivieren") and disarms, with a code.

**Codes:** on the integration's page, **Add code** stores a user name and a four-digit code (the panel's codes have four digits too). The alarm panel asks for a code to arm and to disarm and shows the user as the one who did it. With no code configured, arming and disarming aren't possible; everything else, like omitting zones, needs no code. You may use the same codes as at the keypad, but Home Assistant can't check them against the panel: a code changed at the keypad has to be changed here too. Codes are stored only as a salted hash; to change one, enter a new one.

The panel doesn't switch directly between the two armed modes, so switching disarms first and then arms again; the alarm panel keeps showing the previous mode until the switch is done, and if disarming fails, the message says so. Every command is checked by reading the partition again afterwards, whatever the panel answered: it counts as done only if the partition really is in the requested state.

If arming or disarming fails, the action fails with a message, shown in the UI and in automation traces, and the event `secvest_arming_failed` is fired once, for every failure, with `entry_id`, `partition`, `partition_name`, `requested` (`set`, `partset` or `unset`), `reason`, `step` (`command`, `disarm_first` when switching between the armed modes failed at disarming, or `acknowledge_first` when acknowledging an alarm before disarming failed), `zones` (ids), `zone_names`, `faults` and `user` (the name of the code that was entered). The event carries the calling action's context, so an automation can tell where the command came from: `trigger.event.context.user_id` is set when a user acted in Home Assistant, `parent_id` when an automation or script did, and neither for other callers such as HomeKit Bridge. The reasons:

| `reason` | Meaning |
|---|---|
| `blocked` | The panel refused and named the blocking zones or faults (certain) |
| `refused` | The panel refused without naming a reason (e.g. a partition without zones) |
| `no_permission` | The panel user has no rights for this partition (certain) |
| `likely_open_zones` | The panel answered but didn't arm; open zones that aren't omitted are the likely reason (e.g. an open entry door, depending on the panel's configuration) |
| `likely_faults` | As above, with faults that prevent arming as the likely reason |
| `error` | The panel answered with another error |
| `unknown` | The panel didn't change the state and gave no hint why |
| `not_verified` | The result couldn't be read back (connection lost, timeout): the state is unclear, check it at the panel |
| `installer_locked` | The installer is logged in at the panel; nothing was changed |
| `arm_during_alarm` | Arming during an alarm isn't sent; disarm first |
| `auth_failed` | The panel rejected the credentials; Home Assistant asks to reauthenticate |
| `invalid_code` | The code entered doesn't match any configured code (`user` is empty); nothing was sent |
| `no_codes` | No code is configured; nothing was sent |

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

**HomeKit:** HomeKit Bridge can't ask for a code, so it passes the one set in its configuration (`entity_config` → `code`). Adding a separate code named e.g. "HomeKit" shows HomeKit as the one who armed or disarmed, and can be removed on its own.

**Alarms:** disarming during an alarm acknowledges the alarm first and then disarms, each step checked; there is no separate acknowledge button. An alarm acknowledged elsewhere (keypad, app) shows as triggered with `acknowledged: true` until it is disarmed. Arming during an alarm isn't possible; disarm first. Resetting the panel after an alarm isn't possible through the API. Acknowledging hasn't been tested at a real panel, since that would need a real alarm; it follows the documented behaviour of the panel and the official app.

While the installer is logged in, commands fail with a message saying so; once a polling round has seen the lock, they fail at once without sending anything to the panel, and the entities keep showing their last state. If the result of a command can't be read back, the message says that too; check the state at the panel then.

## Exit and entry delays

The panel's API reports no transitional states, so the alarm panel never shows `arming` or `pending`:

- **Arming from Home Assistant** (or the official app) takes effect immediately, without an exit time, whatever exit mode the panel uses at the keypad. Leave the house before arming, or arm internally.
- **Arming at the keypad** with an exit time: the partition reports disarmed until the exit time is over, then armed. Home Assistant shows the same.
- **Entry delay:** when an entry door opens while armed, the partition keeps reporting its armed state until it is disarmed or the alarm goes off. Home Assistant can't tell that an entry delay is running.

The panel's log does record the start of an entry delay. An optional "pending" state based on it is planned for a later version; it will depend on the panel's language, since the log only has texts there.

## How it works

After setup the integration polls the panel every 30 seconds, never more often than every 24 seconds (the official app's own cycle). To poll on demand, use the action `homeassistant.update_entity` with any of the integration's entities; the same limit applies. If the panel doesn't answer, the integration waits longer after each failed attempt (up to 5 minutes) and pauses for 15 minutes after 5 failures in a row; entities keep their last state until the pause starts.

While the installer is logged in at the panel, its API is locked. The diagnostic sensor **Installer lock** on the panel device is on meanwhile; the other entities keep their last state, and each polling round costs a single request until the installer has logged out.

If the panel later rejects the credentials (for example after the password was changed at the panel), the integration stops sending anything, also after a restart of Home Assistant, and asks you to reauthenticate: enter user code and password again; they are checked with a single request.

## Limitations

- **Delay:** changes show up with the next polling round, by default within 30 seconds.
- **No exit or entry delay states:** see "Exit and entry delays".
- **Arming blocked** covers open zones and the faults the panel reports as preventing arming; the panel may still refuse arming for reasons it reports only when arming is requested.
- **Faults:** the sensor shows the list the panel returns; whether the panel shortens very long lists is unknown.
- **Entities are tied to the config entry:** the API reports no serial number, so removing and re-adding the integration creates new entities. Their entity ids can be renamed back in Home Assistant.

## Documentation

- [Architecture](docs/architecture.md)
- [Decision records](docs/adr/)
- [Glossary](docs/glossary.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
