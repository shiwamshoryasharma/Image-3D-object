"""End-to-end generation against a RUNNING Img3D server (real engine), over HTTP + WebSocket.

Creates a reference set, uploads image(s), creates a job, streams its progress over the WebSocket (falls back
to polling), downloads the GLB (+ preview + result JSON) to ``test/outputs/e2e/`` and prints mesh statistics.

Usage (embedded interpreter; start the server first with ``backend\\python\\python.exe backend\\main.py``)::

    backend\\python\\python.exe test\\e2e\\e2e_generate.py
    backend\\python\\python.exe test\\e2e\\e2e_generate.py --image test\\images\\ANIME_CHARACTER_01\\Alice.png \\
        --resolution 1536 --texture-size 4096 --polygon-target 800000
    backend\\python\\python.exe test\\e2e\\e2e_generate.py --ref back=test\\images\\ALICE\\ALICE_full_back_view.png

Credentials (only when the server has auth enabled): ``--username/--password`` or the environment variables
``IMG3D_E2E_USERNAME`` / ``IMG3D_E2E_PASSWORD``. Exit code 0 when the job completed, 1 otherwise.
"""

from __future__ import annotations

import os
import site
import sys

# The embedded interpreter also searches the per-user site-packages first; use only the embedded packages.
_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p or ".")) != _USER_SITE]

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import mimetypes  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402
from urllib.parse import urlencode, urlsplit, urlunsplit  # noqa: E402

import httpx  # noqa: E402
import websockets  # noqa: E402
from websockets.asyncio.client import connect as ws_connect  # noqa: E402

TEST_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGE = TEST_ROOT / "images" / "ALICE" / "ALICE_full_front_view.png"
DEFAULT_OUTPUT = TEST_ROOT / "outputs" / "e2e"
TERMINAL = {"completed", "failed", "cancelled"}
SLOTS = ("front", "back", "left", "right", "face_front", "face_side", "detail")


