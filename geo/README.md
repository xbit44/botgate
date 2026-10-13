BotGate v2.4.2 -- optional geo-blocklists

Drop IP2Location-format geo-block files here.

Every *.txt file in this directory is loaded automatically at
startup -- no configuration changes needed to add a country.

Download from: https://www.ip2location.com/free/visitor-blocker
Output format: "Apache 2.0 - 2.3 .htaccess deny" (required -- other
formats on that page are not recognized).

Choose IPv4 lists. IPv6 entries are skipped, with one warning per affected
file at startup. Valid IPv4 ranges remain active. This avoids the startup
overflow fixed in v2.4.2; it does not add IPv6 caller support or filtering.

IP2Location recommends refreshing these files monthly. This
directory ships empty; geo-blocking is entirely opt-in.

See botgate.md, Section 8, for full details.

The managed AbuseIPDB feed is separate from these country files. BotGate
stores its automatically refreshed abuseipdb.can in the configured can_dir,
normally can/, and does not automatically download or refresh geo files.
See botgate.md, Section 7a, for the managed-feed settings.
