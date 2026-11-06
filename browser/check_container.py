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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.browser_runtime import DockerBrowser
from dashboard.browser_proxy import Connect, Proxy


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        body = b"<title>Offline HTTPS proxy fixture</title><h1>Synthetic local egress test</h1>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    with tempfile.TemporaryDirectory(prefix="messenger-browser-check-") as directory:
        path = Path(directory) / "egress.sock"
        certificate, key = Path(directory) / "fixture.crt", Path(directory) / "fixture.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=fixture.facebook.com", "-keyout", str(key), "-out", str(certificate)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
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
                    assert browser.call("smoke-network")["title"] == "Offline HTTPS proxy fixture"
            finally:
                browser.close()
                proxy.shutdown()
                fixture.shutdown()
                fixture.server_close()
    print("Sandboxed browser/input/isolated HTTPS proxy smoke check passed. No Facebook login or Telegram send.")


if __name__ == "__main__":
    main()
