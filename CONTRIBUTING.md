# Contributing

Thanks for helping. This integration talks to security equipment in people's homes, so a few rules here are stricter than usual. Please read this page and the [architecture](docs/architecture.md) before a larger change, and open an issue first to agree on it.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Git. uv installs Python 3.14 (see `.python-version`) and the development tools:

```bash
git clone https://github.com/thomasdanz/ha-secvest.git
cd ha-secvest
uv sync
```

If `uv sync` fails with "resolved to Python 3.14.0rc2, incompatible with >=3.14.2", your uv is too old (before about 0.9) to know the final Python 3.14; update it, e.g. with `pip install -U uv`.

The development dependencies pin `pytest-homeassistant-custom-component` to the current Home Assistant release, which pins Home Assistant itself (ADR 0003).

## Checks

```bash
uv run pytest                                # tests
uv run ruff check .                          # lint
uv run ruff format .                         # format
uv run mypy custom_components tests scripts  # type checks
```

CI runs the same checks, the tests against the current and the previous Home Assistant release and the declared minimum, with a coverage of at least 95 % (lines and branches), plus hassfest and the HACS validation.

**Tests never talk to a real panel.** They run against the specification's fixtures and a fake panel (`tests/fake_panel.py`), a small HTTPS server that behaves like the panel and fails a test on requests the specification doesn't know, on parallel requests and on retries after a 401. The fixtures are copied from the [`secvest-api`](https://github.com/thomasdanz/secvest-api) repository, checked out next to this one, with `uv run python scripts/sync_fixtures.py` (ADR 0004).

## Rules for talking to the panel

The panel is fragile security equipment; these rules are not negotiable (see "Don'ts" in the architecture):

- **One connection, strictly sequential requests,** with TLS session resumption. No parallel requests, no `Connection: close`.
- **Never more load than the official app:** a status interval of at least 24 seconds, the log rarely and incrementally.
- **Never retry after a failed login (401).** Stop all requests; failed logins may count towards a code tamper alarm.
- **No automatic retries of commands,** except the one documented case.
- **Verify, don't assume:** after every command, also after an error response, read the real state before reporting success or failure.
- **No guessed requests:** only calls documented in the specification.
- **Logic never relies on panel texts** (descriptions, names); only on ids, types and states.
- **The API client (`custom_components/secvest/api/`) imports nothing from Home Assistant** (ADR 0002); a test enforces it.

If you test against your own panel: never trigger an alarm, never run anything in parallel with the integration (pause its polling first), and never retry after a 401.

## Code style

- Everything in English: code, comments, documentation, commits, issues.
- Ruff and mypy (strict) must pass. Write code like the surrounding code: its naming, comment density and idioms.
- Use the manufacturer's terms from the [glossary](docs/glossary.md) (partition, zone, omit, …).
- Unknown values from the panel are kept raw and logged once, never a crash.
- **No installation-specific data** anywhere — code, tests, docs, commits, issues: no host names, IP addresses, serial numbers, codes, zone, room or partition names. Use made-up examples.

## Pull requests

- One issue per pull request, on a branch named after it: its number and a short description, e.g. `123-installer-lock-refuse`.
- **Definition of Done** for every change:
  - tests pass and cover the change;
  - the README is updated for user-visible changes;
  - UI texts are in English and German, using the glossary terms;
  - a changelog entry under "Unreleased";
  - updates keep working without setting up again: a change to the stored data comes with a migration and a test (see "Updates" in the architecture), unique ids never change.
- Design changes update the architecture or add a decision record (`docs/adr/`).
- The maintainer sets versions and releases; a pull request doesn't bump the version unless agreed.

## Reporting bugs

Use the bug report form and attach the diagnostics download (Settings → Devices & services → ABUS Secvest → ⋮ → Download diagnostics); it is redacted, but look through it before sharing. Security issues go through private vulnerability reporting, see [SECURITY.md](SECURITY.md).
