#!/usr/bin/env python3
"""
botgate_proxy.py

A standalone TCP bot-gate that sits in front of a BBS's real inbound
port (in this setup: NetSerial's telnet listener for a Spitfire node,
inside a VirtualBox VM). The router's port-forward should point here
instead of straight at the BBS; this proxy only opens a connection to
the real backend after a caller passes the gate.

Why this exists: Spitfire (SPITFIRE.EXE) unconditionally redoes its
own full modem answer/reset sequence on every launch, discarding any
existing connection state -- so a gate running in DOS ahead of it (or
any hook inside NetSerial, which has none anyway) can't hand off a
still-alive call. Running the gate here, purely at the TCP level,
completely sidesteps both: nothing downstream (NetSerial, the DOS VM,
Spitfire) ever sees a connection until it's already passed.

Gate behavior: caller must send ESC (0x1B) and/or '*' (0x2A), in any
combination, twice within TIMEOUT_SECONDS. Pass -> connect through to
the real backend and relay bytes transparently in both directions.
Fail -> close the connection, no backend connection is ever made.

No third-party dependencies -- standard library only.

Usage:
    python3 botgate_proxy.py

Configuration is read from botgate_proxy.cfg in the same directory
(created with defaults on first run if missing). Same key=value,
;-comment style as BOTGATE.CFG on the DOS side, for consistency.
"""

import array
import bisect
import configparser
import fnmatch
import ipaddress
import logging
import os
import re
import select
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "botgate_proxy.cfg")

DEFAULT_CONFIG = """; botgate_proxy.cfg
; TCP-level bot gate, sits in front of the real BBS port.

[proxy]
; Port this proxy listens on -- point your router's port-forward here.
; Keep this as your existing public-facing port (e.g. 23230) so
; callers' existing phonebook entries don't need to change.
listen_port = 23230

; Where to relay to once a caller passes the gate -- NetSerial's
; inbound listener, reconfigured to a different internal port
; (e.g. 2323) so it's no longer directly reachable from outside.
backend_host = 192.168.1.XXX
backend_port = 2323

; Seconds to wait for the caller to press ESC or * twice.
timeout_seconds = 20

; Number of ESC/* presses required (in any combination).
required_hits = 2

; Optional: path to an ANSI/ASCII file to display instead of the
; built-in plain-text prompt. Sent as raw bytes, so CP437/ANSI art
; works as-is. Leave blank to use the built-in message. Re-read from
; disk on every connection, so you can edit it without restarting.
prompt_file =

; Whether a '#' placeholder (see prompt_file docs) live-updates once a
; second as a real countdown, or just shows the starting number once
; and stays static. Live updates work great on real terminal software
; (SyncTERM, NetRunner, etc.) but some web-based telnet clients (e.g.
; fTelnet) don't handle the repeated cursor-positioning correctly and
; show garbled text. Set to no if a meaningful chunk of your callers
; use a web-based client and you'd rather have a clean static prompt
; than a glitchy countdown. Yes by default.
live_countdown = yes

; Log file path. Blank disables file logging (console only).
log_file = botgate_proxy.log

; DEBUG also logs the raw bytes received during the gate phase (hex,
; truncated) -- useful for seeing what bots are actually sending.
; INFO logs connection/pass/fail/handoff events only.
log_level = INFO

; Max simultaneous connections allowed from the same source IP.
; Anyone already at the cap gets an instant drop -- no gate prompt,
; no backend connection attempt. 0 disables the cap (unlimited).
ip_cap = 2

; Directory holding Synchronet-style .can blocklist files:
;   ipfilter_exempt.cfg  - always allowed, skips every other check
;   ip.can               - blocked IPs/CIDR/wildcards, logged
;   ip-silent.can        - blocked IPs/CIDR/wildcards, NOT logged
;   host.can             - blocked hostname patterns (needs reverse
;                          DNS -- see dns_lookup_enabled below)
; Same one-pattern-per-line format as Synchronet: plain IP, CIDR
; (e.g. 192.168.1.0/24), wildcard (*.example.com), or !negated to
; carve out an exception within the same file.
can_dir = can

; Directory of Apache .htaccess-style "deny from x.x.x.x/nn" files
; (e.g. from https://www.ip2location.com/free/visitor-blocker).
; Every *.txt file found here is loaded automatically at startup --
; no need to list them individually. Add/remove/update files here,
; then restart the proxy to pick up changes.
geo_dir = geo

; Whether to do a reverse-DNS lookup on each connecting IP to check
; against host.can. 2-second timeout, fails open (treated as no
; match) if the lookup doesn't come back in time or there's no PTR
; record -- never blocks a legitimate caller just for having no
; reverse DNS. Set to no to skip host.can checks entirely.
dns_lookup_enabled = yes

; Auto temp-ban: an IP that makes rate_limit_hits connection attempts
; within rate_limit_window_seconds gets automatically added to
; temp_ip.can (in can_dir) for rate_limit_ban_minutes. Catches rapid
; repeated connects/reconnects that ip_cap alone won't (ip_cap only
; limits *simultaneous* connections, not connection rate over time).
; Exempt IPs are never rate-limited or temp-banned. Set rate_limit_hits
; to 0 to disable this feature entirely.
rate_limit_hits = 20
rate_limit_window_seconds = 10
rate_limit_ban_minutes = 90

; Optional: path to an ANSI/ASCII file to print to the local console
; on startup, purely cosmetic. Only shown when actually running in an
; interactive terminal (never under systemd, or when output is
; redirected/logged) -- raw ANSI escape codes have no place cluttering
; a structured log. Leave blank to disable.
banner_file =

; Advanced / Synchronet-specific: when enabled, sends a PROXY protocol
; v1 header (the real caller's IP) as the very first bytes of the
; backend connection, before telnet negotiation. This lets a backend
; that understands PROXY protocol (e.g. Synchronet with HAPROXY_PROTO
; enabled in sbbs.ini) see the real caller's IP for its own .can
; files, hack-attempt tracking, and logs, instead of seeing every
; connection as coming from wherever BotGate's backend_host is.
;
; Confirmed working against real Synchronet (v3.22): with HAPROXY_PROTO
; added to [BBS] Options in sbbs.ini and this setting enabled, a test
; caller's real LAN IP and hostname both showed up correctly on
; Synchronet's own login screen instead of BotGate's address. Per
; Synchronet's own docs (wiki.synchro.net/howto:haproxy), both v1 and
; v2 of the protocol are supported -- BotGate sends v1. Requires a
; Synchronet build from after Nov 22, 2020.
;
; DO NOT enable this unless your backend is specifically configured
; to expect it -- sending this header to a backend that isn't
; expecting it (Spitfire/NetSerial, Mystic, or Synchronet without
; HAPROXY_PROTO set) will break every connection, since the header
; text would just be seen as garbled login data. Off by default;
; only turn this on if you know your backend supports it. Also note:
; enabling HAPROXY_PROTO on the Synchronet side blocks ALL direct
; connections to its BBS ports -- every connection must come through
; BotGate (or another PROXY-protocol-aware front end) from that point on.
send_proxy_protocol = no
"""

