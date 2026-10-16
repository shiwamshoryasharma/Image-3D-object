"""LAN check: REST + WebSocket through the Vite dev proxy reached via the machine's LAN IP.

Usage: python.exe -s lan_proxy_check.py http://<lan-ip>:5199
"""

import asyncio
import json
import os
import site
import sys

_USER_SITE = os.path.normcase(os.path.abspath(site.getusersitepackages()))
sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.abspath(p)) != _USER_SITE]

import httpx  # noqa: E402
import websockets  # noqa: E402


async def main(base: str) -> int:
    ok = True
    origin = {"Origin": base}  # what a browser on another device sends
    with httpx.Client(base_url=base, timeout=15, headers=origin) as client:
        page = client.get("/")
        print("GET / ->", page.status_code)
        ok &= page.status_code == 200
        health = client.get("/api/v1/health")
        print("GET /api/v1/health ->", health.status_code, health.text[:120])
        ok &= health.status_code == 200
        created = client.post("/api/v1/reference-sets", json={"name": "lan-check"})
        print("POST /api/v1/reference-sets ->", created.status_code)
        ok &= created.status_code == 201
        if created.status_code == 201:
            set_id = created.json()["id"]
            deleted = client.delete(f"/api/v1/reference-sets/{set_id}")
            print("DELETE reference set ->", deleted.status_code)
            ok &= deleted.status_code == 204
    ws_url = base.replace("http://", "ws://") + "/api/v1/ws"
    async with websockets.connect(ws_url, additional_headers=origin, open_timeout=15) as ws:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        print("WS", ws_url, "-> first message type:", hello.get("type"))
        ok &= hello.get("type") == "hello"
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1].rstrip("/"))))
