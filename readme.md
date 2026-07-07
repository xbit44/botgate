# BotGate

A TCP-level bot gate for BBS systems. BotGate sits in front of your BBS's real telnet port and requires each caller to press ESC and/or `*` twice within a configurable timeout before your actual BBS software ever sees the connection. Callers who don't respond — or who are obviously automated rather than human — are disconnected without ever reaching the BBS.

Originally built to protect a Spitfire BBS node running behind a NetSerial virtual-modem bridge, but it works with any BBS reachable over telnet (Synchronet, Mystic, WWIV, Spitfire, or anything else), since it operates purely at the TCP/telnet level with no dependency on, or awareness of, what's actually running behind it.

## Features

- ESC/`*` challenge gate — configurable timeout and required hit count
- Custom ANSI/ASCII prompt screens, with a live per-second countdown
- Optional local startup banner for the sysop's own console — never shown when piped/logged/under a service manager
- Synchronet-style `.can` blocklist support — IP, CIDR, wildcard, and hostname patterns, with an always-allow exempt list
- Geo-blocking via IP2Location-format `.htaccess` lists, auto-loaded from a folder — no config changes needed to add a country
- Reverse-DNS hostname matching, with a hard timeout and fail-open behavior so legitimate callers are never blocked over a slow or missing PTR record
- Per-IP simultaneous connection cap, plus a global cap across all IPs combined for flood protection
- Automatic rate-limiting with a self-managed, human-readable temporary ban file
- Optional PROXY protocol support, so backends like Synchronet (with `HAPROXY_PROTO` enabled) can still see the real caller's IP for their own filtering and logs
- Pure Python 3 standard library — no third-party dependencies, runs on Linux, Windows, and macOS

## Getting Started

See **[quick-install.md](quick-install.md)** for the bare-minimum steps to get running, and **[botgate.md](botgate.md)** for the full user guide — every configuration option, feature walkthroughs, and troubleshooting.

## Credits

Telnet protocol negotiation handling — the IAC constants, the `send_initial_telnet_options()` negotiation function — was adapted near-verbatim from the [ANetBBS Selector](https://github.com/anetonline/ANetBBS-Selector) project. Thank you to its author for sharing the source.

Thanks also to [Digital Man](https://www.synchro.net/) of the Synchronet project, whose `.can`-file filtering conventions inspired BotGate's own blocklist format and the concept behind its IAC-stripping input filter.

## License

MIT — see [LICENSE](LICENSE).
