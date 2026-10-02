#!/usr/bin/env python3
"""End-to-end smoke test for BotGate.

Runs botgate_proxy.py as a real subprocess against a throwaway backend
and drives a handful of connections through it over real sockets, so
the parts that differ per platform -- socket setup, thread startup,
path resolution, the Windows console call -- are actually executed
rather than assumed.

Pure standard library, same as BotGate itself. Usage:

    python3 tests/smoke_test.py

Exits 0 if every scenario passes, 1 otherwise.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY_SRC = os.path.join(REPO_ROOT, "botgate_proxy.py")

ESC = b"\x1b"
IAC = 0xFF

GATE_TIMEOUT = 3.0          # timeout_seconds given to the proxy under test
START_TIMEOUT = 15.0        # how long to wait for the listener to come up


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def strip_telnet(data):
    """Remove IAC sequences so a payload check isn't confused by the
    negotiation BotGate sends to the backend on connect."""
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == IAC:
            i += 3 if i + 2 < len(data) else len(data)
            continue
        out.append(data[i])
        i += 1
    return bytes(out)


class Backend:
    """Stand-in for the real BBS: accepts one connection at a time and
    records what it receives."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]

    def accept(self, timeout=10.0):
        self.sock.settimeout(timeout)
        conn, _ = self.sock.accept()
        return conn

    def close(self):
        self.sock.close()


class Proxy:
    """A botgate_proxy.py subprocess running from its own scratch
    directory, so the shipped config and can/ files are never touched
    (CONFIG_FILE and the relative paths resolve next to the script)."""

    def __init__(self, backend_port, can_files=None):
        self.dir = tempfile.mkdtemp(prefix="botgate-smoke-")
        self.port = free_port()
        shutil.copy(PROXY_SRC, os.path.join(self.dir, "botgate_proxy.py"))

        can_dir = os.path.join(self.dir, "can")
        os.makedirs(can_dir)
        os.makedirs(os.path.join(self.dir, "geo"))
        for name, body in (can_files or {}).items():
            with open(os.path.join(can_dir, name), "w") as f:
                f.write(body)

        lines = [
            "[proxy]",
            f"listen_port = {self.port}",
            "backend_host = 127.0.0.1",
            f"backend_port = {backend_port}",
            f"timeout_seconds = {GATE_TIMEOUT}",
            "required_hits = 2",
            "live_countdown = yes",
            "prompt_file =",
            "banner_file =",
            "log_file = botgate_proxy.log",
            "log_level = DEBUG",
            "ip_cap = 4",
            "can_dir = can",
            "geo_dir = geo",
            "dns_lookup_enabled = no",
            "max_connections = 10",
        ]
        with open(os.path.join(self.dir, "botgate_proxy.cfg"), "w") as f:
            f.write("\n".join(lines) + "\n")

        self.proc = subprocess.Popen(
            [sys.executable, "botgate_proxy.py"],
            cwd=self.dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        self._wait_until_listening()

    def _wait_until_listening(self):
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"proxy exited early (rc={self.proc.returncode}):\n"
                    f"{self.proc.stdout.read().decode('utf-8', 'replace')}"
                )
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=1)
            except OSError:
                time.sleep(0.1)
                continue
            s.close()
            return
        raise RuntimeError("proxy never started listening")

    def connect(self, timeout=10.0):
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout)
        s.settimeout(timeout)
        return s

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.proc.stdout.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def recv_until(sock, needle, timeout=10.0, transform=None):
    """Read until needle shows up, the peer closes, or timeout. The
    optional transform is applied before searching, so the backend side
    can look past BotGate's telnet negotiation."""
    seen = lambda buf: buf if transform is None else transform(buf)
    buf = b""
    deadline = time.monotonic() + timeout
    while needle not in seen(buf):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        sock.settimeout(remaining)
        try:
            chunk = sock.recv(4096)
        except OSError:
            break
        if not chunk:
            break                      # peer closed; no point spinning
        buf += chunk
    return seen(buf)


