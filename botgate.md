# BotGate

**A TCP-Level Bot Gate for BBS Systems**
Version 2.2 — User Guide & Configuration Reference

BotGate is a standalone Python 3 program that stands in front of a BBS's real telnet port and requires each caller to prove they can follow a simple interactive instruction — pressing ESC or `*` twice — before the actual BBS software ever sees the connection. Callers who don't respond (or who are obviously automated, not human) are disconnected without ever reaching the BBS.

It was originally built to protect a Spitfire BBS node running behind a NetSerial virtual-modem bridge, but it works with any BBS reachable over telnet — Synchronet, Mystic, WWIV, Spitfire, or anything else — since it operates purely at the TCP/telnet level and has no knowledge of, or dependency on, what runs behind it.

## Table of Contents

1. [Why BotGate Exists](#1-why-botgate-exists)
2. [How It Works](#2-how-it-works)
3. [Requirements](#3-requirements)
4. [Installation & Quick Start](#4-installation--quick-start)
5. [The Gate](#5-the-gate)
6. [Custom Prompts & the Live Countdown](#6-custom-prompts--the-live-countdown)
   - [6a. Startup Banner (local console, optional)](#6a-startup-banner-local-console-optional)
7. [Blocklists (.can files)](#7-blocklists-can-files)
8. [Geo-Blocking (/geo directory)](#8-geo-blocking-geo-directory)
9. [Reverse DNS (host.can)](#9-reverse-dns-hostcan)
10. [Per-IP Connection Cap](#10-per-ip-connection-cap)
    - [10a. Global Connection Cap](#10a-global-connection-cap)
11. [Rate Limiting & Auto Temp-Bans](#11-rate-limiting--auto-temp-bans)
12. [Logging](#12-logging)
13. [Full Configuration Reference](#13-full-configuration-reference)
14. [Platform Notes](#14-platform-notes)
15. [Troubleshooting](#15-troubleshooting)
16. [Credits](#16-credits)
17. [Version History](#17-version-history)
18. [Passing the Real Caller IP to the Backend (Advanced)](#18-passing-the-real-caller-ip-to-the-backend-advanced)

---

## 1. Why BotGate Exists

Many BBS front ends — especially legacy DOS-era software like Spitfire — have no concept of filtering hostile or automated traffic before it reaches the software's own connection-handling code. That code was written in an era before the modern internet's constant background noise of scanners, bots, and automated probes, and it shows: malformed or unexpected input can cause instability or lockups that have nothing to do with anything a real caller would ever send.

The instinct is usually to add a gate inside the BBS software or its startup scripts. In practice this often doesn't work: many BBS packages (Spitfire included) unconditionally redo their own modem/connection handshake every time they start, discarding any connection state an external gate had already established. BotGate sidesteps this entirely by moving the gate outside the BBS and its supporting software altogether — to the network layer, in front of everything. Nothing downstream ever sees a connection until it has already proven itself.

## 2. How It Works

BotGate listens on the public-facing port your BBS used to listen on directly. When a connection arrives:

1. Blocklist and rate-limit checks run first (Sections 7–11) — known-bad IPs, hostnames, geographic ranges, and abusive connection rates are rejected immediately, before anything else happens.
2. If not blocked, the ESC/`*` gate runs (Section 5).
3. On success, the screen clears and BotGate opens a new connection to the real BBS, then transparently relays bytes in both directions — the BBS behaves exactly as if the caller had connected directly.
4. On failure (timeout, disconnect, or an obviously non-interactive payload), the connection is simply closed. No connection to the real BBS is ever made.

Because the relay is a byte-for-byte passthrough once established, the real BBS software requires no modification, no awareness of BotGate, and no special configuration beyond listening on a different (non-public) port.

## 3. Requirements

- Python 3.6 or later.
- Standard library only — no pip installs, no third-party dependencies.
- Runs anywhere Python 3 runs: Linux, Windows, macOS.
- No dependency on, or awareness of, the BBS software behind it.

### Don't have Python installed?

- **Windows** — download the installer from [python.org/downloads](https://www.python.org/downloads/). On the first setup screen, check **"Add python.exe to PATH"** before clicking Install — this is the single most common thing people miss, and without it `python3` won't be recognized from the command line afterward.
- **Linux** — most distributions already have Python 3 installed (check with `python3 --version`). If not: Debian/Ubuntu-based systems (including Pop!_OS) use `sudo apt install python3`; Fedora/RHEL-based systems use `sudo dnf install python3`; Arch-based systems use `sudo pacman -S python`.
- **macOS** — either the installer from [python.org/downloads](https://www.python.org/downloads/), or `brew install python3` if you use Homebrew.

Verify it worked with `python3 --version` (or `python --version` on some Windows setups) — it should print `Python 3.6` or higher.

## 4. Installation & Quick Start

1. Copy the whole release — `botgate_proxy.py`, `botgate_proxy.cfg`, the `can/` and `geo/` folders, and the rest of the supporting files — to a machine that can reach your BBS's real listening port (this can be the same machine the BBS runs on, or a different one on the same network). The release already includes a ready-to-edit `botgate_proxy.cfg`, so there's no separate "generate a config" step needed.

   **Single-PC sysops:** this is the common case — BotGate and your BBS can both run on the same computer. Just point `backend_host` at `127.0.0.1` and have your BBS listen on a different local port than BotGate does.

   *(If you only have `botgate_proxy.py` by itself with no config file present, running it once will write a default `botgate_proxy.cfg` next to itself and exit — same content as the one included in the full release.)*

2. Edit `botgate_proxy.cfg`:
   - Set `backend_host` and `backend_port` to your real BBS's address — where it's actually listening.
   - Set `listen_port` to the port callers will connect to. Using the same port your BBS used before means existing callers' phonebook entries don't need to change.

3. **(Strongly recommended)** Reconfigure your BBS/telnet server to listen on a different, internal-only port — not the public one — so the gate cannot be bypassed by connecting directly to the BBS's real port. Make sure that internal port is not separately exposed to the internet.

4. Update your router's port-forward to point at the machine running BotGate, using the same public-facing port your BBS used before.

5. Run BotGate:
   ```
   python3 botgate_proxy.py
   ```
   It should report something like:
   ```
   Listening on 0.0.0.0:23230, relaying to 192.168.1.199:2323 on pass.
   ```

6. Test a connection from **outside your own network** before considering the migration complete — a connection from inside your LAN can sometimes behave differently than one from the real internet (see Section 15, Troubleshooting).

## 5. The Gate

By default, a caller must press ESC (`0x1B`) and/or `*` (`0x2A`), in any combination, twice within 20 seconds (both configurable — see Section 13). On success, the screen clears and the caller is handed off to the real BBS. On failure, the connection is closed.

**What this is, and isn't.** The ESC/`*` challenge is effective against generic scanners, HTTP/SSH probes, and bots that blindly fire off unrelated payloads without ever reading what comes back — which describes essentially all of the automated internet background noise a public BBS port attracts. It is not cryptographic authentication or identity verification: a bot specifically written to send the expected characters would pass. Treat BotGate as a scanner/automation filter that protects legacy BBS software from routine internet noise, not as a security boundary against a targeted attacker.

### Anti-bypass protections

- A keypress only counts if the entire chunk of data received is purely ESC/`*` bytes. This closes off a real-world bypass found in testing: an HTTP scanner's request happened to contain two literal `*` characters inside an ordinary `Accept: */*` header, which a naive per-byte check miscounted as two genuine keypresses.
- A large chunk of data that is *not* purely ESC/`*` (more than 8 bytes) is treated as a scripted, non-interactive payload and fails the gate immediately, rather than idling for the rest of the timeout window. Smaller stray keystrokes (a mistyped key, an arrow-key sequence) are simply ignored, giving real humans the benefit of the doubt.
- Telnet protocol negotiation (IAC sequences) is transparently handled on both the caller-facing and backend-facing sides of the connection, so real telnet clients and the actual BBS's own negotiation both work correctly without interference from each other.
- Before drawing the prompt, the screen is cleared and the cursor is homed (`ESC[2J` `ESC[H`). Some web-based telnet clients/gateways print their own connection-status text into the same terminal buffer before handing off to BotGate — without this reset, the whole prompt (and the live countdown's absolute cursor-positioning) would be shifted down by however many rows that status text took, landing the countdown in the wrong place. Harmless no-op on clients that already start at a blank screen.

## 6. Custom Prompts & the Live Countdown

Set `prompt_file` to the path of any ANSI or ASCII file to fully customize what callers see during the gate. The file is sent as raw bytes — CP437 box-drawing and ANSI color codes work exactly as authored — and is re-read from disk on every single connection, so it can be edited live without restarting BotGate.

### Live countdown

Include a run of `#` characters anywhere in the prompt file (for example `##`) and BotGate will substitute the starting timeout value there, then update it live, once per second, counting down — without redrawing anything else on the screen. The field width follows the number of `#` characters used: `##` gives a 2-digit field, `###` gives 3, and so on.

Set `live_countdown = no` to disable the per-second updates — the starting number still displays (the `#` placeholder is still substituted), it just stays static rather than counting down. This is a compatibility option for clients whose ANSI handling still causes trouble even with the screen-reset fix above.

### Line endings

Line endings are automatically normalized to CRLF regardless of how the file was saved (Linux, Windows, or old Mac-style), since telnet terminals require the carriage return to actually reset the cursor to column 1 — without this, ANSI art "staircases" diagonally across the screen.

### Authoring ANSI art

Real box-drawing characters (`═`, `║`, etc.) must be authored in a genuine DOS-art tool such as **Moebius** — not a plain text editor like Notepad. Notepad re-saves files as UTF-8, which represents those characters as multi-byte sequences instead of the single CP437 byte a BBS terminal expects, corrupting the art on screen.

## 6a. Startup Banner (local console, optional)

Separately from the caller-facing prompt, `banner_file` can point at an ANSI/ASCII file to display on BotGate's own local console when it starts up — purely cosmetic, a nice touch if you like a bit of visual flair when you or another sysop is watching the terminal.

This is only ever shown when BotGate is actually attached to an interactive terminal (`sys.stdout.isatty()`). If output is piped, redirected to a file, or running under a service manager like systemd, the banner is silently skipped — raw ANSI escape codes have no place cluttering a structured log, and this check keeps them out of one automatically. Nothing needs to be configured differently for the two cases; it just does the right thing based on how it's being run.

Leave `banner_file` blank (the default) to disable this entirely.

## 7. Blocklists (.can files)

BotGate supports Synchronet-style `.can` blocklist files, located in the directory set by `can_dir` (default: `can/`, next to the script). Format: one entry per line — a plain IP address, CIDR notation (e.g. `192.168.1.0/24`), a wildcard (e.g. `*.example.com`), or the same prefixed with `!` to negate/exempt that specific pattern within the file. Lines starting with `;` or `#` are comments.

### Wildcard support

- `*` matches any number of characters (e.g. `*.example.com` matches any subdomain).
- `?` matches exactly one character.
- `^` and `~` are **not** implemented as wildcards, despite appearing in some `.can` file header comments (that wording matches Synchronet's own convention, kept for familiarity). BotGate could not confirm Synchronet's exact documented behavior for those two characters, and none of the real-world `.can` data used to build and test this feature used them — so rather than guess at unverified semantics, they're treated as ordinary literal characters. A pattern containing `^` or `~` will only match text containing that literal character, not act as a wildcard.

### Files

| File | Purpose |
|---|---|
| `ipfilter_exempt.cfg` | IPs that are always allowed, bypassing every other check in this list (including rate limiting and the `ip_cap` connection limit). |
| `ip.can` | Blocked IPs/CIDR/wildcards. Matches are logged. |
| `ip-silent.can` | Blocked IPs/CIDR/wildcards. Matches are **not** logged — useful for known noise sources you don't want cluttering your logs. |
| `host.can` | Blocked hostname patterns, checked against each connecting IP's reverse-DNS result (see Section 9). |
| `temp_ip.can` | Fully auto-managed by the rate limiter (Section 11). Auto-created if missing; there's no need to hand-create it. |

With the exception of `temp_ip.can` (which updates live as bans are issued and expire), these files are loaded once at startup. Restart BotGate to pick up manual edits.

### Evaluation order

`exempt` → `temp_ip.can` → `ip.can` → `ip-silent.can` → geo blocklists → `host.can` → rate-limit check → gate. The first match wins; exempt IPs skip every subsequent check entirely.

## 8. Geo-Blocking (/geo directory)

Every `.txt` file found in the directory set by `geo_dir` (default: `geo/`) is loaded automatically at startup — no configuration changes are needed to activate a new country's blocklist. Just add or remove files and restart.

### File format

Files must be in Apache `.htaccess` `"deny from x.x.x.x/nn"` format, exactly as downloaded from:

```
https://www.ip2location.com/free/visitor-blocker
```

When downloading, select **"Apache 2.0 - 2.3 .htaccess deny"** as the output format. Other formats offered on that page (Apache 2.4, Nginx, CIDR, iptables, etc.) are not recognized by BotGate's parser.

IP2Location recommends refreshing these lists monthly, since IP allocations change over time.

### Performance

Geo ranges are converted at startup into sorted integer ranges and checked via binary search rather than a linear scan. In testing against real-world data (roughly 32,000 combined entries across two countries), this measured at approximately 5 microseconds per connection check and about 17 bytes of memory per blocked range — a full 500,000-entry blocklist across many countries would still cost only a few megabytes of memory.

## 9. Reverse DNS (host.can)

Controlled by `dns_lookup_enabled` (yes/no). When enabled, BotGate performs a reverse-DNS lookup on each connecting IP with a hard 2-second timeout, and checks the resulting hostname against `host.can`.

If the lookup doesn't complete within the timeout, or the IP has no PTR record at all, the connection is **not** blocked — it fails open. This is deliberate: many legitimate residential and dynamic IP addresses have no reverse DNS, and a flaky or slow resolver should never be able to block real callers.

The lookup itself is skipped entirely if `host.can` has no active patterns (empty, or comments only) — there's no reason to pay the DNS round-trip cost, or spin up a lookup thread, for a check that can't possibly block anything.

## 10. Per-IP Connection Cap

`ip_cap` limits how many simultaneous connections a single source IP may have open at once. A connection attempt from an IP already at the cap is dropped instantly — no gate prompt is sent, and no connection to the backend is attempted. Set to `0` to disable.

IPs listed in `ipfilter_exempt.cfg` bypass this entirely — useful for your own workstation/LAN when you need to test multiple node connections at once (e.g. logging into all four nodes of a multi-node BBS simultaneously) without hitting a cap meant for the general public.

## 10a. Global Connection Cap

`max_connections` limits the total number of simultaneous connections across *all* source IPs combined — a different kind of protection than `ip_cap`, which only bounds a single IP's own concurrency. Nothing stops a distributed source (many different IPs at once) from opening unbounded connections otherwise, each spawning its own worker thread; `max_connections` puts a hard ceiling on that.

Unlike `ip_cap`, this applies to *everyone equally, including exempt IPs* — it's resource protection for the server itself, not an abuse-detection measure aimed at any particular caller, so exemption doesn't make sense the same way here. The slot is reserved before a connection thread is even created, so a burst beyond the cap is rejected immediately in the accept loop rather than still spinning up (and then quickly tearing down) a thread per excess connection.

Size this comfortably above your real expected concurrent callers (e.g. your BBS's node count) — it's a safety ceiling, not a normal operating limit. Default is `50`. Must be `1` or greater; BotGate refuses to start with a clear error if it's set to `0` or a negative number, rather than either silently rejecting every caller or crashing.

## 11. Rate Limiting & Auto Temp-Bans

The IP cap (Section 10) only limits how many connections an IP can have open at the same time — it does nothing to stop an IP that connects, gets rejected, and immediately reconnects over and over. Rate limiting closes that gap.

If a single IP makes `rate_limit_hits` connection attempts within a `rate_limit_window_seconds` sliding window, it is automatically added to `temp_ip.can` for `rate_limit_ban_minutes`. Set `rate_limit_hits` to `0` to disable this feature entirely. Exempt IPs (`ipfilter_exempt.cfg`) are never rate-limited or temp-banned.

Ban entries use the same `t=`/`e=` (created/expires) timestamp convention as real Synchronet ban files, so the file remains fully human-readable if you want to inspect it directly:

```
203.0.113.5   t=20260704T112009+0000   e=20260704T112109+0000   r=20 hits in 10.0s
```

Expired entries are automatically dropped the next time BotGate starts, or the next time that specific IP is checked — there is no separate background cleanup timer for `temp_ip.can` itself, so an expired-but-inactive ban entry may remain visible in the file (harmlessly) until one of those two events occurs.

Separately, the in-memory tracking used to *detect* rate-limit violations (not the same thing as the `temp_ip.can` ban file above) is swept automatically in the background every minute or so, discarding any IP's tracking data once its most recent attempt has aged out of the window. This prevents one-off traffic (a single scanner hit that never repeats) from leaving small leftover entries in memory indefinitely on a long-running install.

## 12. Logging

Controlled by `log_file` (blank disables file logging; console output always happens) and `log_level`:

- **DEBUG** — everything, including raw gate-phase bytes in hex and every reverse-DNS result, regardless of outcome.
- **INFO** — connection accepted, gate pass/fail, IP-cap rejections, rate-limit bans, backend handoff, connection closed.
- **WARNING** — blocklist rejections, backend-unreachable errors, rate-limit bans, file read/write problems.
- **ERROR** — unexpected failures in the gate logic itself.

## 13. Full Configuration Reference

| Setting | Default | Description |
|---|---|---|
| `listen_port` | `23230` | Public-facing port BotGate listens on. Point your router's port-forward here. |
| `backend_host` | `192.168.1.XXX` | IP address of the real BBS/telnet server to relay to once a caller passes. Can be `127.0.0.1` if the BBS runs on the same machine as BotGate. |
| `backend_port` | `2323` | Port the real BBS/telnet server is actually listening on. |
| `timeout_seconds` | `20` | Seconds a caller has to press ESC and/or `*` twice. |
| `required_hits` | `2` | Number of ESC/`*` presses required, in any combination. |
| `prompt_file` | *(blank)* | Path to a custom ANSI/ASCII file for the gate screen. Blank = built-in plain-text prompt. |
| `live_countdown` | `yes` | Whether a `#` placeholder live-updates once a second or just shows the starting number statically. See Section 6, Troubleshooting note on web-based clients. |
| `log_file` | `botgate_proxy.log` | Log file path. Blank disables file logging (console only). |
| `log_level` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. `DEBUG` adds raw gate-phase bytes and reverse-DNS results. |
| `ip_cap` | `2` | Max simultaneous connections per source IP. `0` disables. |
| `can_dir` | `can` | Directory holding the `.can` blocklist files (see Section 7). |
| `geo_dir` | `geo` | Directory of IP2Location-style geo-block `.txt` files (see Section 8). |
| `dns_lookup_enabled` | `yes` | Whether to reverse-DNS each connecting IP and check `host.can`. |
| `rate_limit_hits` | `20` | Connection attempts within the window that trigger an auto temp-ban. `0` disables. |
| `rate_limit_window_seconds` | `10` | Sliding window (seconds) the above is measured over. |
| `rate_limit_ban_minutes` | `90` | How long an auto temp-ban (in `temp_ip.can`) lasts. |
| `banner_file` | *(blank)* | Path to an ANSI/ASCII file to print on startup — see Section 6a. Blank disables it. |
| `send_proxy_protocol` | `no` | Sends a PROXY protocol v1 header to the backend — see Section 18. Only enable if your backend specifically supports it. |
| `max_connections` | `50` | Global cap on total simultaneous connections across all IPs combined — see Section 10a. Must be 1 or greater. |

## 14. Platform Notes

BotGate is pure Python 3 standard library — no code changes are needed to run it on Linux, Windows, or macOS. What differs by platform is how you'd keep it running persistently in the background:

- **Linux** — a systemd service (recommended), for automatic start-on-boot and restart-on-crash.
- **Windows** — Task Scheduler configured to run at startup, or a dedicated service wrapper such as NSSM (Non-Sucking Service Manager).

## 15. Troubleshooting

- **Callers see "BUSY" or the connection is refused immediately** — confirm `listen_port` matches what your router is actually forwarding, and that BotGate is running.
- **Log shows "Could not reach backend"** — confirm `backend_host` and `backend_port` match where your real BBS is actually listening.
- **Gate seems to hang or take a long time to fail** — you're likely running an older version without the fail-fast fix for large non-interactive payloads; update to the current version.
- **A test from your own LAN behaves inconsistently or times out, but external callers work fine** — this is very likely NAT hairpinning on your router (looping traffic back out and in through your own public IP), not a BotGate issue. Test from a genuinely external network (cellular data, a friend's connection, or a remote shell) instead.
- **ANSI art looks garbled or "staircases" diagonally** — the `prompt_file` was saved through a plain text editor rather than a real DOS-art tool. Re-author it in Moebius or similar (see Section 6).
- **A geo blocklist file loads with 0 ranges** — confirm "Apache 2.0 - 2.3 .htaccess deny" was selected as the output format when downloading (see Section 8).
- **The live countdown shows garbled/overlapping text on web-based telnet clients (e.g. fTelnet), but looks fine in SyncTERM/NetRunner/etc.** — this is a client-side ANSI rendering limitation, not a BotGate issue; the gate still functions correctly even when the countdown display glitches. Set `live_countdown = no` for a static (non-updating) display that renders correctly everywhere (see Section 6).

## 16. Credits

Telnet protocol negotiation handling — the IAC constant definitions and the `send_initial_telnet_options()` negotiation function — was adapted near-verbatim from the [ANetBBS Selector](https://github.com/anetonline/ANetBBS-Selector) project, a BBS connection-selector tool. Thank you to its author for sharing the source.

Thanks also to [Digital Man](https://www.synchro.net/) of the Synchronet project, whose `.can`-file filtering conventions inspired BotGate's own blocklist format and the concept behind its IAC-stripping input filter.

## 17. Version History

### v1.0

- Core TCP gate proxy: ESC/`*` challenge with configurable timeout and hit count.
- Telnet negotiation handled correctly on both the caller-facing and backend-facing legs.
- Custom ANSI/ASCII prompt file support, with automatic line-ending normalization.
- Live per-second countdown display via a configurable `#` placeholder.
- Console + file logging with selectable verbosity.
- Per-IP simultaneous connection cap.

### v2.0

- Synchronet-style `.can` blocklist support: `ipfilter_exempt.cfg`, `ip.can`, `ip-silent.can`, `host.can`.
- Geo-blocking via IP2Location-format files, auto-loaded from a configurable directory.
- Reverse-DNS hostname matching with fail-open timeout handling.
- Automatic rate-limiting with a self-managed, human-readable `temp_ip.can` ban file.
- Anti-bypass hardening: strict "pure chunk" ESC/`*` matching, and fail-fast rejection of large scripted payloads.

### v2.1

- Fixed: countdown live-updates were sent to the wrong screen column whenever a prompt file had ANSI color codes before the `##` placeholder on the same line (the position was computed from raw byte offset instead of true visual column, which happened to work by coincidence on plain/uncolored prompt files).
- Fixed: `ipfilter_exempt.cfg` entries now also bypass `ip_cap`, matching what the file's own header comment already promised ("exempt from filtering/banning/throttling"). Useful for testing multiple simultaneous node connections from your own IP.

### v2.2

- Fixed: the live countdown could be sent to the wrong screen row entirely (not just column) when a web-based telnet client/gateway printed its own connection-status text into the terminal before handing off to BotGate, shifting the whole prompt block down without the countdown's absolute coordinates shifting with it. BotGate now clears the screen and homes the cursor before drawing the prompt, giving every client a known, consistent starting point. Diagnosed and fixed based on a detailed write-up from a member of the BBS community — thank you.
- Improved: reverse-DNS lookups are now skipped entirely when `host.can` has no active patterns to match against, avoiding unnecessary latency and resolver load on every connection for a check that couldn't block anything anyway.
- Improved: the in-memory tracking behind rate-limiting is now swept periodically in the background, so one-off traffic (a scanner that hits once and never returns) doesn't leave small leftover entries in memory indefinitely on a long-running install.
- Added: `max_connections`, a global cap on total simultaneous connections across all source IPs combined (Section 10a) — protects against a distributed flood that the existing per-IP `ip_cap` can't stop on its own. The cap is enforced before a connection thread is even created, so excess connections during a burst are rejected immediately rather than still spinning up (and quickly tearing down) a thread each. Config validation added: `max_connections` of `0` or negative now fails at startup with a clear error instead of silently rejecting every caller or crashing.
- Fixed: `prompt_file`, `banner_file`, and `log_file` now resolve relative to the script's own directory (matching how `can_dir`/`geo_dir` already worked), instead of relative to whatever directory the process happened to be launched from. Previously, running BotGate manually from a different working directory than usual could silently fail to find these files even though the exact same config worked fine under systemd.
- Documentation: clarified that `*` and `?` are the only real wildcards in `.can` files — `^` and `~` (mentioned in some file header comments for consistency with Synchronet's own convention) are not implemented and are treated as literal characters. Also added explicit framing that the ESC/`*` gate is a bot/scanner filter, not strong authentication.

Items 1–4 above were identified through an independent code review of the v2.1 source; thank you to that reviewer as well.

## 18. Passing the Real Caller IP to the Backend (Advanced)

Because BotGate relays connections by opening a **brand-new** TCP connection to the backend once a caller passes the gate, the backend BBS has no inherent way to know the original caller's IP — it only sees the connection coming from wherever BotGate itself is running (`127.0.0.1`, or whatever machine BotGate is on). This matters if your backend does its own IP-based filtering, banning, or logging, since all of that would otherwise only ever see BotGate's address, not the real caller's.

### Synchronet

Synchronet's telnet server has built-in support for exactly this situation: the `HAPROXY_PROTO` setting in `sbbs.ini` tells it to expect a **PROXY protocol** header — a single line announcing the real client's address — as the very first bytes of each connection, before any telnet traffic. Per Synchronet's own documentation ([wiki.synchro.net/howto:haproxy](https://wiki.synchro.net/howto:haproxy)), it supports both v1 and v2 of the protocol; BotGate sends v1. This requires a Synchronet build from after November 22, 2020.

**Confirmed working**, tested against a real Synchronet v3.22 install: with `HAPROXY_PROTO` enabled and `send_proxy_protocol = yes` set, a test connection from a separate machine on the LAN showed up correctly on Synchronet's own login screen with that machine's real IP *and* hostname (`CLIENT ADDR: XBIT-POPOS [192.168.1.250]`) — not BotGate's own address, which is what it would otherwise show.

To use this:

1. Add `HAPROXY_PROTO` to the `Options` line under `[BBS]` in Synchronet's `sbbs.ini` (pipe-separated alongside whatever else is already there, e.g. `Options = XTRN_MINIMIZED | ALLOW_RLOGIN | ALLOW_SSH | HAPROXY_PROTO`).
2. Set `send_proxy_protocol = yes` in `botgate_proxy.cfg`.
3. Restart both Synchronet and BotGate.

With both sides configured, Synchronet will correctly see each caller's real IP for its own `.can` files, hack-attempt tracking, and connection logs — exactly as if BotGate weren't in the path at all.

**Important:** `send_proxy_protocol` defaults to `no` and must stay that way unless your backend is specifically configured to expect it. Sending this header to a backend that isn't expecting it (Spitfire/NetSerial, Mystic, or Synchronet without `HAPROXY_PROTO` enabled) will break every single connection — the header text would just be read as garbled login data rather than being parsed and stripped off. Only enable both sides together, never just one. Also note: once `HAPROXY_PROTO` is enabled on the Synchronet side, it stops accepting *any* direct connection to its BBS ports — every connection must come through BotGate (or another PROXY-protocol-aware front end) from that point on.

### Mystic

Mystic BBS has its own mechanism for learning a caller's real IP (`-IP$`/`-HOST$` command-line parameters), but it's fundamentally different from Synchronet's: it's passed internally from Mystic's own Internet Server component to the BBS process it launches, not exposed anywhere on the network. There is no equivalent PROXY-protocol-style header BotGate can send that Mystic would consume — as far as could be determined, this isn't something an external proxy can feed.

In practice this is a smaller gap than it might first appear: BotGate's own protections (`ip_cap`, rate limiting, geo-blocking, `.can`-style blocklists) already cover the same categories as Mystic's built-in telnet-server protections (Max Allowed, Auto IP Ban, Country Block), and BotGate applies them using the *real* caller IP, before the connection ever reaches Mystic. What's lost is mostly cosmetic — things like "last callers" displays or logs showing the correct origin IP — rather than an actual gap in abuse protection.

### Other backends

If your BBS software supports PROXY protocol (check its documentation for "PROXY protocol," "HAProxy," or similar), the same `send_proxy_protocol = yes` setting should work the same way it does for Synchronet. If it doesn't, the same reasoning as the Mystic section above applies: BotGate's own protections are still fully in effect using the real caller IP, even if the backend itself only ever sees BotGate's address.
