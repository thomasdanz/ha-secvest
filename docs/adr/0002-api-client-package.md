# 0002: API client inside the integration, extractable later

- **Status:** accepted
- **Date:** 2026-09-25

## Context

The panel communication should live in an API client without Home Assistant dependencies, so it can be tested on its own. Integrations in Home Assistant core must use a separate library published on PyPI; custom integrations may ship their code directly.

## Options

1. **Separate repository and PyPI package.** Clean separation and reusable by other projects, but a second release process, versions to keep in sync, and PyPI maintenance before the design is stable.
2. **Subpackage inside the integration** (`custom_components/secvest/api/`). One repository, one release, no extra maintenance; not reusable without copying.

## Decision

Option 2 for now. The subpackage must not import anything from Home Assistant; a test enforces this. Its public interface is kept small and documented, so it can be moved to its own package later without changes to its code (for example if the integration is ever proposed for Home Assistant core).

## Consequences

- Faster start, single release process.
- The boundary is enforced by a test instead of by package separation.
- Extraction to PyPI remains a mechanical step.
