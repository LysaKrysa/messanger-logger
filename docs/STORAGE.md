# Storage and retention

[← README](../README.md) · [Server operation](../SERVER_SETUP.md)

## Managed paths

All paths below are relative to the container's `/data` bind mount.

| Path | Data |
| :--- | :--- |
| `messenger-modern.sqlite3` | Messages, deletion state, metadata, runs, event journal, topic routes |
| `messenger-modern.sqlite3-wal`, `-shm` | SQLite WAL/coordination files |
| `message_logs_modern/runs/<run>/<conversation>__<id>/D_M_Y` | Per-run daily exports |
| `media/<run>/<message-hash>/<attachment>` | Downloaded/decrypted media |
| `messenger-modern-state/<account>/device.json` | Private persistent encryption state |
| `messenger-modern-status.log` | Static operational diagnostics, not message bodies |
| `message_logs/` and older unscoped daily files | Earlier managed log formats |
| `.messenger-modern.lock` | Exclusive listener/offline-cleanup lock |

Never share the data directory for debugging. Do not mount it at `/app`, which
would hide source files.

## Admission and deletion tracking

A message creation timestamp must be at or after the process startup cutoff.
An old message is rejected even if its envelope time is new. Missing message
creation times are not guessed to be fresh. Old edit/reaction timestamps are
also rejected when explicit; otherwise their observation time is used.

Every invocation gets a new run ID and fresh counters. An unsend can mark a
message captured during a preceding run while that message remains retained.
The journal records observed events, not a complete authoritative server history.

The message record preserves its original text, latest captured edit, attachment
metadata, and observed deletion state. A deletion without captured content is a
tombstone, not recovered text. Available content and media remain until expiry.

## Journal and file consistency

SQLite commits a normalized journal entry before appending its daily text export.
Committed byte offsets permit recovery of interrupted appends without duplicate
lines. Do not edit or truncate active daily files manually.

Retention first commits database changes and a pending file-rewrite/deletion
journal. Interrupted rewrites and media removals resume on restart. Fresh
messages sharing a daily file with expired ones are preserved. Downloads that
finish after expiry cannot recreate an expired message's saved files.

## The 72-hour window

The window is measured from local capture, not from midnight or three calendar
dates. Edits, reactions, and unsends do not extend the parent's original lifetime.

Cleanup removes expired message records, related journal events, exported
records, media, obsolete attempt records, and stale derived labels. A structured
reply loses its stored copy of an expired quoted parent. Independent new user
text remains a new message; cleanup is not a search-and-delete of matching words.

SQLite uses secure deletion, compaction, and WAL truncation. A busy reader can
defer physical compaction until a later sweep. These operations avoid retaining
deleted text in ordinary reusable database pages, but are not a forensic wipe
of disks or snapshots.

Old-format files are pruned by readable record timestamps; unknown-format files
age by modification time. The old automatic migration snapshot is removed
because it can contain mixed-age content; new automatic content snapshots are
not created while retention is enabled.

## What remains

- Private cookies and encryption-device state needed to reconnect.
- The account association needed to reject an accidental account mismatch.
- Minimal numeric Telegram topic mappings, allowing existing topics to be reused.
- Telegram messages, files, and topics already created remotely.
- Copies/backups/snapshots created outside the managed runtime paths.

The cleanup module sends no Telegram requests and no Messenger deletion request.

## Schedule and manual cleanup

The running listener checks at startup and about every five minutes. The
systemd timer independently checks when the listener is stopped. It uses the
same data lock and does not start another account connection.

```bash
sudo ./cleanup-local-data
sudo systemctl list-timers messenger-retention.timer --no-pager
```

When the listener holds the lock, the command leaves cleanup to it. Powering
off the VM pauses cleanup; startup removes overdue content after it resumes.

Keep the server clock synchronized. A wrong clock affects both event admission
and retention boundaries.