TRIGGER_BYTES = (0x1B, 0x2A)  # ESC, '*'

# Telnet protocol constants (same approach used by the ANetBBS
# selector's telnet front-end, credit to that design)
IAC  = 0xFF
DONT = 0xFE
DO   = 0xFD
WONT = 0xFC
WILL = 0xFB
SB   = 0xFA
SE   = 0xF0
OPT_BINARY = 0x00
OPT_ECHO   = 0x01
OPT_SGA    = 0x03


def send_initial_telnet_options(sock):
    """Negotiate 8-bit binary in both directions, suppress GA, server
    echoes. Needed so 0xFF bytes in ANSI/CP437 (and later ZMODEM)
    pass through cleanly instead of being mistaken for IAC framing."""
    try:
        sock.sendall(bytes([
            IAC, WILL, OPT_BINARY,
            IAC, DO,   OPT_BINARY,
            IAC, WILL, OPT_SGA,
            IAC, DO,   OPT_SGA,
            IAC, WILL, OPT_ECHO,
        ]))
    except OSError:
        pass


def build_proxy_protocol_header(client_addr, backend_sock):
    """Builds a PROXY protocol v1 header announcing the real caller's
    address to a backend that understands it (e.g. Synchronet with
    HAPROXY_PROTO enabled). Sent as the very first bytes of the
    backend connection, before any telnet negotiation. The
    destination address is read back from the actual connected
    socket (getpeername()) rather than the configured backend_host,
    so this is correct even if backend_host was given as a hostname
    rather than a literal IP -- PROXY protocol v1 requires a real
    dotted-decimal address for both ends."""
    src_ip, src_port = client_addr[0], client_addr[1]
    dst_ip, dst_port = backend_sock.getpeername()[:2]
    return f"PROXY TCP4 {src_ip} {dst_ip} {src_port} {dst_port}\r\n".encode("ascii")


