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

## Documentation

- [Architecture](docs/architecture.md)
- [Decision records](docs/adr/)
- [Glossary](docs/glossary.md)
- [Changelog](CHANGELOG.md)

## License

[MIT](LICENSE)
