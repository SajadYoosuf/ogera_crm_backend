"""Run the Flutter app's end-to-end test against a real, throwaway backend.

    python tests/run_mobile_e2e.py            # from ogera_crm_backend

Starts tests/e2e_server.py (SQLite, seeded), waits for /health, runs
`flutter test test/e2e` in ../ogera_crm_mobile with E2E_API_URL set, then stops
the server. Exits with Flutter's exit code.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
MOBILE = BACKEND.parent / "ogera_crm_mobile"
PORT = int(os.environ.get("E2E_PORT", "8765"))
URL = f"http://127.0.0.1:{PORT}"


def wait_for_server(server: subprocess.Popen, seconds: int = 60) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        if server.poll() is not None:
            raise SystemExit(f"E2E server exited early with code {server.returncode}")
        try:
            with urllib.request.urlopen(f"{URL}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.5)
    raise SystemExit("E2E server did not become healthy in time")


def main() -> int:
    flutter = shutil.which("flutter") or shutil.which("flutter.bat")
    if not flutter:
        raise SystemExit("flutter is not on PATH")
    log_path = Path(tempfile.gettempdir()) / "ogera_mobile_e2e_server.log"
    log = open(log_path, "w", encoding="utf-8")
    server = subprocess.Popen(
        [sys.executable, "-X", "faulthandler", str(BACKEND / "tests" / "e2e_server.py"), "--port", str(PORT)],
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    try:
        wait_for_server(server)
        env = {**os.environ, "E2E_API_URL": URL}
        result = subprocess.run(
            [flutter, "test", "test/e2e", "--reporter", "expanded"], cwd=MOBILE, env=env
        )
        if server.poll() is not None:
            print(f"The E2E server stopped early (exit code {server.returncode}); its log is {log_path}")
        return result.returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