def assert_no_backend_connection(backend):
    """The backend must not have been dialed at all."""
    try:
        conn = backend.accept(timeout=1)
    except OSError:
        return                         # nothing queued, which is the point
    conn.close()
    raise AssertionError("backend was connected despite a failed gate")


def read_until_closed(sock, limit=8192):
    """Read until the peer closes or we time out. Returns what arrived
    and whether the close actually happened."""
    chunks = []
    try:
        while sum(len(c) for c in chunks) < limit:
            data = sock.recv(4096)
            if not data:
                return b"".join(chunks), True
            chunks.append(data)
    except OSError:
        pass
    return b"".join(chunks), False


# --- scenarios ------------------------------------------------------


def test_gate_pass_and_relay(proxy, backend):
    """ESC twice passes the gate, and bytes then flow both ways."""
    client = proxy.connect()
    client.recv(4096)                  # telnet negotiation + prompt
    client.sendall(ESC + ESC)

    conn = backend.accept()
    client.sendall(b"hello-from-caller")
    got = recv_until(conn, b"hello-from-caller", transform=strip_telnet)
    assert b"hello-from-caller" in got, "backend never saw caller data"

    conn.sendall(b"hello-from-backend")
    back = recv_until(client, b"hello-from-backend")
    assert b"hello-from-backend" in back, "caller never saw backend data"

    conn.close()
    client.close()


def test_gate_timeout(proxy, backend):
    """A caller that never presses anything is dropped, and no backend
    connection is made."""
    client = proxy.connect(timeout=GATE_TIMEOUT + 10)
    start = time.monotonic()
    _, closed = read_until_closed(client)
    elapsed = time.monotonic() - start
    client.close()

    assert closed, "proxy did not close the connection after the timeout"
    assert elapsed >= GATE_TIMEOUT - 1, f"closed too early ({elapsed:.1f}s)"
    assert elapsed < GATE_TIMEOUT + 10, f"closed too late ({elapsed:.1f}s)"
    assert_no_backend_connection(backend)


def test_scripted_payload_rejected(proxy):
    """A large mixed chunk (an HTTP request, say) fails the gate
    immediately rather than idling out the full timeout."""
    client = proxy.connect(timeout=GATE_TIMEOUT + 10)
    client.recv(4096)
    client.sendall(b"GET / HTTP/1.1\r\nAccept: */*\r\n\r\n")

    start = time.monotonic()
    _, closed = read_until_closed(client)
    elapsed = time.monotonic() - start
    client.close()

    assert closed, "proxy did not close on a scripted payload"
    assert elapsed < GATE_TIMEOUT, (
        f"scripted payload idled out the full timeout ({elapsed:.1f}s) "
        f"instead of failing fast"
    )


def test_ip_can_block(backend):
    """An IP in ip.can is dropped before the gate is ever drawn."""
    proxy = Proxy(backend.port, can_files={"ip.can": "127.0.0.1\n"})
    try:
        client = proxy.connect()
        data, closed = read_until_closed(client)
        client.close()
        assert closed, "blocked IP was not disconnected"
        assert data == b"", f"blocked IP still got sent {data!r}"
    finally:
        proxy.stop()


def main():
    results = []

    def run(fn, *args):
        try:
            fn(*args)
            results.append((fn.__name__, None))
        except Exception as e:
            results.append((fn.__name__, e))

    backend = Backend()
    proxy = Proxy(backend.port)
    try:
        run(test_gate_pass_and_relay, proxy, backend)
        run(test_gate_timeout, proxy, backend)
        run(test_scripted_payload_rejected, proxy)
    finally:
        proxy.stop()

    try:
        run(test_ip_can_block, backend)
    finally:
        backend.close()

    failed = 0
    for name, err in results:
        if err is None:
            print(f"ok   {name}")
        else:
            failed += 1
            print(f"FAIL {name}: {err}")

    print(f"\n{len(results) - failed}/{len(results)} passed "
          f"(python {sys.version.split()[0]} on {sys.platform})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
