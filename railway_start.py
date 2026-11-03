import json
import os
from pathlib import Path
import runpy


def parse_cookies(raw):
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise ValueError('FB_SESSION_JSON must contain valid JSON.') from None
    if isinstance(value, list):
        if not all(isinstance(c, dict) and isinstance(c.get('name'), str)
                   and isinstance(c.get('value'), str) for c in value):
            raise ValueError('Cookie array entries need string name and value fields.')
        value = {c['name']: c['value'] for c in value}
    if not isinstance(value, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in value.items()
    ):
        raise ValueError('Cookies must be a name/value object or browser-export array.')
    if not value.get('c_user') or not value.get('xs'):
        raise ValueError('Cookies must include non-empty c_user and xs.')
    return value


def main():
    raw = os.environ.get('FB_SESSION_JSON', '').strip()
    if not raw:
        raise ValueError('FB_SESSION_JSON is required. No other login method is enabled.')
    cookies = parse_cookies(raw)
    data = Path(os.environ.get('DATA_DIR', '/data')).resolve()
    data.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    # Explicit variable always takes precedence over previously saved cookies.
    target = data / 'session.json'
    target.write_text(json.dumps(cookies), encoding='utf-8')
    target.chmod(0o600)
    os.chdir(data)
    runpy.run_path(str(Path(__file__).resolve().with_name('main.py')), run_name='__main__')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print(str(exc), flush=True)
        raise SystemExit(1)
