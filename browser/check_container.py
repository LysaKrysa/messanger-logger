"""Real Chromium isolation/input smoke check. Never opens Facebook or Telegram."""
import base64
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard.browser_runtime import DockerBrowser


def main():
    with tempfile.TemporaryDirectory(prefix="messenger-browser-check-") as directory:
        path = Path(directory) / "egress.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as proxy:
            proxy.bind(str(path))
            browser = DockerBrowser(uuid.uuid4().hex, time.time() + 120, proxy_path=path, policy_path=Path(__file__).resolve().with_name("seccomp.json"), offline_check=True)
            try:
                result = browser.call("smoke")
                assert result["sandboxed"] and result["title"] == "Offline browser fixture"
                browser.call("click", {"x": 50, "y": 15})
                browser.call("text", {"text": "OFFLINE_SYNTHETIC_INPUT"})
                assert browser.call("smoke-check")["input"] == "OFFLINE_SYNTHETIC_INPUT"
                frame = browser.call("frame")
                assert base64.b64decode(frame["jpeg"]).startswith(b"\xff\xd8") and not frame["ready"]
                inspected = subprocess.run(["docker", "inspect", browser.name], check=True, capture_output=True)
                info = json.loads(inspected.stdout)[0]
                host = info["HostConfig"]
                assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"]
                assert host["LogConfig"]["Type"] == "none" and not host["PortBindings"]
                assert info["Config"]["User"] == "10001:10001" and host["Memory"] == 1024 ** 3
                assert host["CapDrop"] == ["ALL"] and len(info["Mounts"]) == 1 and not info["Mounts"][0]["RW"]
            finally:
                browser.close()
    print("Sandboxed browser/input smoke check passed. No Facebook login or Telegram send.")


if __name__ == "__main__":
    main()
