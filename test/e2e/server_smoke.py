"""Smoke test of the REAL server process: start ``backend/main.py``, probe it, stop it gracefully.

* launches ``backend\\python\\python.exe backend\\main.py --no-preload --port <port>`` in a new process group,
* waits for ``GET /api/v1/health``, prints ``GET /api/v1/system`` (engine state/error, device) and checks the
  WebSocket ``hello``,
* sends CTRL+BREAK (uvicorn's graceful shutdown on Windows) and verifies a clean exit + "Shutdown complete".

Runtime data and logs go to ``test/.runtime/server-smoke`` and ``test/.logs/server-smoke``. By default the child
runs with ``IMG3D_AUTH_ENABLED=false`` so no credentials are needed (the server binds to 127.0.0.1 only).

    backend\\python\\python.exe test\\e2e\\server_smoke.py [--port 8765] [--keep-auth] [--preload]
"""

from __future__ import annotations

import os
import site
import sys

_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != _USER_SITE]

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import httpx  # noqa: E402
from websockets.asyncio.client import connect as ws_connect  # noqa: E402

TEST_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = TEST_ROOT.parent
PYTHON = PROJECT_ROOT / "backend" / "python" / "python.exe"
MAIN = PROJECT_ROOT / "backend" / "main.py"
RUNTIME_DIR = TEST_ROOT / ".runtime" / "server-smoke"
LOG_DIR = TEST_ROOT / ".logs" / "server-smoke"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Img3D real-server smoke test")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--startup-timeout", type=float, default=180.0)
    parser.add_argument("--keep-auth", action="store_true", help="do not force IMG3D_AUTH_ENABLED=false")
    parser.add_argument("--preload", action="store_true", help="start without --no-preload")
    return parser.parse_args()


async def ws_hello(port: int) -> dict:
    async with ws_connect(f"ws://127.0.0.1:{port}/api/v1/ws", open_timeout=10) as ws:
        return json.loads(await asyncio.wait_for(ws.recv(), timeout=10))


def main() -> int:
    args = parse_args()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["IMG3D_RUNTIME_DIR"] = str(RUNTIME_DIR)
    env["IMG3D_LOG_DIR"] = str(LOG_DIR)
    env["NO_COLOR"] = "1"
    if not args.keep_auth:
        env["IMG3D_AUTH_ENABLED"] = "false"
    command = [str(PYTHON), str(MAIN), "--port", str(args.port)]
    if not args.preload:
        command.append("--no-preload")

    console_log = LOG_DIR / "console.txt"
    base = f"http://127.0.0.1:{args.port}"
    print(f"Starting: {' '.join(command)}")
    with open(console_log, "w", encoding="utf-8") as out:
        proc = subprocess.Popen(
            command,
            cwd=str(TEST_ROOT),  # deliberately not the backend dir: paths must not depend on the CWD
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        ok = True
        try:
            started = time.monotonic()
            while True:
                if proc.poll() is not None:
                    raise RuntimeError(f"server exited early with code {proc.returncode}")
                try:
                    response = httpx.get(f"{base}/api/v1/health", timeout=2)
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() - started > args.startup_timeout:
                    raise RuntimeError("server did not become healthy in time")
                time.sleep(0.5)
            print(f"health ({time.monotonic() - started:.1f}s): {response.json()}")

            system = httpx.get(f"{base}/api/v1/system", timeout=15)
            print(f"system: HTTP {system.status_code}")
            if system.status_code == 200:
                body = system.json()
                engine = body["engine"]
                print(f"  engine: {engine['name']} state={engine['state']} message={engine['message']!r}")
                if engine.get("error"):
                    print(f"  engine error: {engine['error']}")
                print(f"  capabilities: {json.dumps(engine['capabilities'])}")
                print(f"  device: {json.dumps(body['device'])}")
                print(f"  queue: {body['queue']}  auth_required: {body['auth_required']}")
                hello = asyncio.run(ws_hello(args.port))
                print(f"  websocket: {hello['type']} (engine state {hello['data']['system']['engine']['state']})")
                # The first device_info() call may import torch; it is served from cache once available.
                deadline = time.monotonic() + 30
                device = body["device"]
                while device is None and time.monotonic() < deadline:
                    time.sleep(1.0)
                    device = httpx.get(f"{base}/api/v1/system", timeout=15).json()["device"]
                print(f"  device (after warm-up): {json.dumps(device)}")
            else:
                print(f"  body: {system.text[:300]}")
                ok = system.status_code == 401 and args.keep_auth
        except Exception as exc:  # noqa: BLE001
            print(f"SMOKE TEST FAILED: {exc}")
            ok = False
        finally:
            if proc.poll() is None:
                print("Stopping server with CTRL+BREAK (graceful shutdown)...")
                try:
                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                    proc.wait(timeout=60)
                except (OSError, subprocess.TimeoutExpired, AttributeError) as exc:
                    print(f"graceful stop failed ({exc!r}); terminating")
                    proc.kill()
                    proc.wait(timeout=30)
                    ok = False
            print(f"server exit code: {proc.returncode}")

    log_text = console_log.read_text(encoding="utf-8", errors="replace")
    graceful = "Shutdown complete" in log_text
    print(f"graceful shutdown logged: {graceful}")
    print(f"console log: {console_log}")
    print("--- last console lines ---")
    print("\n".join(log_text.strip().splitlines()[-15:]))
    return 0 if ok and graceful else 1


if __name__ == "__main__":
    raise SystemExit(main())
