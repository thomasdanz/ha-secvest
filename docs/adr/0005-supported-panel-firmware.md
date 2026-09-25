# 0005: Supported panel firmware

- **Status:** accepted
- **Date:** 2026-09-25

## Context

The integration is built on a reverse-engineered, unofficial API. Its behaviour was analysed and tested on one panel: a Secvest Touch FUAA50500 with firmware v3.01.31. Other firmware versions may differ in endpoints, values or error behaviour, and they will not be tested by the maintainers.

The REST API doesn't report the firmware version; it is only shown at the keypad and in the web interface. The integration therefore can't detect or enforce a version.

## Options

1. Refuse to run on unknown firmware — impossible without a version from the API.
2. Read the version from the web interface — requires a web login with its own risks (see the web interface epic); not acceptable just for a version check.
3. Declare the tested version clearly and stay tolerant towards differences.

## Decision

Option 3:

- The README, the setup dialog and the diagnostics state the tested model and firmware version (FUAA50500, v3.01.31).
- The API client tolerates unknown values instead of failing, and logs them once, so that differences on other firmware show up in logs and diagnostics.
- Bug reports ask for the panel model and firmware version.
- Reports confirming other versions are collected in the README ("Reported to work with").

## Consequences

- Users of other firmware can try the integration at their own risk and get clear information about it.
- The tested version must be updated deliberately when the reference panel is updated.
