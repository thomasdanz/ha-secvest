"""Tests for the fake panel itself (#65).

They use a minimal sequential client, so the fake's behaviour is checked
independently of the integration's transport.
"""

from base64 import b64encode
import http.client
import json
import socket
import ssl
import threading
import time
from typing import Any

import pytest

from custom_components.secvest.api.parsing import (
    loads,
    parse_alarms,
    parse_faults,
    parse_log,
    parse_partition,
    parse_partitions,
    parse_system,
    parse_zone,
    parse_zones,
)

from .fake_panel import FakePanel, Injection


class Client:
    """Sequential HTTPS client that keeps the connection and TLS session."""

    def __init__(self, panel: FakePanel, password: str | None = None) -> None:
        """Connect to the fake panel with its credentials."""
        self.panel = panel
        token = f"{panel.user_code}:{password or panel.password}"
        self.auth = "Basic " + b64encode(token.encode()).decode()
        self.context = ssl.create_default_context()
        self.context.check_hostname = False
        self.context.verify_mode = ssl.CERT_NONE
        self.conn: http.client.HTTPSConnection | None = None
        self.session: ssl.SSLSession | None = None

    def connect(self) -> http.client.HTTPSConnection:
        """Open a connection, resuming the last TLS session."""
        conn = http.client.HTTPSConnection(
            self.panel.host, self.panel.port, context=self.context, timeout=5
        )
        sock = socket.create_connection((self.panel.host, self.panel.port), 5)
        conn.sock = self.context.wrap_socket(sock, session=self.session)
        self.session = conn.sock.session
        self.conn = conn
        return conn

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Send one request and return status and body."""
        conn = self.conn or self.connect()
        data = None if body is None else json.dumps(body).encode()
        all_headers = {"Authorization": self.auth, "Accept": "application/json"}
        all_headers.update(headers or {})
        try:
            conn.request(method, path, body=data, headers=all_headers)
            response = conn.getresponse()
            payload = response.read()
        except Exception:
            self.close()
            raise
        return response.status, payload

    def json(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        """Send one request and decode the JSON body, if any."""
        status, payload = self.request(method, path, body)
        return status, loads(payload) if payload.strip() else None

    def close(self) -> None:
        """Close the connection."""
        if self.conn is not None:
            self.conn.close()
            self.conn = None


@pytest.fixture
def client(fake_panel: FakePanel) -> Client:
    """Return a client for the fake panel."""
    return Client(fake_panel)


def _state(client: Client, partition: int = 1) -> str:
    _, data = client.json("GET", f"/system/partitions-{partition}/")
    return str(data["state"])


def test_responses_parse(client: Client) -> None:
    """Every read has the format of the specification's fixtures."""
    parse_system(client.json("GET", "/system/")[1])
    parse_partitions(client.json("GET", "/system/partitions/")[1])
    parse_partition(client.json("GET", "/system/partitions-1/")[1])
    parse_zones(client.json("GET", "/system/partitions-1/zones/")[1])
    parse_zone(client.json("GET", "/system/partitions-1/zones-209/")[1])
    parse_alarms(client.json("GET", "/alarms/")[1])
    parse_faults(client.json("GET", "/faults/")[1])
    parse_log(client.json("GET", "/logs/")[1])


def test_response_format(client: Client) -> None:
    """JSON is pretty-printed with CRLF and sent chunked, like the panel's."""
    conn = client.connect()
    conn.request("GET", "/system/", headers={"Authorization": client.auth})
    response = conn.getresponse()
    body = response.read()
    assert response.getheader("Transfer-Encoding") == "chunked"
    assert response.getheader("Content-Type") == "application/json"
    assert b"\r\n" in body


