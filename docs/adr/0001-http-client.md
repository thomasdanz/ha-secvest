# 0001: HTTP client with TLS session resumption

- **Status:** accepted
- **Date:** 2026-09-25

## Context

The panel's full TLS handshake takes about 6.5 s. Measured directly against a panel (not through a proxy), 2026-09-25:

| Situation | Result |
|---|---|
| First request, new connection | TLS 1.2, handshake 6.5 s |
| Second request on the same connection | 0.03 s — keep-alive works |
| Next request after 10 s idle | 0.03 s, connection still open |
| Next request after 30 s or 60 s idle | the panel had closed the connection; new handshake 6.5 s |
| New connection resuming the previous TLS session | handshake 0.013 s, `session_reused` true; session ticket lifetime 24 h |

The panel closes idle connections after somewhere between 10 and 30 seconds. The polling interval must be at least 24 s (never faster than the official app), so every polling round would find the connection closed. Without session resumption every round would cost a full handshake — slow, and a load on the panel.

Home Assistant integrations usually use `aiohttp`. Neither `aiohttp`, `httpx` nor asyncio's TLS support let the caller resume a TLS session on a new connection. Python's blocking `http.client` does, by passing the previous session to `SSLContext.wrap_socket(..., session=...)`.

## Options

1. **Keep the connection alive with extra requests** every few seconds. Rejected: adds load on the panel, which contradicts the load rules.
2. **aiohttp with keep-alive only.** Rejected: every polling round would pay the full handshake.
3. **Blocking client with TLS session cache, run in Home Assistant's executor.** A small transport based on `http.client`: one connection at a time, reconnects resume the last TLS session. Requests are strictly sequential anyway, so blocking in an executor thread costs nothing in practice. Proven by the measurement above.
4. **Custom asyncio transport** building TLS via `SSLContext.wrap_bio(..., session=...)`. Fully asynchronous, but reimplements a small HTTP/1.1 client (chunked transfer, keep-alive handling) with considerably more code and risk.

## Decision

Option 3. The transport:

- keeps one `http.client.HTTPSConnection` and stores the TLS session after each successful handshake;
- on every new connection passes the stored session for resumption, and falls back to a full handshake if the panel doesn't accept it;
- detects connections closed by the panel (`RemoteDisconnected`, broken pipe) before or during a request and retries that single request once on a fresh connection — this is a transport retry for an idempotent reconnect, not an authentication retry, and never happens after a 401;
- offers async methods that run the blocking request in an executor (`loop.run_in_executor`), guarded by the transport's lock, so only one request is in flight at a time — without importing Home Assistant (see ADR 0002);
- exposes the number of full handshakes and resumptions for diagnostics.

## Consequences

- Polling rounds cost about 13 ms of TLS setup instead of 6.5 s, as long as the session ticket is valid (24 h); roughly once a day a full handshake happens.
- The transport contains a little more code than an aiohttp call, and executor usage must be kept to one thread per panel.
- A retry after a closed connection must only be done for requests that are safe to repeat. For state changes (PUT), a closed connection before the request was sent is safe to retry. If it closed after sending, the verification refresh runs first; only if the target state was not reached is the command sent once more.
- If Python's asyncio gains support for TLS session resumption, option 4 becomes cheap and this decision can be revisited.
