# ABUS Secvest for Home Assistant

A Home Assistant custom integration for the ABUS Secvest alarm panel. It talks to the panel's local REST API, the one the official app uses, as documented in [`secvest-api`](https://github.com/thomasdanz/secvest-api).

> **Status:** under development, not usable yet. The first release (v0.1, read-only) is tracked in the [milestones](https://github.com/thomasdanz/ha-secvest/milestones).

**Tested panel:** Secvest Touch FUAA50500 with firmware v3.01.31. Other firmware versions are untested.

**Tested Home Assistant versions:** 2026.9 and 2026.8.

> **Disclaimer:** This is an unofficial community project, not affiliated with or endorsed by ABUS. The panel is security equipment: use this integration at your own risk.

## Setup

Add the integration in Home Assistant (Settings → Devices & services → Add integration → ABUS Secvest) and enter:

- **Address:** the panel's IP address or host name, optionally with a port (default 4433), or the https URL of a reverse proxy in front of it.
- **User code** and **password** of a panel user. Use a separate user for Home Assistant: the level "normal user" is enough, with rights for exactly the partitions Home Assistant should operate. The installer code doesn't work.
- **Verify certificate:** leave off for the panel's own self-signed certificate.
- **Advanced → User-Agent:** only needed for a reverse proxy that filters by User-Agent.

The credentials are checked with a single request. If the panel rejects them, nothing is retried automatically.

If the panel later rejects the credentials (for example after the password was changed at the panel), the integration stops sending anything, also after a restart of Home Assistant, and asks you to reauthenticate: enter user code and password again; they are checked with a single request.

Then select the **partitions** Home Assistant should show and operate; their zones are added automatically. The panel doesn't reveal which partitions the user may operate, so all of them are listed; partitions without zones are deselected.

After setup the integration polls the panel every 30 seconds, never more often than every 24 seconds (the official app's own cycle). To poll on demand, use the action `homeassistant.update_entity` with any of the integration's entities; the same limit applies. If the panel doesn't answer, the integration waits longer after each failed attempt (up to 5 minutes) and pauses for 15 minutes after 5 failures in a row; entities keep their last state until the pause starts.

While the installer is logged in at the panel, its API is locked. The diagnostic sensor **Installer lock** on the panel device is on meanwhile; the other entities keep their last state, and each polling round costs a single request until the installer has logged out.

## Entities

- **Alarm panel** per selected partition, named after the partition: disarmed, armed home (internally armed), armed away or triggered. An acknowledged alarm is still shown as triggered, with the attribute `acknowledged`; the attribute `panel_state` holds the panel's own state. Arming and disarming follow in a later version.
- **Zones:** each zone of the selected partitions is its own device below the panel device, so you can assign it to an area. Its binary sensor is on while the zone is open; other zone states (such as tamper) show as unknown, with the panel's value in the attribute `zone_state`. The API doesn't tell detector types apart, so the sensors have no device class yet; choosing one per zone comes with the options. A diagnostic **Problem** sensor per zone is on for such other states or while a fault (other than "zone open") affects the zone.
- **Faults** on the panel device: the number of current faults, all of them in the attribute `faults` and a readable list in `summary` (one line per fault). This includes faults of components the API doesn't list otherwise, such as a repeater's low battery.
- **Problem** on the panel device: on while any fault other than an open zone is present. The panel reports every open zone as a fault, even when disarmed; those are left out here.
- **Installer lock** (diagnostic) on the panel device.

To show the faults on a dashboard, use a Markdown card:

```yaml
type: markdown
title: Alarm panel faults
content: >
  {{ state_attr('sensor.alarmanlage_faults', 'summary') or 'No faults' }}
```

Replace `sensor.alarmanlage_faults` with the entity id of your faults sensor.

## Documentation

- [Architecture](docs/architecture.md)
- [Decision records](docs/adr/)
- [Glossary](docs/glossary.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