def test_arm_and_disarm(fake_panel: FakePanel, client: Client) -> None:
    """Arming and disarming change the state and write log entries."""
    assert client.json("PUT", "/system/partitions-1/", {"state": "set"})[0] == 200
    assert _state(client) == "set"
    # switching directly between armed modes is ignored
    status, data = client.json("PUT", "/system/partitions-1/", {"state": "partset"})
    assert (status, data["state"]) == (200, "set")
    assert client.json("PUT", "/system/partitions-1/", {"state": "unset"})[0] == 200
    assert client.json("PUT", "/system/partitions-1/", {"state": "partset"})[0] == 200
    assert _state(client) == "partset"
    texts = [entry["desc"] for entry in fake_panel.log[:6]]
    assert texts == [
        "Ben003 TB 1 intern aktiv",
        "Ben003TB 1 Übergehen",
        "Ben 003 TB 1 deaktiv",
        "Ben 003 TB 1 aktiv",
        "Ben003TB 1 Übergehen",
        texts[5],
    ]


def test_open_zone_blocks_arming(fake_panel: FakePanel, client: Client) -> None:
    """An open omittable zone blocks arming with 409 until it is omitted."""
    fake_panel.open_zone("209")
    for state in ("set", "partset"):
        status, faults = client.json("PUT", "/system/partitions-1/", {"state": state})
        assert status == 409
        assert [f.zone_id for f in parse_faults(faults)] == ["209"]
    assert [f.zone_id for f in parse_faults(client.json("GET", "/faults/")[1])] == [
        "209"
    ]
    status, zone = client.json(
        "PUT", "/system/partitions-1/zones-209/", {"omitted": "true"}
    )
    assert (status, zone["omitted"]) == (200, True)
    assert client.json("GET", "/faults/")[1] == []
    assert client.json("PUT", "/system/partitions-1/", {"state": "set"})[0] == 200
    assert fake_panel.log[2]["desc"] == "Ben003 Zone209 ausgeblendet"
    # the omission ends at disarm
    client.json("PUT", "/system/partitions-1/", {"state": "unset"})
    assert client.json("GET", "/system/partitions-1/zones-209/")[1]["omitted"] is False
    assert fake_panel.log[0]["desc"] == "Ben053 Zone209 ausgeblendet ok "


def test_partition_without_zones(client: Client) -> None:
    """A partition without zones can't be armed, but disarming answers 200."""
    assert client.json("PUT", "/system/partitions-2/", {"state": "set"}) == (409, [])
    assert client.json("PUT", "/system/partitions-2/", {"state": "partset"}) == (
        409,
        [],
    )
    assert client.json("PUT", "/system/partitions-2/", {"state": "unset"})[0] == 200


def test_partset_without_internal_arming(fake_panel: FakePanel, client: Client) -> None:
    """A partition not set up for internal arming refuses partset."""
    fake_panel.partitions[1].internal_arming = False
    assert client.json("PUT", "/system/partitions-1/", {"state": "partset"})[0] == 409
    assert client.json("PUT", "/system/partitions-1/", {"state": "set"})[0] == 200


def test_user_without_rights(fake_panel: FakePanel, client: Client) -> None:
    """Without rights commands get an empty 403, open zones a 409 first."""
    fake_panel.rights = {2}
    for state in ("set", "partset", "unset"):
        assert client.request("PUT", "/system/partitions-1/", {"state": state}) == (
            403,
            b"",
        )
    for omitted in ("true", "false"):
        assert client.request(
            "PUT", "/system/partitions-1/zones-209/", {"omitted": omitted}
        ) == (403, b"")
    fake_panel.open_zone("209")
    assert client.json("PUT", "/system/partitions-1/", {"state": "set"})[0] == 409


def test_open_entry_door(fake_panel: FakePanel, client: Client) -> None:
    """An open entry/exit door silently ignores set and blocks partset.

    Observed on the reference panel; depends on its exit mode settings.
    """
    fake_panel.open_zone("219")
    assert client.json("GET", "/faults/")[1] == []
    status, data = client.json("PUT", "/system/partitions-1/", {"state": "set"})
    assert (status, data["state"]) == (200, "unset")
    status, faults = client.json("PUT", "/system/partitions-1/", {"state": "partset"})
    assert status == 409
    assert [f.zone_id for f in parse_faults(faults)] == ["219"]


