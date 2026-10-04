# Security policy

## Reporting a vulnerability

Please report vulnerabilities in this integration **privately**, through GitHub's private vulnerability reporting: on this repository, go to **Security** → **Report a vulnerability**. Don't open a public issue for them.

Please include what you found, how to reproduce it, and which versions of the integration and Home Assistant you used. Never include real credentials, user codes, arming codes, addresses or other data of your installation; use made-up values.

This is a hobby project maintained by one person. You can expect an answer within a few weeks; a fix, if one is needed, comes with the next release.

## Supported versions

Only the latest release gets security fixes. Update to it before reporting.

## Scope

This project takes reports about **the integration**: its code, and how it handles your credentials, codes and data — for example credentials ending up in logs or the diagnostics, codes stored readably, or requests that put more load on the panel than documented.

Observations about **the panel itself** — its REST API, its firmware, the official app — are not vulnerabilities of this project. Behaviour seen on the panel is documented in [`secvest-api`](https://github.com/thomasdanz/secvest-api) as dated observations; whether it is a vulnerability, and fixing it, is a matter for the manufacturer. Please report such findings to ABUS.

## Using the integration safely

See "Safety notes" in the [README](README.md): use a separate panel user for Home Assistant with rights for only the partitions it should operate, and don't expose the panel's API to the internet unprotected.