class TelnetFilter:
    """Incrementally strips Telnet IAC negotiation/subnegotiation
    sequences out of a byte stream, so the gate only ever sees real
    data bytes. State persists across chunks in case a sequence is
    split across separate recv() calls."""

    def __init__(self):
        self._state = "data"  # data, iac, opt, sb, sb_iac

    def feed(self, chunk):
        out = bytearray()
        for byte in chunk:
            if self._state == "data":
                if byte == IAC:
                    self._state = "iac"
                else:
                    out.append(byte)
            elif self._state == "iac":
                if byte == IAC:
                    out.append(byte)  # escaped literal 0xFF data byte
                    self._state = "data"
                elif byte in (WILL, WONT, DO, DONT):
                    self._state = "opt"
                elif byte == SB:
                    self._state = "sb"
                else:
                    self._state = "data"  # NOP/AYT/etc, single-byte command
            elif self._state == "opt":
                self._state = "data"  # consumed the option byte
            elif self._state == "sb":
                if byte == IAC:
                    self._state = "sb_iac"
                # else still inside subnegotiation, discard
            elif self._state == "sb_iac":
                if byte == SE:
                    self._state = "data"
                else:
                    self._state = "sb"  # false alarm, back to subneg
        return bytes(out)


# ============================================================
# Blocklists: Synchronet-style .can pattern files + IP2Location-
# style geo .htaccess deny lists + reverse-DNS host matching.
# All loaded once at startup (restart to pick up file changes).
# ============================================================

class Pattern:
    """A single line from a .can-style file: a plain IP/hostname, a
    CIDR range, a '*' wildcard, or any of those prefixed with '!' to
    negate (carve an exception out of the rest of the file)."""

    __slots__ = ("negate", "kind", "network", "regex", "literal")

    def __init__(self, raw):
        raw = raw.strip()
        self.negate = raw.startswith("!")
        if self.negate:
            raw = raw[1:].strip()

        self.network = None
        self.regex = None
        self.literal = None

        if "/" in raw and raw.split("/")[0].count(".") == 3:
            try:
                self.network = ipaddress.ip_network(raw, strict=False)
                self.kind = "cidr"
                return
            except ValueError:
                pass  # fall through -- doesn't actually parse as CIDR

        if "*" in raw or "?" in raw:
            self.kind = "wildcard"
            self.regex = re.compile(fnmatch.translate(raw), re.IGNORECASE)
        else:
            self.kind = "literal"
            self.literal = raw.lower()

    def matches(self, value):
        if self.kind == "cidr":
            try:
                return ipaddress.ip_address(value) in self.network
            except ValueError:
                return False
        elif self.kind == "wildcard":
            return bool(self.regex.match(value))
        else:
            return value.lower() == self.literal


