#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then
  echo 'Run this installer with sudo bash.' >&2
  exit 1
fi
LOGGER_CODE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
LOGGER_DATA_DIR="${LOGGER_DATA_DIR:-$(dirname -- "$LOGGER_CODE_DIR")/messenger-data}"
LOGGER_ENV_FILE="${LOGGER_ENV_FILE:-$(dirname -- "$LOGGER_CODE_DIR")/messenger.env}"
LOGGER_IMAGE="${LOGGER_IMAGE:-messenger-logger:modern}"
LOGGER_DOCKER="$(command -v docker)"
for LOGGER_PATH in "$LOGGER_CODE_DIR" "$LOGGER_DATA_DIR" "$LOGGER_ENV_FILE" "$LOGGER_DOCKER"; do
  if [[ ! "$LOGGER_PATH" =~ ^/[A-Za-z0-9_./-]+$ ]]; then
    echo 'Use absolute paths containing letters, digits, dots, underscores, slashes, and hyphens.' >&2
    exit 1
  fi
done
if [[ ! "$LOGGER_IMAGE" =~ ^[A-Za-z0-9_./:@-]+$ ]]; then
  echo 'Invalid Docker image reference.' >&2
  exit 1
fi
if [[ ! -f "$LOGGER_ENV_FILE" ]]; then
  echo 'The existing messenger.env file is required.' >&2
  exit 1
fi
systemctl enable --now docker.service
docker image inspect "$LOGGER_IMAGE" >/dev/null
docker run --rm --log-driver none \
  --mount "type=bind,source=$LOGGER_CODE_DIR,target=/app,readonly" \
  --entrypoint python "$LOGGER_IMAGE" /app/logger.py --self-test
if docker container inspect messenger-logger-modern-test >/dev/null 2>&1; then
  echo 'Stop the foreground messenger-logger-modern-test container first (Ctrl+C in its terminal).' >&2
  exit 1
fi
if systemctl is-active --quiet messenger-logger.service; then
  systemctl stop messenger-logger.service
elif docker container inspect messenger-logger >/dev/null 2>&1; then
  echo 'A container named messenger-logger already exists outside this service. Stop/remove it first.' >&2
  exit 1
fi
install -d -m 700 "$LOGGER_DATA_DIR"
chmod 600 "$LOGGER_ENV_FILE"
python3 - "$LOGGER_CODE_DIR" "$LOGGER_DATA_DIR" "$LOGGER_ENV_FILE" "$LOGGER_IMAGE" "$LOGGER_DOCKER" <<'PY'
from pathlib import Path
import sys
code, data, env_file, image, docker = sys.argv[1:]
values = {'@CODE_DIR@': code, '@DATA_DIR@': data, '@ENV_FILE@': env_file, '@IMAGE@': image, '@DOCKER@': docker}
base = Path(code) / 'server'
for name in ('messenger-logger.service', 'messenger-retention.service'):
    content = (base / (name + '.in')).read_text()
    for key, value in values.items():
        content = content.replace(key, value)
    target = Path('/etc/systemd/system') / name
    target.write_text(content)
    target.chmod(0o644)
target = Path('/etc/systemd/system/messenger-retention.timer')
target.write_text((base / target.name).read_text())
target.chmod(0o644)
PY
systemctl daemon-reload
systemctl enable --now messenger-retention.timer
systemctl enable --now messenger-logger.service
echo 'Installed. The server now runs the logger independently of SSH or your PC.'
echo 'Check: sudo systemctl status messenger-logger --no-pager'
echo 'Logs:  sudo journalctl -u messenger-logger -f'
echo 'Timer: sudo systemctl list-timers messenger-retention.timer'