class E2EError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Img3D end-to-end generation client")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="server base URL")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE, help="primary reference image")
    parser.add_argument("--slot", default="front", choices=SLOTS, help="slot of --image")
    parser.add_argument(
        "--ref", action="append", default=[], metavar="SLOT=PATH", help="additional reference image (repeatable)"
    )
    parser.add_argument("--resolution", type=int, default=1024, choices=(512, 1024, 1536))
    parser.add_argument("--texture-size", type=int, default=2048, choices=(512, 1024, 2048, 4096))
    parser.add_argument("--polygon-target", type=int, default=500_000)
    parser.add_argument("--seed", type=int, default=None, help="fixed seed (default: server-side random)")
    parser.add_argument("--no-remove-background", action="store_true")
    parser.add_argument("--username", default=os.environ.get("IMG3D_E2E_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("IMG3D_E2E_PASSWORD"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=3600.0, help="max seconds to wait for the job")
    parser.add_argument("--keep-set", action="store_true", help="do not delete the reference set afterwards")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------------------------------


def _raise_for_error(response: httpx.Response, what: str) -> None:
    if response.is_success:
        return
    try:
        error = response.json().get("error", {})
        detail = f"{error.get('code')}: {error.get('message')} (details={error.get('details')})"
    except ValueError:
        detail = response.text[:500]
    raise E2EError(f"{what} failed with HTTP {response.status_code} - {detail}")


async def authenticate(client: httpx.AsyncClient, args: argparse.Namespace) -> str | None:
    status = (await client.get("/api/v1/auth/status")).json()
    if not status["auth_required"]:
        return None
    if not args.username or not args.password:
        raise E2EError("The server requires authentication: pass --username/--password")
    response = await client.post("/api/v1/auth/login", json={"username": args.username, "password": args.password})
    _raise_for_error(response, "Login")
    token = response.json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    print("Authenticated.")
    return token


async def upload_image(client: httpx.AsyncClient, set_id: str, path: Path, slot: str, primary: bool) -> dict:
    if not path.is_file():
        raise E2EError(f"Image not found: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with open(path, "rb") as fh:
        response = await client.post(
            f"/api/v1/reference-sets/{set_id}/images",
            data={"slot": slot, "make_primary": "true" if primary else "false"},
            files={"file": (path.name, fh, mime)},
            timeout=120,
        )
    _raise_for_error(response, f"Upload of {path.name}")
    image = response.json()["image"]
    print(f"Uploaded {path.name} -> {slot} ({image['width']}x{image['height']}, alpha={image['has_alpha']})")
    return image


async def download(client: httpx.AsyncClient, url: str, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    async with client.stream("GET", url, timeout=600) as response:
        if not response.is_success:
            await response.aread()  # error bodies must be read before .json() on a streamed response
        _raise_for_error(response, f"Download of {url}")
        with open(destination, "wb") as fh:
            async for chunk in response.aiter_bytes(1024 * 1024):
                fh.write(chunk)
                size += len(chunk)
    return size


# --------------------------------------------------------------------------------------------------
# Progress
# --------------------------------------------------------------------------------------------------


class ProgressPrinter:
    def __init__(self) -> None:
        self._last: tuple[Any, ...] | None = None

    def show(self, job: dict) -> None:
        p = job["progress"]
        key = (job["status"], p["stage"], p.get("step"), round(p.get("percent") or 0.0), p.get("message"))
        if key == self._last:
            return
        self._last = key
        step = f" {p['step']}/{p['total_steps']}" if p.get("total_steps") else ""
        eta = f" eta {p['eta_seconds']:.0f}s" if p.get("eta_seconds") is not None else ""
        position = f" (queue position {job['queue_position']})" if job.get("queue_position") else ""
        print(
            f"[{time.strftime('%H:%M:%S')}] {job['status']:<9} {p['percent']:5.1f}%  "
            f"{p['stage_label']}{step}{eta} - {p.get('message') or ''}{position}",
            flush=True,
        )


def _ws_url(base_url: str, job_id: str, token: str | None) -> str:
    parts = urlsplit(base_url)
    scheme = "wss" if parts.scheme == "https" else "ws"
    query = {"job_id": job_id}
    if token:
        query["token"] = token
    return urlunsplit((scheme, parts.netloc, "/api/v1/ws", urlencode(query), ""))


async def fetch_job(client: httpx.AsyncClient, job_id: str) -> dict:
    response = await client.get(f"/api/v1/jobs/{job_id}")
    _raise_for_error(response, "Job status")
    return response.json()


async def follow_over_websocket(
    url: str, job_id: str, printer: ProgressPrinter, deadline: float, client: httpx.AsyncClient
) -> dict | None:
    """Stream ``job.updated`` events until the job is terminal. Returns the final job or ``None`` on WS failure.

    The job may already have finished before the socket connected (e.g. an immediate engine error), so the REST
    state is checked right after ``hello`` and again on every server ``ping`` (the REST API is the source of truth).
    """
    try:
        async with ws_connect(url, open_timeout=15, ping_interval=20, max_size=16 * 1024 * 1024) as ws:
            while time.monotonic() < deadline:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.monotonic()))
                message = json.loads(raw)
                if message["type"] in ("hello", "ping"):
                    job = await fetch_job(client, job_id)
                    printer.show(job)
                    if job["status"] in TERMINAL:
                        return job
                elif message["type"] == "job.updated" and message["data"]["id"] == job_id:
                    printer.show(message["data"])
                    if message["data"]["status"] in TERMINAL:
                        return message["data"]
                elif message["type"] == "system.updated":
                    engine = message["data"]["engine"]
                    if engine["state"] == "loading" and engine.get("message"):
                        print(f"  engine: {engine['message']}", flush=True)
    except (OSError, websockets.exceptions.WebSocketException, TimeoutError) as exc:
        print(f"WebSocket unavailable ({type(exc).__name__}: {exc}); falling back to polling", flush=True)
    return None


async def follow_by_polling(client: httpx.AsyncClient, job_id: str, printer: ProgressPrinter, deadline: float) -> dict:
    while time.monotonic() < deadline:
        job = await fetch_job(client, job_id)
        printer.show(job)
        if job["status"] in TERMINAL:
            return job
        await asyncio.sleep(2.0)
    raise E2EError("Timed out waiting for the job")


# --------------------------------------------------------------------------------------------------
# Main flow
# --------------------------------------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    started = time.monotonic()
    deadline = started + args.timeout
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60) as client:
        health = await client.get("/api/v1/health")
        _raise_for_error(health, "Health check")
        print(f"Server: {health.json()['app']} {health.json()['version']} at {args.base_url}")
        token = await authenticate(client, args)

        system = (await client.get("/api/v1/system")).json()
        engine = system["engine"]
        print(f"Engine: {engine['name']} state={engine['state']} {engine.get('message') or ''}")
        if engine.get("error"):
            print(f"  engine error: {engine['error']}")
        if system.get("device"):
            device = system["device"]
            print(f"Device: {device.get('name')} (VRAM {device.get('vram_free_mb')}/{device.get('vram_total_mb')} MB free)")

        response = await client.post("/api/v1/reference-sets", json={"name": f"e2e {time.strftime('%Y-%m-%d %H:%M:%S')}"})
        _raise_for_error(response, "Create reference set")
        set_id = response.json()["id"]
        job: dict | None = None
        try:
            await upload_image(client, set_id, args.image, args.slot, primary=True)
            for ref in args.ref:
                slot, _, path = ref.partition("=")
                if slot not in SLOTS or not path:
                    raise E2EError(f"Invalid --ref '{ref}' (expected SLOT=PATH with SLOT in {SLOTS})")
                await upload_image(client, set_id, Path(path), slot, primary=False)

            settings: dict[str, Any] = {
                "resolution": args.resolution,
                "texture_size": args.texture_size,
                "polygon_target": args.polygon_target,
                "remove_background": not args.no_remove_background,
                "randomize_seed": args.seed is None,
            }
            if args.seed is not None:
                settings["seed"] = args.seed
            response = await client.post("/api/v1/jobs", json={"reference_set_id": set_id, "settings": settings})
            _raise_for_error(response, "Create job")
            job = response.json()
            job_id = job["id"]
            print(f"Job {job_id} queued (seed {job['seed']}, settings {json.dumps(job['settings'])})")

            printer = ProgressPrinter()
            printer.show(job)
            try:
                final = await follow_over_websocket(
                    _ws_url(args.base_url, job_id, token), job_id, printer, deadline, client
                )
                if final is None:
                    final = await follow_by_polling(client, job_id, printer, deadline)
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("Interrupted - cancelling the job")
                await client.post(f"/api/v1/jobs/{job_id}/cancel")
                raise
            job = final
        finally:
            if not args.keep_set:
                await client.delete(f"/api/v1/reference-sets/{set_id}")

        assert job is not None
        if job["status"] != "completed":
            error = job.get("error") or {}
            print(f"\nJob {job['id']} {job['status']}: {error.get('code')} - {error.get('message')}")
            if error.get("details"):
                print(f"  details: {json.dumps(error['details'])}")
            return 1

        result = (await client.get(f"/api/v1/jobs/{job['id']}/result")).json()
        out_dir: Path = args.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        glb_path = out_dir / f"img3d_{job['id']}.glb"
        size = await download(client, result["download_url"], glb_path)
        with open(glb_path, "rb") as fh:
            if fh.read(4) != b"glTF":
                raise E2EError(f"Downloaded file is not a GLB: {glb_path}")
        if result.get("preview_url"):
            await download(client, result["preview_url"], out_dir / f"img3d_{job['id']}_preview.png")
        (out_dir / f"img3d_{job['id']}_result.json").write_text(
            json.dumps({"job": job, "result": result}, indent=2), encoding="utf-8"
        )

        stats = result["stats"]
        print("\nCompleted.")
        print(f"  GLB:         {glb_path} ({size / 1e6:.2f} MB)")
        print(f"  vertices:    {stats['vertices']:,}")
        print(f"  faces:       {stats['faces']:,}")
        print(f"  materials:   {stats['materials']}  textures: {stats['textures']}  texture_size: {stats['texture_size']}")
        print(f"  has_uv:      {stats['has_uv']}")
        print(f"  bbox:        {stats['bbox_min']} .. {stats['bbox_max']} (dimensions {stats['dimensions']})")
        print(f"  seed:        {result['seed']}  resolution: {result['resolution']} (effective {result['effective_resolution']})")
        timings = ", ".join(f"{k}={v:.1f}s" for k, v in result["timings"].items())
        print(f"  timings:     {timings}")
        print(f"  wall clock:  {time.monotonic() - started:.1f}s")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except E2EError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"ERROR: HTTP failure talking to {args.base_url}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
