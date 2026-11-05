"""Launch the packaged app against synthetic data and require HTTP readiness."""
from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def executable() -> Path:
    if sys.platform == "darwin":
        return ROOT / "dist/LungLabelTools.app/Contents/MacOS/LungLabelTools"
    if sys.platform == "win32":
        return ROOT / "dist/LungLabelTools.exe"
    return ROOT / "dist/LungLabelTools/LungLabelTools"


def main() -> None:
    binary = executable()
    if not binary.is_file():
        raise RuntimeError("BINARY_MISSING")
    diagnostic = subprocess.run([str(binary), "--diagnose"], capture_output=True,
                                text=True, timeout=40, check=True)
    caps = json.loads(diagnostic.stdout.splitlines()[-1])
    assert caps["platform"] == platform.system()
    assert caps["server_cuda_acceleration"] is False
    with tempfile.TemporaryDirectory(prefix="lung-label-release-") as temp:
        home = Path(temp)
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["XDG_DATA_HOME"] = str(home)
        env["LOCALAPPDATA"] = str(home)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        proc = subprocess.Popen([str(binary), "--demo", "--port", str(port), "--no-browser"],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            url = f"http://127.0.0.1:{port}/"
            for _ in range(100):
                if proc.poll() is not None:
                    raise RuntimeError("PACKAGED_APP_EXITED_EARLY")
                try:
                    with urllib.request.urlopen(url, timeout=1) as response:
                        if response.status == 200:
                            print("PACKAGED_SYNTHETIC_HTTP_PASS")
                            return
                except Exception:
                    time.sleep(0.3)
            raise RuntimeError("PACKAGED_APP_HTTP_TIMEOUT")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
