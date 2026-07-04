# BotGate — Quick Install

0. Need Python first? Requires **3.6+**.
   - Windows: [python.org/downloads](https://www.python.org/downloads/) — check "Add python.exe to PATH" during install.
   - Linux: usually already installed (`python3 --version`); if not, `sudo apt install python3` (Debian/Ubuntu/Pop!_OS) or your distro's equivalent.
   - macOS: [python.org/downloads](https://www.python.org/downloads/) or `brew install python3`.

1. Copy `botgate_proxy.py` to a machine that can reach your BBS.

2. Run it once:
   ```
   python3 botgate_proxy.py
   ```
   This creates `botgate_proxy.cfg` and exits.

3. Edit `botgate_proxy.cfg`:
   - `backend_host` / `backend_port` → your real BBS's address
   - `listen_port` → the port callers will connect to (usually your BBS's current public port)

   **Running everything on one PC?** That's fine — `backend_host` can just be `127.0.0.1`, with your BBS listening on a different local port than BotGate.

4. Move your BBS/telnet server to a different, internal-only port — not exposed to the internet.

5. Point your router's port-forward at this machine instead of the BBS, using the same public port.

6. Run it again:
   ```
   python3 botgate_proxy.py
   ```
   You should see:
   ```
   Listening on 0.0.0.0:23230, relaying to <your BBS>:<port> on pass.
   ```

7. Test a connection from **outside your network** before calling it done.

Full documentation: see `botgate.md`
