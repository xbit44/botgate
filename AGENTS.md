# AGENTS.md — BotGate

## Required context

Before changing code, read:

- `../BBS_CODEX_MASTER_CONTEXT.txt`
- `readme.md`
- `quick-install.md`
- `botgate.md`
- `botgate_proxy.cfg`
- the current Git history and release tags

The checked-in code and configuration are authoritative for the current
release. The master context documents project history and known decisions.

## Project constraints

- BotGate is a pure Python 3 standard-library TCP/Telnet front-door proxy.
- Do not add third-party dependencies without explicit approval.
- Preserve operation on Linux, Windows, and macOS where practical.
- Keep configuration backward-compatible unless a deliberate release change
  is approved and documented.
- Do not open a backend connection until the caller passes the configured
  challenge.
- Treat the challenge as bot filtering, not strong authentication.

## Required workflow

1. Run:
   - `git status`
   - `git branch --show-current`
   - `git log -1 --oneline`
   - `git tag --sort=-version:refname`
2. Read the relevant code and sample configuration.
3. Explain the proposed change and compatibility impact.
4. Make focused changes.
5. Run syntax checks and focused tests.
6. Update documentation and sample configuration with behavior changes.
7. Review `git diff`.
8. Leave the tree clean or clearly explain remaining changes.

## Testing expectations

Test relevant paths including:

- allowed caller
- successful ESC challenge
- successful `*` challenge
- failed challenge
- challenge timeout
- ANSI prompt
- ASCII prompt
- exact IP block
- CIDR block
- wildcard block
- silent block
- exemption
- reverse-DNS hostname match
- reverse-DNS timeout or failure
- geo block
- per-IP connection cap
- global connection cap
- sliding-window temporary ban
- ban expiry
- backend unavailable
- caller disconnect
- backend disconnect
- optional PROXY protocol
- relative path handling under service startup

## Code-quality rules

- Use clear standard-library Python.
- Add type hints where they improve safety and readability.
- Handle network errors and disconnects explicitly.
- Avoid unbounded state growth.
- Prune stale rate-limit and temporary state.
- Avoid unnecessary reverse-DNS work.
- Document fail-open or fail-closed behavior.
- Do not log secrets or excessive caller data.
- Distinguish a connection cap from a strict worker-thread cap.

## Release rules

When preparing a release:

- update version references consistently
- update `readme.md`, `quick-install.md`, and `botgate.md`
- update the sample configuration
- run tests
- commit with a focused message
- create an annotated tag only after verification
- push the branch and tag
- confirm the working tree is clean

## Communication

- Give copy/paste-ready commands.
- Explain compatibility and operational impact.
- Separate confirmed behavior from inferred behavior.
- Do not assume historical defaults remain current without reading the
  checked-in v2.3-or-later configuration.
