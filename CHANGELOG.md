# Changelog

## Unreleased — administrator workspace

- Optional HTTPS admin dashboard for up to six separate Messenger accounts.
- Encrypted cookie/bot configuration, expiring sign-in sessions, and CSRF protection.
- One shared Telegram gateway with destination checks, paced sends, single-use
  invitations, administrator-approved pairing, and per-user disconnect.
- Separate root container manager; web/bot services have no Docker access.
- Mounted runtime worker credentials, independent archives, account lifecycle
  controls, stopped-account retention, and original-service migration/rollback.
- Synthetic isolation/migration tests and a real-browser account setup/mobile check.

## Unreleased — initial open-source edition

- Receive-only regular/encrypted capture with a pinned native bridge.
- Future-events-only admission, per-run counters, resolved names, and content-only logs.
- Observed deletion tracking with original/latest captured text and media.
- Bounded authenticated media downloads and supported view-once wrapper parsing.
- Optional formatted Telegram notices and persistent per-conversation topics.
- Fresh read-only Telegram setup checks and local topic-ID repair.
- Rolling 72-hour retention, recoverable file/journal cleanup, and late-download
  protection; remote Telegram copies remain untouched.
- Ubuntu systemd installation, controlled restarts, and an independent cleanup timer.
- Synthetic tests, Docker/native CI configuration, generic setup guides,
  example configuration, and upstream license/provenance records.
