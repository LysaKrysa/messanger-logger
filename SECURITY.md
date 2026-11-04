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
