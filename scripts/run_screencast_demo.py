"""Verify the demo, launch it, and save a real Playwright WebM recording."""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    for command in ([sys.executable, str(ROOT / "scripts/verify_contracts.py")],
                    [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]):
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode:
            print(f"Verification failed: {command}", file=sys.stderr)
            return result.returncode
    node = shutil.which("node")
    if not node:
        print("Node.js/Playwright is required to record video", file=sys.stderr)
        return 2
    environment = os.environ.copy()
    environment["QC_DEMO_VIEWER_TOKEN"] = secrets.token_urlsafe(24)
    environment["QC_DEMO_CONTROLLER_TOKEN"] = secrets.token_urlsafe(24)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        environment["QC_PORT"] = str(probe.getsockname()[1])
    url = f"http://127.0.0.1:{environment['QC_PORT']}"
    environment["QC_BASE_URL"] = url
    server = subprocess.Popen([sys.executable, "-m", "qc.demo"], cwd=ROOT, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    local_http = build_opener(ProxyHandler({}))
    try:
        viewer_line = next((line for line in iter(server.stdout.readline, "") if line.startswith("Viewer token:")), None)
        if viewer_line is None or viewer_line.partition(": ")[2].strip() != environment["QC_DEMO_VIEWER_TOKEN"]:
            raise RuntimeError("demo did not use the requested viewer token")
        for _ in range(50):
            if server.poll() is not None:
                print(server.stderr.read(), file=sys.stderr)
                return 3
            try:
                with local_http.open(url + "/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        else:
            print("Demo server did not become healthy", file=sys.stderr)
            return 4
        with local_http.open(Request(url + "/api/me", headers={
            "Authorization": "Bearer " + environment["QC_DEMO_VIEWER_TOKEN"]}), timeout=2) as response:
            if response.status != 200:
                raise RuntimeError("demo viewer token rejected")
        result = subprocess.run([node, str(ROOT / "scripts/record_screencast.js")],
                                cwd=ROOT, env=environment, check=False)
        if result.returncode:
            return result.returncode
        smoke = subprocess.run([node, str(ROOT / "tests/browser_smoke.js")],
                               cwd=ROOT, env=environment, check=False)
        return smoke.returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        server.stderr.close()
        server.stdout.close()


if __name__ == "__main__":
    raise SystemExit(main())
