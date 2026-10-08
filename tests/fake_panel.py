"""A simulated Secvest panel for tests.

An HTTPS server that behaves like the panel as documented in the
specification (secvest-api): the responses have the format of the fixtures,
and the state rules, error responses and connection behaviour follow what
was observed on the reference panel. Where the specification doesn't know
the panel's answer, the fake records a violation instead of guessing, so a
test fails if the integration sends such a request.

Violations (parallel requests, undocumented requests, a request after a
401, `Connection: close`, commands the official app never sends) fail the
test through the `fake_panel` fixture.
"""

from base64 import b64encode
from collections.abc import Iterator
import contextlib
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
import socket
import socketserver
import ssl
import threading
import time
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).parent / "fixtures"

ARMED = {"set", "partset"}
IN_ALARM = {"set-alarm", "partset-alarm", "unset-alarm"}
REQUESTABLE = {"set", "partset", "unset", "acknowledged"}
PARTITION_STATES = REQUESTABLE | IN_ALARM
# FakePanel.split_partitions(): partition 2's own zones and the shared one
SPLIT_ZONES = ("217", "218")
SHARED_ZONE = "209"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _fixture_json(name: str) -> Any:
    return json.loads(_fixture(name))


@dataclass
class FakeZone:
    """A zone of the simulated panel."""

    id: str
    name: str
    state: str = "closed"
    inner: bool = True
    omittable: bool = True
    omitted: bool = False
    # zone type "Ein/Ausgang": may be open when arming at the keypad. How an
    # open entry/exit door affects arming via the API is simulated as observed
    # on the reference panel, see FakePanel.faults() and FakePanel._arm().
    entry_exit: bool = False

    def to_json(self) -> dict[str, Any]:
        """Render like GET /system/partitions-{n}/zones-{zone}/."""
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state,
            "inner": self.inner,
            "omittable": self.omittable,
            "omitted": self.omitted,
        }


@dataclass
class FakePartition:
    """A partition of the simulated panel."""

    number: int
    name: str
    state: str = "unset"
    zone_ids: list[str] = field(default_factory=list)
    # set up for internal arming (partset); another project saw 409 without
    internal_arming: bool = True

    def to_json(self) -> dict[str, Any]:
        """Render like GET /system/partitions-{n}/."""
        return {
            "id": str(self.number),
            "name": self.name,
            "state": self.state,
            "zones": list(self.zone_ids),
        }


@dataclass
class Injection:
    """An error the next matching request(s) get instead of the normal answer.

    - status: answer with this status and body
    - timeout: hold the request for `delay` seconds, then close without answer
    - drop_before: close the connection without handling the request
    - drop_after: handle the request (a command takes effect), then close
      the connection without an answer
    - slow: answer normally after `delay` seconds
    """

    method: str
    path: str
    action: Literal["status", "timeout", "drop_before", "drop_after", "slow"]
    status: int = 500
    body: bytes = b""
    content_type: str | None = None
    delay: float = 0.0
    times: int = 1


@dataclass
class Stats:
    """What the fake panel observed."""

    connections: int = 0
    max_open_connections: int = 0
    full_handshakes: int = 0
    resumed_handshakes: int = 0
    requests: list[tuple[str, str]] = field(default_factory=list)
    user_agents: list[str | None] = field(default_factory=list)


