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

Then select the **partitions** Home Assistant should show and operate; their zones are added automatically. The panel doesn't reveal which partitions the user may operate, so all of them are listed; partitions without zones are deselected.

After setup the integration polls the panel every 30 seconds, never more often than every 24 seconds (the official app's own cycle). To poll on demand, use the action `homeassistant.update_entity` with any of the integration's entities; the same limit applies. If the panel doesn't answer, the integration waits longer after each failed attempt (up to 5 minutes) and pauses for 15 minutes after 5 failures in a row; entities keep their last state until the pause starts.

## Documentation

- [Architecture](docs/architecture.md)
- [Decision records](docs/adr/)
- [Glossary](docs/glossary.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
