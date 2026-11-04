# Troubleshooting

[← README](../README.md) · [Server setup](../SERVER_SETUP.md) · [Telegram](TELEGRAM.md)

## Start with safe operational diagnostics

```bash
sudo systemctl status messenger-logger --no-pager
sudo journalctl -u messenger-logger -n 60 --no-pager
sudo systemctl list-timers messenger-retention.timer --no-pager
```

Review output before sharing it. Do not paste cookies, tokens, database files,
raw bridge output, full Docker inspect dumps, or real conversation content.

## Common problems

| Symptom | Likely next step |
| :--- | :--- |
| `Unit messenger-logger.service could not be found` | The installer has not completed. Resolve its printed error, then rerun it. |
| Existing container named `messenger-logger` | Follow the stop/remove steps in the server guide after verifying persistent mounts. |
| Required cookie missing or invalid JSON | Correct the private `messenger.env`; use `--check-config` before live startup. |
| Authentication/checkpoint failure | Refresh your own browser session and resolve any checkpoint manually. |
| Status 78 / service does not keep retrying | Repair the displayed configuration/account/storage/native problem, then reset/start the service. |
| Start request repeated too quickly | Diagnose repeated failures, then `systemctl reset-failed` and restart. |
| Bridge checksum/version mismatch | Build the matching source/toolchain. Do not disable verification. |
| Native Go modules still downloading | First builds need network and time; a running compiler can also be quiet. Check for a real error before interrupting. |
| Logger ready but no old messages appear | History and pre-startup messages are deliberately excluded. Test a new message. |
| Encrypted messages unavailable | Confirm `LOGGER_E2EE=true`, encrypted readiness, and persistent device state. |
| Incoming/outgoing or group event missing | Test a fresh event after readiness; inspect safe transport diagnostics. Unsupported formats/offline gaps are possible. |
| Sender shown as unknown | Name resolution is best effort; the placeholder is not treated as a real username. |
| Deleted view has no content | The message was not captured before deletion or did not provide supported content. |
| Attachment failed or interrupted | Inspect the local status; keys are not persisted for retry after restart. |
| Empty view-once content | No usable transport/download information was received; existing empty records cannot recover bytes. |
| No Telegram notices in deleted mode | Capture and then unsend a fresh message; ordinary messages alone are not forwarded in this mode. |
| Threaded mode still appears disabled | Run `check-telegram-topics`; compare the bot username and both independent settings. |
| Unconfirmed topic | Bind the already-created topic with `manage-topics`; see the Telegram guide. |
| Logs disappear after restart | Verify the persistent `/data` bind mount; also account for 72-hour expiry. |
| Cleanup delayed | VM/Docker must be running. Check the timer and whether the active listener owns cleanup. |

## Restart after repair

```bash
sudo systemctl reset-failed messenger-logger
sudo systemctl restart messenger-logger
sudo journalctl -u messenger-logger -f
```

A temporary network failure may recover natively before the application restart
deadline. Repeated auth/checkpoint failures are not solved by an automatic login
loop. The cleanup timer does not require valid Facebook or Telegram credentials.

## What tests establish

Offline tests use synthetic events and mocked Telegram responses. A native
smoke test verifies local RPC and the pinned binary, without logging in.
Neither proves live authentication, universal conversation coverage, attachment
quality, or successful server deployment.

See [VALIDATION.md](../VALIDATION.md) for tested scope and [SECURITY.md](../SECURITY.md)
for safe issue reports.
