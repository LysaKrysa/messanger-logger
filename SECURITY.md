# Security and private data

Facebook cookies grant account access. Telegram tokens authorize the bot.
Archives, decrypted media, and encryption-device state are private data.

## Runtime files

Keep `messenger.env` and `messenger-data/` outside the source checkout. Use mode
`600` for the environment file and a private data directory. Git ignore rules
exclude common runtime paths, but cannot remove a secret already committed or
recognize an arbitrarily renamed backup.

Do not post real cookies, tokens, raw provider payloads, native `device.json`,
sessions, SQLite files, or private media in issues/PRs. If credentials are
exposed, revoke/refresh them through the relevant service. Removing a file from
a later commit does not remove it from Git history.

## Optional administrator dashboard

The dashboard adds an HTTPS listener on TCP 8443. Verify the initial self-signed
certificate fingerprint through SSH before entering credentials, or install a
trusted certificate. Use a strong administrator password and restrict network
access where practical. There is no public user registration or credential-read
endpoint. Saved cookies/bot tokens are encrypted; the master key must be kept
private. Cookies and device state remain accessible to the trusted host admin.

Web/bot services are unprivileged and have no Docker socket/group access. A
separate root manager controls only generated account container names and paths.
Workers have separate archives, read-only code/root filesystems, dropped Linux
capabilities, memory/process limits, and individual gateway keys. Host networking
is used for loopback gateway access; this is not a hostile-tenant sandbox.

Invitations are hashed, single-use, and expire after 15 minutes. Private Telegram
identity claims require administrator approval. Every gateway request checks the
approved destination and a revocable account credential. The shared bot token
is not provided to workers; destructive Telegram methods are not exposed.

See [dashboard deployment and threat boundaries](docs/DASHBOARD.md).

## Safe diagnostics

Use static operational errors, categories, versions, and synthetic reproductions.
`check-telegram-topics` is read-only and reports the bot's public username and
capability flags without secrets. View commands display private messages:
review and redact output before sharing it. Full Docker inspect output can
contain environment secrets and should not be posted.

Local retention does not revoke Telegram copies, manual downloads, backups, or
cloud snapshots. Use only accounts/conversations you are authorized to archive.

## Reporting a vulnerability

If GitHub's **Report a vulnerability** option is available, use it for a private
report. Otherwise open a minimal issue asking for a private contact route,
without exploit details, credentials, or private data. This document does not
imply that a private reporting address or response-time guarantee exists.

Once a private route is established, include the affected revision, impact,
and a synthetic reproduction. Do not test against other people's accounts or
send real messages as part of a public report.
