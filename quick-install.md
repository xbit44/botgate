# BotGate v2.4.2 — Quick Install

0. Need Python first? Requires **3.6+**.
   - Windows: [python.org/downloads](https://www.python.org/downloads/) — check "Add python.exe to PATH" during install.
   - Linux: usually already installed (`python3 --version`); if not, `sudo apt install python3` (Debian/Ubuntu/Pop!_OS) or your distro's equivalent.
   - macOS: [python.org/downloads](https://www.python.org/downloads/) or `brew install python3`.

1. Copy the whole release (`botgate_proxy.py`, `botgate_proxy.cfg`, `can/`, `geo/`, and the rest) to a machine that can reach your BBS. A ready-to-edit config is already included — no separate "generate a config" step needed.

2. Edit `botgate_proxy.cfg`:
   - `backend_host` / `backend_port` → your real BBS's address
   - `listen_port` → the port callers will connect to (usually your BBS's current public port)
   - Under `[proxy]`, `abuseipdb update = 12` → refresh the managed IPv4
     reputation list every 12 hours. `0` disables both downloads and feed
     blocking. It defaults to `12` even if omitted from an older config.
     Enabled BotGate needs outbound HTTPS access to `raw.githubusercontent.com`
     and write access to `can_dir` to save `abuseipdb.can`. A download failure
     does not stop startup; any valid cache remains active.

   **Running everything on one PC?** That's fine — `backend_host` can just be `127.0.0.1`, with your BBS listening on a different local port than BotGate.

3. Move your BBS/telnet server to a different, internal-only port — not exposed to the internet.

4. Point your router's port-forward at this machine instead of the BBS, using the same public port.

5. Run it:
   ```
   python3 botgate_proxy.py
   ```
   You should see:
   ```
   [23230] Listening on 0.0.0.0:23230, relaying to <your BBS>:<port> on pass.
   BotGate running with 1 listener(s). (max_connections=50)
   ```

6. Test a connection from **outside your network** before calling it done.

**Protecting a second app too?** (2.3+) Add a `[Listener2]` section to `botgate_proxy.cfg` with its own `listen_port`/`backend_host`/`backend_port` — no changes needed to `[proxy]` or anything else. See `botgate.md`, Section 19.

Full documentation: see `botgate.md`

**Upgrading an existing installation?** Keep your production configuration,
prompt art, and local blocklists. Stop BotGate, back up the working script,
replace it with v2.4.2, then restart. No config edit is required when upgrading
from v2.4 or v2.4.1. IPv6 entries in geo files are now skipped with a warning,
preserving valid IPv4 ranges instead of crashing at startup. Choose IPv4 lists;
this update does not add IPv6 listening or filtering.

The countdown-color fix from v2.4.1 remains included. Keep `live_countdown = yes`
for live updates (`no` continues to show a static starting number).

If upgrading from before v2.4, add the commented `abuseipdb update` setting from
the sample config under your existing `[proxy]` section; the default when omitted
is 12 hours. Do not overwrite your configuration with the sample's
backend values. The feed cache is created after the first successful refresh;
never install a bundled snapshot over your existing cache. Restart BotGate
after updating the script and config. Refresh results log at INFO; callers
blocked by this feed log at WARNING. Updates replace the full feed, including
upstream removals. See Section 7a of `botgate.md` for details.