def test_open_entry_door_without_rights(fake_panel: FakePanel, client: Client) -> None:
    """The door is checked before the rights, like the blocking faults.

    Inferred from the observed order (open zones before rights).
    """
    fake_panel.rights = {2}
    fake_panel.open_zone("219")
    status, data = client.json("PUT", "/system/partitions-1/", {"state": "set"})
    assert (status, data["state"]) == (200, "unset")
    assert client.json("PUT", "/system/partitions-1/", {"state": "partset"})[0] == 409


def test_zone_not_omittable(client: Client) -> None:
    """A zone that isn't omittable gives an empty 403."""
    assert client.request(
        "PUT", "/system/partitions-1/zones-219/", {"omitted": "true"}
    ) == (403, b"")


def test_alarm(fake_panel: FakePanel, client: Client) -> None:
    """An alarm is acknowledged first, then the partition is disarmed."""
    client.json("PUT", "/system/partitions-1/", {"state": "set"})
    fake_panel.trigger_alarm(1, "202")
    assert _state(client) == "set-alarm"
    alarms = parse_alarms(client.json("GET", "/alarms/")[1])
    assert [(a.partitions, a.zone_id) for a in alarms] == [((1,), "202")]
    assert client.json("PUT", "/system/partitions-1/", {"state": "acknowledged"})[1][
        "state"
    ] == ("acknowledged")
    assert client.json("PUT", "/system/partitions-1/", {"state": "unset"})[1][
        "state"
    ] == ("unset")
    assert client.json("GET", "/alarms/")[1] == []


def test_direct_unset_during_alarm_fails(fake_panel: FakePanel, client: Client) -> None:
    """Disarming without acknowledging is recorded as a violation."""
    client.json("PUT", "/system/partitions-1/", {"state": "set"})
    fake_panel.trigger_alarm(1, "202")
    client.json("PUT", "/system/partitions-1/", {"state": "unset"})
    assert fake_panel.violations
    fake_panel.violations.clear()


def test_entry_time(fake_panel: FakePanel, client: Client) -> None:
    """An entry time only shows in the log; the state stays armed."""
    client.json("PUT", "/system/partitions-1/", {"state": "set"})
    since = fake_panel.now()
    fake_panel.start_entry_time("219")
    status, entries = client.json("GET", f"/logs/?$filter=timestamp%20ge%20{since}")
    assert status == 200
    assert parse_log(entries)[0].text == "Eing gest. Z219"
    assert _state(client) == "set"
    assert client.json("GET", "/alarms/")[1] == []


def test_log_filter(fake_panel: FakePanel, client: Client) -> None:
    """The log filter returns entries at or after the timestamp."""
    newest = max(int(e["events"][0]["timestamp"]) for e in fake_panel.log)
    assert client.json("GET", f"/logs/?$filter=timestamp%20ge%20{newest + 1}") == (
        200,
        [],
    )
    status, entries = client.json("GET", f"/logs/?$filter=timestamp%20ge%20{newest}")
    assert status == 200
    assert entries


def test_error_responses(fake_panel: FakePanel, client: Client) -> None:
    """400, 404 and the installer lock look like the panel's."""
    status, body = client.request("PUT", "/system/partitions-1/", {"state": "x"})
    assert status == 400
    assert b"400 Bad Request" in body
    assert client.request("GET", "/system/partitions-9/")[0] == 404
    assert client.request("GET", "/system/partitions-1/zones-220/")[0] == 404
    fake_panel.installer_locked = True
    assert client.json("GET", "/system/") == (403, {"state": "installer"})


