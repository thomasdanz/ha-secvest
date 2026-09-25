# 0003: Supported Home Assistant versions

- **Status:** accepted
- **Date:** 2026-09-25

## Context

Home Assistant releases monthly and deprecates APIs regularly. A support promise is only worth something if it is tested; claiming compatibility with versions that nobody tests is a paper tiger. At the time of writing the current release is 2026.9, which requires Python 3.14.

`pytest-homeassistant-custom-component` is released for every Home Assistant version and pins that version. CI can therefore test against several Home Assistant versions in parallel, each in a fresh environment, without installing anything locally.

## Options

1. Only the latest release.
2. The latest and the previous release, both tested in CI.
3. The last six releases — more matrix jobs and more compatibility code; not justified before the first release.

## Decision

Option 2:

- Supported (and tested) are the current and the previous Home Assistant release (at the time of writing 2026.9 and 2026.8).
- CI runs the test suite against both versions in a matrix on every push and pull request.
- When a new Home Assistant release comes out, the window moves on (current + previous); the change is noted in the changelog.
- A scheduled CI run tests weekly against the upcoming Home Assistant beta, to see breaking changes before they are released. A failure there doesn't block anything but opens an issue.
- Older versions are not tested, but compatibility with them is not broken on purpose: newer Home Assistant APIs are only adopted when there is a reason (a deprecation, a needed feature), not for their own sake. Users of older versions may find that the integration still works, without any promise.
- Accordingly, the minimum declared in `hacs.json` is the oldest version the code actually needs, and it is raised only when a change requires it — not automatically with every release.
- The tested window can be widened later if users need it, as long as every supported version is tested.

## Consequences

- Every supported version is actually tested.
- Users on older Home Assistant versions have to update or use an older release of the integration.
- The CI matrix needs a small update every month; the declared minimum changes only when the code requires it.
