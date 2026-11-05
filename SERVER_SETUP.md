# Unattended Ubuntu server setup

[← README](README.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) · [Storage](docs/STORAGE.md)

The service runs on the server after SSH closes or your PC shuts down, and starts
again at boot. A separate timer expires local content while capture is stopped.
The VM and Docker must be running for either process to execute.

## 1. Prepare the host

Use an Ubuntu host with Docker, Git, Python 3, and systemd. Follow the
[quick start](README.md#quick-start), build the image, and test in the foreground.

| Default path | Purpose |
| :--- | :--- |
| `~/messenger-modern/` | Source checkout |
| `~/messenger.env` | Private settings |
| `~/messenger-data/` | Persistent data |

Let an in-progress build finish before replacing code. No public domain,
reverse proxy, database server, or incoming application port is required.

## 2. Stop the foreground test

Press **Ctrl+C in the listener's terminal**. If that terminal is unavailable:

```bash
sudo docker stop --time 30 messenger-logger-modern-test
```

A `No such container` response means it is already gone. Do not run two listeners
against the same data directory.

### Container name already exists

The installer refuses an unmanaged container named `messenger-logger`.
Verify it is your previous logger and inspect its mount locations locally:

```bash
sudo docker inspect --format '{{json .Mounts}}' messenger-logger
```

Confirm the archive is bind-mounted to the intended persistent host folder,
then stop/remove the old container:

```bash
sudo docker stop --time 30 messenger-logger
sudo docker rm messenger-logger
```

If `rm` says `No such container`, an old `--rm` container already removed itself.
The bind-mounted host folder is preserved. The installer has not installed units
until this conflict is resolved, so `Unit ... could not be found` is expected
at that point.

## 3. Install the service and timer

```bash
cd ~/messenger-modern
sudo bash server/install-service.sh
```

The installer checks the image/native RPC without login, protects the env/data
paths, enables Docker at boot, and installs:

| Unit | Purpose |
| :--- | :--- |
| `messenger-logger.service` | Container supervised by systemd |
| `messenger-retention.service` | Offline cleanup without account credentials |
| `messenger-retention.timer` | Cleanup checks about every five minutes |

Systemd supervises the container without a competing Docker restart policy.
All source is mounted read-only at `/app`; persistent data is mounted at `/data`.

For custom paths/image, use overrides. Paths may contain letters, digits, dots,
underscores, hyphens, and slashes:

```bash
sudo env \
  LOGGER_DATA_DIR=/srv/messenger-data \
  LOGGER_ENV_FILE=/srv/messenger.env \
  LOGGER_IMAGE=messenger-logger:modern \
  bash server/install-service.sh
```

If the native self-test requests a rebuild:

```bash
sudo docker build -t messenger-logger:modern .
sudo bash server/install-service.sh
```

## 4. Verify connection and retention

```bash
sudo systemctl status messenger-logger --no-pager
sudo journalctl -u messenger-logger -f
```

Wait for `Messenger listener ready (regular and encrypted)`. Ask another person
to send you a fresh message and then unsend it after capture. Your own outgoing
messages are excluded. Inspect the incoming message in another SSH session:

```bash
cd ~/messenger-modern
sudo ./show-messages
sudo ./show-deleted --run all
sudo systemctl list-timers messenger-retention.timer --no-pager
```

**Ctrl+C in `journalctl -f` closes only the log viewer.** You can close SSH;
the server keeps running.

## Routine controls

| Action | Command |
| :--- | :--- |
| Status | `sudo systemctl status messenger-logger --no-pager` |
| Recent logs | `sudo journalctl -u messenger-logger -n 60 --no-pager` |
| Restart capture | `sudo systemctl restart messenger-logger` |
| Stop capture | `sudo systemctl stop messenger-logger` |
| Start capture | `sudo systemctl start messenger-logger` |
| Retention results | `sudo journalctl -u messenger-retention -n 20 --no-pager` |
| Telegram setup | `sudo ./check-telegram-topics` from the source directory |
| RAM/CPU usage | `sudo docker stats --no-stream messenger-logger` |
| Free disk space | `df -h ~/messenger-data` |

Stopping capture leaves the retention timer active.

### Cookie refresh and failures

Refresh cookies in your own browser and edit `~/messenger.env` privately.
Do not put cookies into shell arguments or support logs. Then:

```bash
sudo systemctl reset-failed messenger-logger
sudo systemctl restart messenger-logger
```

Temporary failures restart after 60 seconds, with at most five starts in a
15-minute window. Diagnose repeated failures before resetting that limit.
Auth/checkpoint errors, bad settings, account mismatches, incompatible binaries,
and storage failures stop with status 78 and wait for repair.

### Updating source

Stop capture before replacing mounted files:

```bash
sudo systemctl stop messenger-logger
cd ~/messenger-modern
git pull --ff-only
sudo systemctl start messenger-logger
```

Python-only changes can reuse an image with the required native bridge.
Native changes, checksum changes, or new runtime dependencies require rebuilding
before restart. Rerun the installer when unit templates change.

## Cleanup while stopped

```bash
cd ~/messenger-modern
sudo ./cleanup-local-data
```

This makes no Facebook login or Telegram requests. If the listener holds the
data lock, cleanup is left to that listener. The timer uses the same mechanism.
After a VM outage, startup removes overdue content.

Local retention is 72 hours from capture. Telegram copies remain intentionally.
Cookies, encryption-device state, and numeric topic mappings remain for operation.
External backups/snapshots need their own policy.

## Capacity and validation

A personal listener is usually a small workload, but RAM use and downloaded media
determine capacity. The Go build needs more resources than normal operation.
Monitor your deployment and check current provider quotas/eligibility rather
than treating a free tier as an uptime guarantee.

Actual service installation, reboot recovery, and live authentication must be
verified on the target server. Development checks did not have access to a
running systemd manager or Docker daemon; see [VALIDATION.md](VALIDATION.md).
