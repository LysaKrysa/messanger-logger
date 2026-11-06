"""Real Chromium isolation/input smoke check. Never opens Facebook or Telegram."""
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import ssl
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.browser_runtime import DockerBrowser
from dashboard.browser_proxy import Connect, Proxy


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        self.server.seen.add((self.headers.get("Host"), path))
        content_type = "text/html"
        if path == "/":
            body = b"""<title>Offline HTTPS proxy fixture</title><h1>Synthetic local egress test</h1>
<script>
globalThis.syntheticChallenge = {script:false, frame:false, alternative:false};
globalThis.syntheticUnrelated = false;
addEventListener('message', event => {
  if (event.origin === 'https://recaptcha.google.com' && event.data === 'SYNTHETIC_FRAME_READY') syntheticChallenge.frame = true;
});
</script>
<script src="https://www.google.com/recaptcha/enterprise.js"></script>
<script src="https://www.recaptcha.net/recaptcha/api.js"></script>
<script src="https://www.google.com/search"></script>
<script src="https://www.gstatic.com/unrelated.js"></script>
<script src="https://unlisted.gstatic.com/recaptcha/unrelated.js"></script>"""
        elif path == "/recaptcha/enterprise.js":
            content_type = "application/javascript"
            body = b"syntheticChallenge.script = true; const frame = document.createElement('iframe'); frame.src = 'https://recaptcha.google.com/recaptcha/enterprise/anchor'; document.body.appendChild(frame);"
        elif path == "/recaptcha/api.js":
            content_type = "application/javascript"
            body = b"syntheticChallenge.alternative = true;"
        elif path == "/recaptcha/enterprise/anchor":
            body = b'<h1>Synthetic verification frame</h1><script src="https://www.gstatic.com/recaptcha/releases/offline/fixture.js"></script>'
        elif path == "/recaptcha/releases/offline/fixture.js":
            content_type = "application/javascript"
            body = b"const image = new Image(); image.onload = () => parent.postMessage('SYNTHETIC_FRAME_READY', 'https://fixture.facebook.com'); image.src = 'https://www.google.com/recaptcha/api2/payload'; document.body.appendChild(image);"
        elif path == "/recaptcha/api2/payload":
            content_type = "image/gif"
            body = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7")
        else:
            content_type = "application/javascript"
            body = b"globalThis.syntheticUnrelated = true;"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    with tempfile.TemporaryDirectory(prefix="messenger-browser-check-") as directory:
        path = Path(directory) / "egress.sock"
        certificate, key = Path(directory) / "fixture.crt", Path(directory) / "fixture.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=fixture.facebook.com", "-keyout", str(key), "-out", str(certificate)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
        fixture.seen = set()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(certificate, key)
        fixture.socket = tls.wrap_socket(fixture.socket, server_side=True)
        threading.Thread(target=fixture.serve_forever, daemon=True).start()
        with Proxy(str(path), Connect) as proxy:
            os.chown(path, 10001, 10001)
            os.chmod(path, 0o600)
            threading.Thread(target=proxy.serve_forever, daemon=True).start()
            browser = DockerBrowser(uuid.uuid4().hex, time.time() + 120, proxy_path=path, policy_path=Path(__file__).resolve().with_name("seccomp.json"), offline_check=True)
            try:
                result = browser.call("smoke")
                assert result["sandboxed"] and result["title"] == "Offline browser fixture"
                browser.call("click", {"x": 50, "y": 15})
                browser.call("text", {"text": "OFFLINE_SYNTHETIC_INPUT"})
                assert browser.call("smoke-check")["input"] == "OFFLINE_SYNTHETIC_INPUT"
                browser.call("input", {"events": [
                    {"action": "key", "key": "Control+A"},
                    {"action": "text", "text": "BATCHED_SYNTHETIC_INPUT"},
                ]})
                assert browser.call("smoke-check")["input"] == "BATCHED_SYNTHETIC_INPUT"
                assert "jpeg" not in browser.call("frame", {"metadata_only": True})
                frame = browser.call("frame")
                assert base64.b64decode(frame["jpeg"]).startswith(b"\xff\xd8") and not frame["ready"]
                timings = []
                for _ in range(12):
                    began = time.monotonic()
                    browser.call("frame")
                    timings.append(time.monotonic() - began)
                assert sum(timings) < 3, "Direct viewport capture did not sustain five frames per second."
                print("Offline direct-capture median: {:.0f} ms; slowest: {:.0f} ms.".format(statistics.median(timings) * 1000, max(timings) * 1000))
                inspected = subprocess.run(["docker", "inspect", browser.name], check=True, capture_output=True)
                info = json.loads(inspected.stdout)[0]
                host = info["HostConfig"]
                assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
                assert host["LogConfig"]["Type"] == "none" and not host["PortBindings"]
                assert info["Config"]["User"] == "10001:10001" and host["Memory"] == 1024 ** 3
                assert host["CapDrop"] == ["ALL"] and len(info["Mounts"]) == 1 and not info["Mounts"][0]["RW"]
                # Explicit test-only DNS replacement: the real browser crosses
                # its local tunnel and mounted Unix socket to this host fixture.
                # The production resolver never permits loopback addresses.
                with patch("dashboard.browser_proxy.public_addresses", return_value=[(socket.AF_INET, ("127.0.0.1", fixture.server_port))]):
                    network = browser.call("smoke-network")
                    assert network["title"] == "Offline HTTPS proxy fixture"
                    assert network["challenge_loaded"] and network["unrelated_blocked"]
                    for request in (
                        ("www.google.com", "/recaptcha/enterprise.js"),
                        ("recaptcha.google.com", "/recaptcha/enterprise/anchor"),
                        ("www.gstatic.com", "/recaptcha/releases/offline/fixture.js"),
                        ("www.google.com", "/recaptcha/api2/payload"),
                        ("www.recaptcha.net", "/recaptcha/api.js"),
                    ):
                        assert request in fixture.seen, "A synthetic verification dependency did not traverse the private proxy."
                    for request in (("www.google.com", "/search"), ("www.gstatic.com", "/unrelated.js"), ("unlisted.gstatic.com", "/recaptcha/unrelated.js")):
                        assert request not in fixture.seen, "An unrelated Google request was allowed."
            finally:
                browser.close()
                proxy.shutdown()
                fixture.shutdown()
                fixture.server_close()
    print("Sandboxed browser/input/isolated HTTPS proxy and synthetic cross-domain verification frame checks passed. No live CAPTCHA, Facebook login or Telegram send.")


if __name__ == "__main__":
    main()