def test_unknown_key_is_ignored(client: Client) -> None:
    """Unknown keys in a PUT body are ignored with 200."""
    status, data = client.json("PUT", "/system/partitions-1/", {"statesss": "set"})
    assert (status, data["state"]) == (200, "unset")


def test_wrong_credentials(fake_panel: FakePanel) -> None:
    """Wrong credentials get 401; repeating them is a violation."""
    client = Client(fake_panel, password="wrong")
    status, body = client.request("GET", "/system/")
    assert status == 401
    assert b"401 Unauthorized" in body
    client.request("GET", "/system/")
    assert fake_panel.violations == ["GET /system/ repeated credentials that got a 401"]
    fake_panel.violations.clear()


def test_undocumented_requests_are_violations(
    fake_panel: FakePanel, client: Client
) -> None:
    """Guessed paths, methods and log filters fail the test."""
    client.request("GET", "/system/partition-1/")
    client.request("POST", "/system/partitions-1/zones-209/", {})
    client.request("GET", "/logs/?$filter=timestamp%20gt%201")
    client.request("GET", "/system/", headers={"Connection": "close"})
    assert len(fake_panel.violations) == 4
    fake_panel.violations.clear()


def test_injected_status(fake_panel: FakePanel, client: Client) -> None:
    """An injected status answers once, then the panel is normal again."""
    fake_panel.inject(Injection("GET", "/faults/", "status", status=503))
    assert client.request("GET", "/faults/")[0] == 503
    assert client.request("GET", "/faults/")[0] == 200


def test_injected_timeout(fake_panel: FakePanel, client: Client) -> None:
    """A timeout leaves the client without an answer."""
    fake_panel.inject(Injection("GET", "/system/", "timeout", delay=1.5))
    conn = client.connect()
    conn.sock.settimeout(0.5)
    conn.request("GET", "/system/", headers={"Authorization": client.auth})
    with pytest.raises(TimeoutError):
        conn.getresponse()
    client.close()
    # the next request isn't counted as parallel to the timed-out one
    assert client.request("GET", "/system/")[0] == 200


def test_dropped_before_command(fake_panel: FakePanel, client: Client) -> None:
    """A connection dropped before the command leaves the state unchanged."""
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "drop_before"))
    with pytest.raises(http.client.RemoteDisconnected):
        client.request("PUT", "/system/partitions-1/", {"state": "set"})
    assert _state(client) == "unset"


def test_dropped_after_command(fake_panel: FakePanel, client: Client) -> None:
    """A connection dropped after the command: the state changed anyway."""
    fake_panel.inject(Injection("PUT", "/system/partitions-1/", "drop_after"))
    with pytest.raises(http.client.RemoteDisconnected):
        client.request("PUT", "/system/partitions-1/", {"state": "set"})
    assert _state(client) == "set"


def test_idle_connection_closed_and_session_resumed(
    fake_panel: FakePanel, client: Client
) -> None:
    """The panel closes idle connections; a reconnect resumes the session."""
    fake_panel.idle_timeout = 0.3
    client.request("GET", "/system/")
    client.request("GET", "/system/")
    assert fake_panel.stats.connections == 1
    time.sleep(0.6)
    with pytest.raises((http.client.RemoteDisconnected, ConnectionError)):
        client.request("GET", "/system/")
    client.request("GET", "/system/")
    assert fake_panel.stats.full_handshakes == 1
    assert fake_panel.stats.resumed_handshakes == 1


def test_parallel_requests_are_violations(fake_panel: FakePanel) -> None:
    """Requests that overlap are detected."""
    fake_panel.log_delay = 0.5
    clients = [Client(fake_panel), Client(fake_panel)]
    threads = [
        threading.Thread(target=c.request, args=("GET", "/logs/")) for c in clients
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert any("parallel request" in v for v in fake_panel.violations)
    assert fake_panel.stats.max_open_connections == 2
    fake_panel.violations.clear()