class FakePanel:
    """The simulated panel with its state, scenario helpers and HTTPS server."""

    def __init__(
        self,
        cert_file: Path,
        key_file: Path,
        *,
        user_code: str = "1234",
        password: str = "secret",
        user_slot: int = 3,
        idle_timeout: float = 1.0,
    ) -> None:
        """Set up the panel from the specification's fixtures."""
        self.user_code = user_code
        self.password = password
        self.user_slot = user_slot
        self.idle_timeout = idle_timeout
        self.installer_locked = False
        # partitions the user may operate; None = all
        self.rights: set[int] | None = None
        # extra faults that aren't zone-open faults (e.g. a weak battery)
        self.static_faults: list[dict[str, Any]] = []
        self.alarms: list[dict[str, Any]] = []
        self.log: list[dict[str, Any]] = _fixture_json("GET_logs.json")
        # seconds between the panel's local wall-clock time and UTC
        self.clock_offset = 0
        self.log_delay = 0.0
        self.injections: list[Injection] = []
        self.violations: list[str] = []
        self.stats = Stats()

        system = _fixture_json("GET_system.json")
        self.name: str = system["name"]
        self.partitions: dict[int, FakePartition] = {
            int(item["id"]): FakePartition(
                number=int(item["id"]),
                name=item["name"],
                zone_ids=list(item["zones"]),
            )
            for item in _fixture_json("GET_system_partitions.json")
        }
        # all zones start closed; the non-omittable ones are the entry doors,
        # as on the reference panel
        self.zones: dict[str, FakeZone] = {
            item["id"]: FakeZone(
                id=item["id"],
                name=item["name"],
                inner=item["inner"],
                omittable=item["omittable"],
                # on the reference panel the non-omittable zones are its
                # entry doors; zone type and omittable are separate installer
                # settings on other panels
                entry_exit=not item["omittable"],
            )
            for item in _fixture_json("GET_system_partitions-1_zones.json")
        }

        self._lock = threading.RLock()
        self._in_flight = 0
        self._open_connections = 0
        self._failed_credentials: str | None = None
        self._cert_file = cert_file
        self._context = self._tls_context(cert_file, key_file)
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _tls_context(cert_file: Path, key_file: Path) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        # the panel speaks TLS 1.2 and issues session tickets
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_file, key_file)
        return context

    def swap_certificate(self, cert_file: Path, key_file: Path) -> None:
        """Present another certificate from the next connection on.

        Like a new certificate of the panel, or someone impersonating it;
        sessions of the old one can't be resumed.
        """
        self._cert_file = cert_file
        self._context = self._tls_context(cert_file, key_file)
        self.close_connections()

    @property
    def fingerprint(self) -> str:
        """SHA-256 fingerprint of the certificate presented, as lowercase hex."""
        der = ssl.PEM_cert_to_DER_cert(self._cert_file.read_text())
        return hashlib.sha256(der).hexdigest()

    # server

    def start(self) -> None:
        """Start serving on a free local port."""
        self._server = _Server(("127.0.0.1", 0), _Handler, self)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="fake-panel",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop serving, close all connections and wait for every thread."""
        if self._server is not None:
            self._server.shutdown()
            self._server.close_connections()
            # joins the connection threads
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()

    @property
    def host(self) -> str:
        """Address of the server."""
        assert self._server is not None
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        """Port of the server."""
        assert self._server is not None
        return int(self._server.server_address[1])

    @property
    def url(self) -> str:
        """Base URL of the server."""
        return f"https://{self.host}:{self.port}"

    def assert_no_violations(self) -> None:
        """Fail if the client broke one of the panel's rules."""
        assert not self.violations, "\n".join(self.violations)

    # scenario helpers

    def open_zone(self, zone_id: str) -> None:
        """Open a zone (door, window)."""
        with self._lock:
            self.zones[zone_id].state = "open"

    def close_zone(self, zone_id: str) -> None:
        """Close a zone."""
        with self._lock:
            self.zones[zone_id].state = "closed"

    def split_partitions(self) -> None:
        """Give partition 2 zones of its own and one shared with partition 1.

        The reference installation has all zones in partition 1. Here 217
        and 218 move to partition 2, and 209 belongs to both; rights per
        partition stay available through `rights`.
        """
        with self._lock:
            first, second = self.partitions[1], self.partitions[2]
            first.zone_ids = [z for z in first.zone_ids if z not in SPLIT_ZONES]
            second.zone_ids = [SHARED_ZONE, *SPLIT_ZONES]

    def trigger_alarm(
        self, partition: int, zone_id: str, alarm_type: str = "4"
    ) -> None:
        """Raise an alarm in a partition, as the firmware reports it."""
        with self._lock:
            part = self.partitions[partition]
            part.state = f"{part.state}-alarm"
            self.alarms.append(
                {
                    "type": alarm_type,
                    "id": str(len(self.alarms) + 1),
                    "ui-string": f"Einbruch Z{zone_id} Alarm",
                    "affects-partition": [str(partition)],
                    "affects-zone": zone_id,
                    "prevents-set": False,
                    "prevents-reset": False,
                    "is-rf-warning": False,
                }
            )
            self.add_log_entry(f"Einbruch Z{zone_id} Alarm", "alarm", zone=zone_id)

    def start_entry_time(self, zone_id: str) -> None:
        """Open an entry/exit door while armed; only the log shows it."""
        with self._lock:
            self.zones[zone_id].state = "open"
            self.add_log_entry(f"Eing gest. Z{zone_id}")

    def add_log_entry(
        self,
        text: str,
        log_type: str = "normal",
        **event: str,
    ) -> dict[str, Any]:
        """Write a log entry with the panel's id scheme."""
        with self._lock:
            timestamp = self.now()
            same_second = sum(
                1
                for entry in self.log
                if entry["events"][0]["timestamp"] == str(timestamp)
            )
            entry = {
                "id": str((timestamp << 8) | same_second),
                "type": log_type,
                "desc": text,
                "events": [{"timestamp": str(timestamp), **event}],
            }
            self.log.insert(0, entry)
            del self.log[600:]
            return entry

    def close_connections(self) -> None:
        """Close the open connections, as the panel does after idling."""
        if self._server is not None:
            self._server.close_connections()

    def inject(self, injection: Injection) -> None:
        """Answer the next matching request(s) with an error."""
        with self._lock:
            self.injections.append(injection)

    def now(self) -> int:
        """Return the panel's local wall-clock time, encoded as Unix seconds."""
        return int(time.time()) + self.clock_offset

    # state rules

    def faults(self, requested: str | None = None) -> list[dict[str, Any]]:
        """Return the current faults; for a requested partset also open entry doors."""
        faults = list(self.static_faults)
        for zone in self.zones.values():
            if zone.state != "open" or zone.omitted:
                continue
            # Configuration-dependent, observed on the reference panel only:
            # an open entry/exit door is no fault, except that it blocks
            # partset with 409. There internal arming is "immediate" (no exit
            # time) and entry/exit doors stay entry/exit when armed
            # internally ("Ein/Aus bei Intern als"). Other exit modes or
            # settings may behave differently.
            if zone.entry_exit and requested != "partset":
                continue
            partitions = [
                str(p.number) for p in self.partitions.values() if zone.id in p.zone_ids
            ]
            faults.append(
                {
                    "type": "5000",
                    "id": str(1305 + int(zone.id)),
                    "ui-string": f"Z{zone.id} A {zone.name}",
                    "affects-partition": partitions,
                    "affects-zone": zone.id,
                    "prevents-set": True,
                    "prevents-reset": False,
                    "is-rf-warning": False,
                }
            )
        return faults

    def may_operate(self, partition: int) -> bool:
        """Whether the user has rights for the partition."""
        return self.rights is None or partition in self.rights

    def set_partition_state(
        self, part: FakePartition, requested: str
    ) -> tuple[int, Any]:
        """Apply PUT /system/partitions-{n}/ like the panel."""
        if requested in {"set", "partset"}:
            return self._arm(part, requested)
        if requested == "unset":
            return self._disarm(part)
        return self._acknowledge(part)

    def _arm(self, part: FakePartition, requested: str) -> tuple[int, Any]:
        if part.state in IN_ALARM or part.state == "acknowledged":
            self.violations.append(
                f"{requested} sent to partition {part.number} in state "
                f"{part.state}; the app never does this"
            )
            return 200, part.to_json()
        if part.state in ARMED:
            # switching between armed modes is ignored
            return 200, part.to_json()
        if not part.zone_ids:
            return 409, []
        if requested == "partset" and not part.internal_arming:
            return 409, []
        blocking = [
            fault
            for fault in self.faults(requested)
            if fault["prevents-set"] and str(part.number) in fault["affects-partition"]
        ]
        if blocking:
            # blocking faults are evaluated before the rights (observed)
            return 409, blocking
        if any(
            self.zones[zone_id].entry_exit and self.zones[zone_id].state == "open"
            for zone_id in part.zone_ids
        ):
            # Configuration-dependent, observed on the reference panel only
            # (full arming "delayed" with 15 s exit time): an open entry/exit
            # door makes set a silent no-op, 200 with the old state. Other
            # exit modes may arm, refuse with 409 or behave otherwise.
            # Checked before the rights, like the blocking faults: inferred
            # from the observed order (open zones before rights), not tested
            # with an open entry/exit door itself.
            return 200, part.to_json()
        if not self.may_operate(part.number):
            return 403, None
        part.state = requested
        user = f"{self.user_slot:03d}"
        index = str(part.number - 1)
        for zone_id in part.zone_ids:
            if self.zones[zone_id].omitted:
                self.add_log_entry(
                    f"Ben{user} Zone{zone_id} ausgeblendet", zone=zone_id
                )
        self.add_log_entry(f"Ben{user}TB {part.number} Übergehen", partition=index)
        text = (
            f"Ben {user} TB {part.number} aktiv"
            if requested == "set"
            else f"Ben{user} TB {part.number} intern aktiv"
        )
        self.add_log_entry(
            text,
            partition=index,
            user=str(self.user_slot),
            username="User",
        )
        return 200, part.to_json()

    def _disarm(self, part: FakePartition) -> tuple[int, Any]:
        if not self.may_operate(part.number):
            return 403, None
        if part.state in IN_ALARM:
            self.violations.append(
                f"unset sent to partition {part.number} in state {part.state}; "
                "acknowledge first"
            )
            return 200, part.to_json()
        if part.state == "unset":
            return 200, part.to_json()
        was_armed = part.state in ARMED
        part.state = "unset"
        self.alarms = [
            alarm
            for alarm in self.alarms
            if str(part.number) not in alarm["affects-partition"]
        ]
        user = f"{self.user_slot:03d}"
        self.add_log_entry(
            f"Ben {user} TB {part.number} deaktiv",
            partition=str(part.number - 1),
            user=str(self.user_slot),
            username="User",
        )
        if was_armed:
            # the omission lasts one arming cycle
            for zone_id in part.zone_ids:
                zone = self.zones[zone_id]
                if zone.omitted:
                    zone.omitted = False
                    self.add_log_entry(
                        f"Ben053 Zone{zone_id} ausgeblendet ok ", zone=zone_id
                    )
        return 200, part.to_json()

    def _acknowledge(self, part: FakePartition) -> tuple[int, Any]:
        if part.state not in IN_ALARM:
            self.violations.append(
                f"acknowledged sent to partition {part.number} in state "
                f"{part.state}; the app only acknowledges during an alarm"
            )
            return 200, part.to_json()
        part.state = "acknowledged"
        return 200, part.to_json()

    def set_zone_omitted(
        self, part: FakePartition, zone: FakeZone, omitted: bool
    ) -> tuple[int, Any]:
        """Apply PUT /system/partitions-{n}/zones-{zone}/ like the panel."""
        if not zone.omittable or not self.may_operate(part.number):
            return 403, None
        zone.omitted = omitted
        return 200, zone.to_json()


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        panel: FakePanel,
    ) -> None:
        self.panel = panel
        self._sockets: set[socket.socket] = set()
        super().__init__(address, handler)

    def get_request(self) -> tuple[socket.socket, Any]:
        sock, address = self.socket.accept()
        tls = self.panel._context.wrap_socket(
            sock, server_side=True, do_handshake_on_connect=False
        )
        self._sockets.add(tls)
        return tls, address

    def shutdown_request(self, request: Any) -> None:
        self._sockets.discard(request)
        super().shutdown_request(request)

    def close_connections(self) -> None:
        for sock in list(self._sockets):
            # shutdown wakes a connection thread blocked in a read
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: _Server

    def setup(self) -> None:
        self.panel = self.server.panel
        super().setup()
        conn = self.connection
        assert isinstance(conn, ssl.SSLSocket)
        conn.settimeout(10)
        try:
            conn.do_handshake()
        except OSError, ssl.SSLError:
            self.close_connection = True
            self._handshake_failed = True
            return
        self._handshake_failed = False
        conn.settimeout(self.panel.idle_timeout)
        with self.panel._lock:
            stats = self.panel.stats
            stats.connections += 1
            if conn.session_reused:
                stats.resumed_handshakes += 1
            else:
                stats.full_handshakes += 1
            self.panel._open_connections += 1
            stats.max_open_connections = max(
                stats.max_open_connections, self.panel._open_connections
            )

    def handle(self) -> None:
        if self._handshake_failed:
            return
        with contextlib.suppress(OSError, ssl.SSLError):
            super().handle()

    def finish(self) -> None:
        if not getattr(self, "_handshake_failed", True):
            with self.panel._lock:
                self.panel._open_connections -= 1
        with contextlib.suppress(OSError, ssl.SSLError):
            super().finish()
        if not getattr(self, "_handshake_failed", True):
            # close cleanly (close_notify), so the client may resume the
            # session, as with the panel
            conn = self.connection
            assert isinstance(conn, ssl.SSLSocket)
            with contextlib.suppress(OSError, ssl.SSLError, ValueError):
                conn.settimeout(0.2)
                conn.unwrap()

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the test output quiet."""

    def do_GET(self) -> None:
        self._request("GET")

    def do_PUT(self) -> None:
        self._request("PUT")

    def do_POST(self) -> None:
        self._request("POST")

    def do_DELETE(self) -> None:
        self._request("DELETE")

    # request handling

    def _request(self, method: str) -> None:
        panel = self.panel
        with panel._lock:
            panel._in_flight += 1
            self._counted = True
            if panel._in_flight > 1:
                panel.violations.append(
                    f"parallel request: {method} {self.path} while another "
                    "request was in progress"
                )
        try:
            self._handle(method)
        finally:
            self._leave()

    def _leave(self) -> None:
        with self.panel._lock:
            if self._counted:
                self.panel._in_flight -= 1
                self._counted = False

    def _handle(self, method: str) -> None:
        panel = self.panel
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with panel._lock:
            panel.stats.requests.append((method, self.path))
            panel.stats.user_agents.append(self.headers.get("User-Agent"))
            if self.headers.get("Connection", "").lower() == "close":
                panel.violations.append(f"Connection: close on {method} {self.path}")
            injection = self._take_injection(method)
        if injection is not None and injection.action == "slow":
            time.sleep(injection.delay)
            injection = None
        if injection is not None and injection.action != "drop_after":
            self._inject(injection)
            return
        status, payload = self._answer(method, body)
        if injection is not None:
            # the command took effect, but the answer never arrives
            self.close_connection = True
            self.connection.close()
            return
        self._send(status, payload)

    def _take_injection(self, method: str) -> Injection | None:
        for injection in self.panel.injections:
            if injection.method == method and re.fullmatch(
                injection.path, urlsplit(self.path).path
            ):
                injection.times -= 1
                if injection.times <= 0:
                    self.panel.injections.remove(injection)
                return injection
        return None

    def _inject(self, injection: Injection) -> None:
        if injection.action == "status":
            self._send_raw(injection.status, injection.body, injection.content_type)
            return
        if injection.action == "timeout":
            # the client gives up waiting, so the request no longer counts
            # towards parallel requests while the panel stays silent
            self._leave()
            time.sleep(injection.delay)
        self.close_connection = True
        self.connection.close()

    def _answer(self, method: str, body: bytes) -> tuple[int, Any]:
        panel = self.panel
        expected = "Basic " + b64encode(
            f"{panel.user_code}:{panel.password}".encode()
        ).decode("ascii")
        authorization = self.headers.get("Authorization")
        with panel._lock:
            if authorization != expected:
                if (
                    panel._failed_credentials is not None
                    and authorization == panel._failed_credentials
                ):
                    panel.violations.append(
                        f"{method} {self.path} repeated credentials that got a 401"
                    )
                panel._failed_credentials = authorization
                return 401, _fixture("GET_system.401.html")
            if panel.installer_locked:
                return 403, _fixture_json("GET_system.403.json")
            return self._route(method, body)

    def _route(self, method: str, body: bytes) -> tuple[int, Any]:
        panel = self.panel
        url = urlsplit(self.path)
        path = url.path
        if method == "GET":
            if path == "/system/":
                return 200, {
                    "name": panel.name,
                    "partitions": [str(n) for n in panel.partitions],
                }
            if path == "/system/partitions/":
                return 200, [p.to_json() for p in panel.partitions.values()]
            if path == "/alarms/":
                return 200, panel.alarms
            if path == "/faults/":
                return 200, panel.faults()
            if path == "/logs/":
                return self._log(url.query)
            if path in {"/outputs/", "/cameras/"}:
                return 200, _fixture_json(f"GET_{path.strip('/')}.json")
        if match := re.fullmatch(r"/system/partitions-(\d+)/", path):
            return self._partition(method, int(match[1]), body)
        if match := re.fullmatch(r"/system/partitions-(\d+)/zones/", path):
            part = panel.partitions.get(int(match[1]))
            if method == "GET":
                if part is None:
                    return 404, _fixture("GET_system_partitions-9.404.html")
                return 200, [panel.zones[z].to_json() for z in part.zone_ids]
        if match := re.fullmatch(r"/system/partitions-(\d+)/zones-(\d+)/", path):
            return self._zone(method, int(match[1]), match[2], body)
        panel.violations.append(f"undocumented request: {method} {self.path}")
        if method in {"GET", "PUT"}:
            return 404, _fixture("GET_unknown-path.404.html")
        return 403, None

    def _partition(self, method: str, number: int, body: bytes) -> tuple[int, Any]:
        panel = self.panel
        part = panel.partitions.get(number)
        if part is None:
            return 404, _fixture("GET_system_partitions-9.404.html")
        if method == "GET":
            return 200, part.to_json()
        if method != "PUT":
            panel.violations.append(f"undocumented request: {method} {self.path}")
            return 403, None
        data = _json_object(body)
        if data is None:
            return 400, _fixture("PUT_system_partitions-1.400.html")
        if "state" not in data:
            # unknown keys are ignored
            return 200, part.to_json()
        requested = data["state"]
        if requested not in PARTITION_STATES:
            return 400, _fixture("PUT_system_partitions-1.400.html")
        if requested not in REQUESTABLE:
            panel.violations.append(
                f"state {requested!r} sent to partition {number}; the app never "
                "sends alarm states"
            )
            return 200, part.to_json()
        return panel.set_partition_state(part, requested)

    def _zone(
        self, method: str, number: int, zone_id: str, body: bytes
    ) -> tuple[int, Any]:
        panel = self.panel
        part = panel.partitions.get(number)
        zone = panel.zones.get(zone_id)
        if part is None or zone is None or zone_id not in part.zone_ids:
            return 404, _fixture("GET_system_partitions-1_zones-220.404.html")
        if method == "GET":
            return 200, zone.to_json()
        if method != "PUT":
            panel.violations.append(f"undocumented request: {method} {self.path}")
            return 403, None
        data = _json_object(body)
        if data is None:
            return 400, _fixture("PUT_system_partitions-1_zones-209.400.html")
        if "omitted" not in data:
            return 200, zone.to_json()
        if data["omitted"] not in {"true", "false"}:
            # the app sends a string; anything else is a guessed request
            return 400, _fixture("PUT_system_partitions-1_zones-209.400.html")
        return panel.set_zone_omitted(part, zone, data["omitted"] == "true")

    def _log(self, query: str) -> tuple[int, Any]:
        panel = self.panel
        if panel.log_delay:
            time.sleep(panel.log_delay)
        params = parse_qs(query)
        entries = panel.log
        if "$filter" in params:
            match = re.fullmatch(r"timestamp ge (\d+)", params["$filter"][0])
            if match is None or len(params) > 1:
                panel.violations.append(f"undocumented log filter: {query}")
                return 200, []
            since = int(match[1])
            entries = [
                entry
                for entry in entries
                if int(entry["events"][0]["timestamp"]) >= since
            ]
        elif params:
            panel.violations.append(f"undocumented log query: {query}")
        return 200, entries

    # responses

    def _send(self, status: int, payload: Any) -> None:
        if isinstance(payload, bytes):
            # HTML error pages come without a Content-Type, like the panel's
            self._send_raw(status, payload, None)
        elif payload is None:
            self._send_raw(status, b"", "application/json")
        else:
            text = json.dumps(payload, indent=2, ensure_ascii=False)
            self._send_chunked(status, text.replace("\n", "\r\n").encode())

    def _send_raw(self, status: int, body: bytes, content_type: str | None) -> None:
        self.send_response_only(status)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_chunked(self, status: int, body: bytes) -> None:
        self.send_response_only(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache, no-store, max-age=0")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for chunk in _chunks(body, 512):
            self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")


def _chunks(data: bytes, size: int) -> Iterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


def _json_object(body: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None