class PatternList:
    """A loaded .can-style file. A '!' entry anywhere in the file
    clears a match even if an earlier pattern in the same file
    matched -- lets a file block a wide pattern while carving out
    specific exceptions."""

    def __init__(self, patterns):
        self.patterns = patterns

    def matches(self, value):
        if not value:
            return False
        matched = False
        for p in self.patterns:
            if p.matches(value):
                if p.negate:
                    return False
                matched = True
        return matched

    @classmethod
    def load(cls, path):
        patterns = []
        try:
            with open(path, "r", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(";") or line.startswith("#"):
                        continue
                    patterns.append(Pattern(line))
        except OSError as e:
            log.warning(f"Could not read {path}: {e} -- treating as empty list.")
        return cls(patterns)


def load_geo_file(path):
    """Parses an Apache .htaccess-style 'deny from x.x.x.x/nn' file
    into two sorted array.array('L') structures (range starts and
    ends) for fast binary-search containment checks. Non-'deny from'
    lines (comments, <Limit>, order/allow directives) are ignored."""
    ranges = []
    try:
        with open(path, "r", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line.lower().startswith("deny from "):
                    continue
                cidr = line[len("deny from "):].strip()
                try:
                    net = ipaddress.ip_network(cidr, strict=False)
                except ValueError:
                    log.warning(f"Skipping malformed entry in {path}: {cidr!r}")
                    continue
                ranges.append((int(net.network_address), int(net.broadcast_address)))
    except OSError as e:
        log.warning(f"Could not read geo file {path}: {e}")

    ranges.sort()
    starts = array.array("L", (r[0] for r in ranges))
    ends = array.array("L", (r[1] for r in ranges))
    return starts, ends


def geo_range_contains(ip_int, starts, ends):
    i = bisect.bisect_right(starts, ip_int) - 1
    return i >= 0 and starts[i] <= ip_int <= ends[i]


class BlockLists:
    """Loads all .can files and geo blocklists once at startup."""

    def __init__(self, cfg):
        can_dir = cfg["can_dir"]
        geo_dir = cfg["geo_dir"]

        self.exempt = PatternList.load(os.path.join(can_dir, "ipfilter_exempt.cfg"))
        self.ip_can = PatternList.load(os.path.join(can_dir, "ip.can"))
        self.ip_silent = PatternList.load(os.path.join(can_dir, "ip-silent.can"))
        self.host_can = PatternList.load(os.path.join(can_dir, "host.can"))

        log.info(f"Loaded {len(self.exempt.patterns)} exempt, "
                 f"{len(self.ip_can.patterns)} ip.can, "
                 f"{len(self.ip_silent.patterns)} ip-silent.can, "
                 f"{len(self.host_can.patterns)} host.can entries from {can_dir}")

        self.geo = {}
        if os.path.isdir(geo_dir):
            for fname in sorted(os.listdir(geo_dir)):
                if fname.lower().endswith(".txt"):
                    starts, ends = load_geo_file(os.path.join(geo_dir, fname))
                    self.geo[fname] = (starts, ends)
                    log.info(f"Loaded geo blocklist {fname}: {len(starts)} ranges")
        else:
            log.warning(f"geo_dir '{geo_dir}' does not exist -- no geo blocklists loaded.")

    def check_geo(self, ip_str):
        try:
            ip_int = int(ipaddress.ip_address(ip_str))
        except ValueError:
            return None
        for fname, (starts, ends) in self.geo.items():
            if geo_range_contains(ip_int, starts, ends):
                return fname
        return None


def reverse_dns_lookup(ip, timeout=2.0):
    """Reverse-DNS an IP with a hard timeout, regardless of what the
    system resolver itself is configured to do. Returns the hostname,
    or None on failure/timeout (fail open -- caller should treat None
    as "no match", never as a reason to block)."""
    result = [None]

    def worker():
        try:
            hostname, _, _ = socket.gethostbyaddr(ip)
            result[0] = hostname
        except (socket.herror, socket.gaierror, OSError):
            result[0] = None

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout)
    return result[0]


TIMESTAMP_FMT = "%Y%m%dT%H%M%S%z"


class RateLimiter:
    """Tracks connection attempts per IP in a sliding window; an IP
    that crosses rate_limit_hits within rate_limit_window_seconds
    gets auto-banned for rate_limit_ban_minutes, persisted to
    temp_ip.can (auto-created if missing) using the same t=/e=
    timestamp convention as real Synchronet ban entries. Bans survive
    a restart (expired ones are dropped on load); the live rate
    window itself does not (that's fine -- it's only ever a few
    seconds wide anyway)."""

    def __init__(self, cfg, can_dir):
        self.hits_threshold = cfg["rate_limit_hits"]
        self.window_seconds = cfg["rate_limit_window_seconds"]
        self.ban_minutes = cfg["rate_limit_ban_minutes"]
        self.path = os.path.join(can_dir, "temp_ip.can")
        self.lock = threading.Lock()
        self.recent = {}   # ip -> [monotonic timestamps within window]
        self.banned = {}   # ip -> expiration datetime (aware, UTC)
        self._load()

    def _parse_line(self, line):
        parts = line.split("\t")
        ip = parts[0].strip()
        expires = None
        reason = "unknown"
        for p in parts[1:]:
            if p.startswith("e="):
                try:
                    expires = datetime.strptime(p[2:], TIMESTAMP_FMT)
                except ValueError:
                    return None
            elif p.startswith("r="):
                reason = p[2:]
        if expires is None:
            return None
        return ip, expires, reason

    def _format_line(self, ip, expires, reason):
        now_str = datetime.now(timezone.utc).strftime(TIMESTAMP_FMT)
        exp_str = expires.strftime(TIMESTAMP_FMT)
        return f"{ip}\tt={now_str}\te={exp_str}\tr={reason}"

    def _load(self):
        if not os.path.exists(self.path):
            try:
                with open(self.path, "w") as f:
                    f.write("; Auto-managed temporary IP bans (rate-limit triggered)\n"
                            "; Entries past their e= expiration are dropped automatically\n"
                            "; on startup -- no need to edit this by hand.\n")
            except OSError as e:
                log.warning(f"Could not create {self.path}: {e}")
            log.info("temp_ip.can not found -- created a new empty one.")
            return

        now = datetime.now(timezone.utc)
        try:
            with open(self.path, "r", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith(";") or line.startswith("#"):
                        continue
                    parsed = self._parse_line(line)
                    if parsed is None:
                        continue
                    ip, expires, reason = parsed
                    if expires > now:
                        self.banned[ip] = (expires, reason)
        except OSError as e:
            log.warning(f"Could not read {self.path}: {e}")

        self._rewrite()  # drop any expired entries from the on-disk file too
        log.info(f"Loaded {len(self.banned)} active temp ban(s) from temp_ip.can")

    def _rewrite(self):
        try:
            with open(self.path, "w") as f:
                f.write("; Auto-managed temporary IP bans (rate-limit triggered)\n"
                        "; Entries past their e= expiration are dropped automatically\n"
                        "; on startup -- no need to edit this by hand.\n")
                for ip, (expires, reason) in self.banned.items():
                    f.write(self._format_line(ip, expires, reason) + "\n")
        except OSError as e:
            log.warning(f"Could not write {self.path}: {e}")

    def is_banned(self, ip):
        with self.lock:
            entry = self.banned.get(ip)
            if entry is None:
                return False
            expires, _reason = entry
            if expires <= datetime.now(timezone.utc):
                del self.banned[ip]
                self._rewrite()
                return False
            return True

    def record_and_check(self, ip):
        """Records this connection attempt. Returns True if this
        attempt just pushed the IP over the threshold (and it has
        now been banned)."""
        if self.hits_threshold <= 0:
            return False

        now = time.monotonic()
        with self.lock:
            window_start = now - self.window_seconds
            attempts = [t for t in self.recent.get(ip, []) if t >= window_start]
            attempts.append(now)

            if len(attempts) >= self.hits_threshold:
                expires = datetime.now(timezone.utc) + timedelta(minutes=self.ban_minutes)
                reason = f"{len(attempts)} hits in {self.window_seconds}s"
                self.banned[ip] = (expires, reason)
                self.recent.pop(ip, None)
                try:
                    with open(self.path, "a") as f:
                        f.write(self._format_line(ip, expires, reason) + "\n")
                except OSError as e:
                    log.warning(f"Could not write {self.path}: {e}")
                return True

            self.recent[ip] = attempts
            return False


def check_access(ip, blocklists, cfg, rate_limiter):
    """Returns (action, reason). action is one of:
       'exempt'        -- always allowed, bypasses every other check
       'block_logged'  -- blocked, log it (ip.can / geo / host.can /
                           temp_ip.can)
       'block_silent'  -- blocked, do not log (ip-silent.can)
       'allow'         -- proceed to the IP cap / gate as normal
    """
    if blocklists.exempt.matches(ip):
        return "exempt", None

    if rate_limiter.is_banned(ip):
        return "block_logged", "temp_ip.can"

    if blocklists.ip_can.matches(ip):
        return "block_logged", "ip.can"

    if blocklists.ip_silent.matches(ip):
        return "block_silent", "ip-silent.can"

    geo_hit = blocklists.check_geo(ip)
    if geo_hit:
        return "block_logged", f"geo/{geo_hit}"

    if cfg["dns_lookup_enabled"]:
        hostname = reverse_dns_lookup(ip, timeout=2.0)
        log.debug(f"{ip} reverse DNS: {hostname!r}")
        if hostname and blocklists.host_can.matches(hostname):
            return "block_logged", f"host.can ({hostname})"

    # Nothing statically blocked this IP -- now record the attempt for
    # rate-limiting purposes. This deliberately happens last: an IP
    # already permanently blocked elsewhere doesn't need this too, and
    # this is meant to catch otherwise-unblocked IPs hammering the
    # port, not pad the count for ones already handled above.
    if rate_limiter.record_and_check(ip):
        log.warning(f"{ip} exceeded rate limit -- temp-banned for "
                    f"{cfg['rate_limit_ban_minutes']:.0f} minute(s).")
        return "block_logged", "temp_ip.can (just triggered)"

    return "allow", None


def load_config():
    if not os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "w") as f:
            f.write(DEFAULT_CONFIG)
        print(f"[botgate_proxy] Wrote default config to {CONFIG_FILE} -- "
              f"edit backend_host/backend_port and rerun.")
        sys.exit(1)

    cfg = configparser.ConfigParser()
    cfg.read(CONFIG_FILE)
    p = cfg["proxy"]

    script_dir = os.path.dirname(os.path.abspath(__file__))

    def resolve_dir(value):
        return value if os.path.isabs(value) else os.path.join(script_dir, value)

    return {
        "listen_port": p.getint("listen_port", 2323),
        "backend_host": p.get("backend_host", "127.0.0.1"),
        "backend_port": p.getint("backend_port", 23),
        "timeout_seconds": p.getfloat("timeout_seconds", 20),
        "required_hits": p.getint("required_hits", 2),
        "prompt_file": p.get("prompt_file", "").strip(),
        "live_countdown": p.getboolean("live_countdown", True),
        "log_file": p.get("log_file", "botgate_proxy.log").strip(),
        "log_level": p.get("log_level", "INFO").strip().upper(),
        "ip_cap": p.getint("ip_cap", 2),
        "can_dir": resolve_dir(p.get("can_dir", "can").strip()),
        "geo_dir": resolve_dir(p.get("geo_dir", "geo").strip()),
        "dns_lookup_enabled": p.getboolean("dns_lookup_enabled", True),
        "rate_limit_hits": p.getint("rate_limit_hits", 20),
        "rate_limit_window_seconds": p.getfloat("rate_limit_window_seconds", 10),
        "rate_limit_ban_minutes": p.getfloat("rate_limit_ban_minutes", 90),
        "banner_file": p.get("banner_file", "").strip(),
        "send_proxy_protocol": p.getboolean("send_proxy_protocol", False),
    }


DEFAULT_PROMPT_TEXT = b"\r\nPress ESC or * twice within ## seconds to continue...\r\n"

PLACEHOLDER_RE = re.compile(rb"#+")

log = logging.getLogger("botgate_proxy")


def setup_logging(log_file, log_level):
    level = getattr(logging, log_level, logging.INFO)
    log.setLevel(level)
    log.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s",
                             datefmt="%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    log.addHandler(console)

    if log_file:
        try:
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(fmt)
            log.addHandler(file_handler)
        except OSError as e:
            log.warning(f"Could not open log file {log_file}: {e} -- console only.")


def get_prompt_template(cfg):
    """Returns the raw, normalized prompt template bytes (CRLF line
    endings), with any '#' countdown placeholder left intact --
    either a configured ANSI/ASCII file (re-read from disk every
    connection, so it can be edited live) or the built-in default."""
    path = cfg.get("prompt_file", "")
    if path:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            # Normalize line endings to CRLF regardless of how the file
            # was authored (bare \n from Linux editors, \r\n, or lone
            # \r). Telnet terminals need the \r to actually return the
            # cursor to column 1 -- \n alone just moves down a row,
            # which is what causes ANSI art to "stair-step" rightward.
            raw = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            return raw.replace(b"\n", b"\r\n")
        except OSError as e:
            log.warning(f"Could not read prompt_file '{path}': {e} -- using default prompt.")

    return DEFAULT_PROMPT_TEXT


def build_prompt(cfg):
    """Returns (initial_bytes, countdown). countdown is None for a
    fully static prompt (no '#' placeholder found -- old behavior,
    unchanged), or (row, col, width) 1-based ANSI coordinates of a
    run of '#' characters, which get live-updated with the
    remaining seconds once a second during the gate. line_count is
    the total number of lines in the template, so callers can
    position follow-up messages safely below the whole display
    instead of wherever the cursor happens to be left."""
    template = get_prompt_template(cfg)
    lines = template.split(b"\r\n")
    line_count = len(lines)

    for row_idx, line in enumerate(lines):
        m = PLACEHOLDER_RE.search(line)
        if m:
            width = m.end() - m.start()
            col = m.start() + 1
            row = row_idx + 1
            start_text = f"{int(cfg['timeout_seconds']):>{width}}".encode("ascii")[-width:]
            lines[row_idx] = line[:m.start()] + start_text + line[m.end():]
            return b"\r\n".join(lines), (row, col, width), line_count

    return template, None, line_count


def run_gate(client_sock, cfg, addr):
    """Returns (passed, end_row). passed is True/False as before.
    end_row is the last line number of the prompt that was actually
    displayed, so a caller can position any follow-up message safely
    below it rather than wherever the countdown left the cursor."""
    timeout_seconds = cfg["timeout_seconds"]
    required_hits = cfg["required_hits"]

    initial_bytes, countdown, end_row = build_prompt(cfg)
    if not cfg.get("live_countdown", True):
        # Sysop opted out of the live per-second updates (e.g. for
        # compatibility with weaker web-based telnet clients that
        # mishandle repeated cursor-positioning sequences). The ##
        # placeholder, if present, still gets substituted with the
        # starting timeout value in build_prompt() above -- this just
        # skips the periodic re-send that follows, leaving it static.
        countdown = None
    try:
        client_sock.sendall(initial_bytes)
    except OSError:
        return False, end_row

    hits = 0
    deadline = time.monotonic() + timeout_seconds
    telnet_filter = TelnetFilter()
    last_shown = int(timeout_seconds)

    while hits < required_hits:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, end_row

        # Cap the wait to 1 second so we wake up regularly to update
        # the live countdown even if the caller sends nothing at all --
        # not just once at the very end when the full timeout expires.
        select_timeout = min(remaining, 1.0)
        try:
            ready, _, _ = select.select([client_sock], [], [], select_timeout)
        except OSError:
            return False, end_row

        if countdown is not None:
            remaining_now = deadline - time.monotonic()
            shown = max(0, int(remaining_now + 0.999))  # ceiling, floor at 0
            if shown != last_shown:
                row, col, width = countdown
                text = f"{shown:>{width}}".encode("ascii")[-width:]
                update = f"\x1b[{row};{col}H".encode("ascii") + text
                try:
                    client_sock.sendall(update)
                except OSError:
                    return False, end_row
                last_shown = shown

        if not ready:
            continue  # just a 1-second polling wakeup, no data yet

        try:
            raw = client_sock.recv(256)
        except OSError:
            return False, end_row
        if not raw:
            return False, end_row  # caller disconnected
        log.debug(f"{addr[0]} raw gate bytes: {raw[:64].hex()}"
                   f"{' ...' if len(raw) > 64 else ''}")
        data = telnet_filter.feed(raw)
        # Only count these as real gate presses if the ENTIRE chunk is
        # nothing but ESC/* bytes. A real keypress (or several fast
        # presses coalesced into one packet) arrives this way; a
        # scripted payload -- an HTTP GET request, for instance --
        # arrives as a large mixed chunk that might just happen to
        # contain a couple of '*' bytes incidentally (e.g. inside an
        # Accept: */* header). Requiring a "pure" chunk closes that
        # off, and as a side benefit also stops a stray ESC that's
        # actually the start of an arrow-key/ANSI sequence (ESC [ A,
        # etc.) from being miscounted as a deliberate ESC press.
        if data and all(b in TRIGGER_BYTES for b in data):
            hits += len(data)
        elif len(data) > 8:
            # A chunk this large that ISN'T pure ESC/* isn't a human
            # keystroke by any stretch (even function/arrow keys only
            # run a few bytes) -- it's a scripted payload. Fail this
            # immediately rather than silently ignoring it and idling
            # out the rest of the full timeout, which just ties up a
            # connection slot for no reason and made bots/scanners
            # look like they were "hanging" rather than being rejected.
            return False, end_row
        # Small non-trigger chunks (a stray mistyped key, etc.) are
        # just ignored -- the caller still gets the rest of their time
        # window to press ESC/* correctly.

    return True, end_row


def relay(a, b):
    """Bidirectional byte relay between two connected sockets, until
    either side closes."""
    sockets = [a, b]
    try:
        while True:
            ready, _, err = select.select(sockets, [], sockets, 60)
            if err:
                break
            if not ready:
                continue  # idle timeout on select, keep waiting
            for s in ready:
                other = b if s is a else a
                try:
                    data = s.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                try:
                    other.sendall(data)
                except OSError:
                    return
    finally:
        for s in sockets:
            try:
                s.close()
            except OSError:
                pass


active_connections = {}
active_connections_lock = threading.Lock()


def try_acquire_ip_slot(ip, ip_cap):
    """Atomically check-and-increment the per-IP active connection
    count. Returns True if this connection may proceed, False if the
    IP is already at its cap (0 = unlimited, always True)."""
    if ip_cap <= 0:
        return True
    with active_connections_lock:
        current = active_connections.get(ip, 0)
        if current >= ip_cap:
            return False
        active_connections[ip] = current + 1
        return True


def release_ip_slot(ip):
    with active_connections_lock:
        current = active_connections.get(ip, 0)
        if current <= 1:
            active_connections.pop(ip, None)
        else:
            active_connections[ip] = current - 1


def handle_client(client_sock, addr, cfg, blocklists, rate_limiter):
    ip = addr[0]

    action, reason = check_access(ip, blocklists, cfg, rate_limiter)
    if action == "block_logged":
        log.warning(f"{ip} blocked by {reason}.")
        try:
            client_sock.close()
        except OSError:
            pass
        return
    elif action == "block_silent":
        try:
            client_sock.close()
        except OSError:
            pass
        return
    # 'exempt' and 'allow' both proceed normally from here

    if not try_acquire_ip_slot(ip, cfg["ip_cap"]):
        log.warning(f"{ip} rejected -- already at IP cap ({cfg['ip_cap']}), instant drop.")
        try:
            client_sock.close()
        except OSError:
            pass
        return

    try:
        _handle_client_inner(client_sock, addr, cfg)
    finally:
        release_ip_slot(ip)


def _handle_client_inner(client_sock, addr, cfg):
    log.info(f"Connection from {addr[0]}:{addr[1]} -- running gate...")
    send_initial_telnet_options(client_sock)
    try:
        passed, end_row = run_gate(client_sock, cfg, addr)
    except Exception as e:
        log.error(f"Gate error for {addr[0]}: {e}")
        client_sock.close()
        return

    if not passed:
        log.info(f"{addr[0]} failed the gate -- closing, no backend connection made.")
        try:
            # Move well below the actual bottom of the displayed prompt
            # before printing this -- otherwise it lands wherever the
            # live countdown last left the cursor, mid-screen.
            position = f"\x1b[{end_row + 2};1H".encode("ascii")
            client_sock.sendall(position + b"No response.\r\n")
        except OSError:
            pass
        client_sock.close()
        return

    log.info(f"{addr[0]} passed the gate -- connecting to backend "
             f"{cfg['backend_host']}:{cfg['backend_port']}")
    try:
        client_sock.sendall(b"\x1b[2J\x1b[H")  # clear screen for a clean handoff
    except OSError:
        pass

    try:
        backend_sock = socket.create_connection(
            (cfg["backend_host"], cfg["backend_port"]), timeout=10
        )
    except OSError as e:
        log.warning(f"Could not reach backend for {addr[0]}: {e}")
        try:
            client_sock.sendall(b"\r\nBBS unavailable.\r\n")
        except OSError:
            pass
        client_sock.close()
        return

    # If enabled, the PROXY protocol header MUST be the very first
    # bytes on this connection, before anything else -- including
    # telnet negotiation. Only sent when the sysop has explicitly
    # opted in, since a backend not expecting it would just see this
    # as garbled login data.
    if cfg.get("send_proxy_protocol"):
        try:
            backend_sock.sendall(build_proxy_protocol_header(addr, backend_sock))
        except OSError as e:
            log.warning(f"Could not send PROXY protocol header for {addr[0]}: {e}")

    # NetSerial (and presumably similar telnet-bridge products) waits
    # to see a real telnet negotiation handshake from whatever connects
    # to it, and drops the line as "Telnet not detected" / NO CARRIER
    # if nothing arrives in time. Since we're now the "client" on this
    # leg, we need to negotiate the same way a real telnet client
    # (SyncTERM, etc.) would on first connect.
    send_initial_telnet_options(backend_sock)

    relay(client_sock, backend_sock)
    log.info(f"Connection from {addr[0]} closed.")


def enable_windows_ansi():
    """Legacy Windows consoles (plain cmd.exe, older PowerShell hosts)
    don't interpret ANSI escape codes unless Virtual Terminal
    Processing is explicitly turned on for that console -- Windows
    Terminal already does this by default, which is why the same
    bytes render correctly there but show up as literal escape-code
    text (e.g. "<-[1;33m") in classic cmd.exe. This flips that switch
    via a direct Windows API call, no third-party dependency needed.
    No-op on any non-Windows platform, and any failure here is
    silently ignored -- this is purely cosmetic and must never block
    startup."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        STD_OUTPUT_HANDLE = -11
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
    except Exception:
        pass


def print_startup_banner(cfg):
    """Prints an optional ANSI/ASCII banner to the local console on
    startup -- purely cosmetic. Only shown when actually attached to
    an interactive terminal; never under systemd or when output is
    redirected/logged, since raw ANSI escape codes have no place in
    a structured log file. Any failure here is silently ignored --
    a missing or broken banner file should never block startup."""
    path = cfg.get("banner_file", "")
    if not path or not sys.stdout.isatty():
        return
    enable_windows_ansi()
    try:
        with open(path, "rb") as f:
            data = f.read()
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.write(b"\r\n\r\n")
        sys.stdout.flush()
    except OSError:
        pass


def main():
    cfg = load_config()
    print_startup_banner(cfg)
    setup_logging(cfg["log_file"], cfg["log_level"])
    blocklists = BlockLists(cfg)
    rate_limiter = RateLimiter(cfg, cfg["can_dir"])
    log.info(f"Listening on 0.0.0.0:{cfg['listen_port']}, "
             f"relaying to {cfg['backend_host']}:{cfg['backend_port']} on pass.")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", cfg["listen_port"]))
    listener.listen(8)

    try:
        while True:
            client_sock, addr = listener.accept()
            t = threading.Thread(
                target=handle_client,
                args=(client_sock, addr, cfg, blocklists, rate_limiter),
                daemon=True,
            )
            t.start()
    except KeyboardInterrupt:
        log.info("Shutting down.")
    finally:
        listener.close()


if __name__ == "__main__":
    main()
