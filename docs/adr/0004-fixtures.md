# 0004: Using the specification's fixtures in tests

- **Status:** accepted
- **Date:** 2026-09-25

## Context

The `secvest-api` repository contains redacted real responses of the panel ("fixtures"). The API client tests and the fake panel for integration tests should use exactly these responses, so that tests and specification can't drift apart.

## Options

1. **Git submodule** pointing to `secvest-api`. Always the real source, but submodules complicate cloning, CI and HACS downloads.
2. **Copy with a sync script.** `tests/fixtures/` holds a copy; a script updates it from a given `secvest-api` commit and records that commit.
3. **Download in CI.** No copy in the repository, but tests depend on network access and the other repository's availability.

## Decision

Option 2. `scripts/sync_fixtures.py` copies the fixtures and writes the source commit to `tests/fixtures/SOURCE`. Updating the fixtures is a normal, reviewable change.

## Consequences

- Tests run offline and reproducibly.
- The copy must be refreshed deliberately when the specification gains new fixtures.
