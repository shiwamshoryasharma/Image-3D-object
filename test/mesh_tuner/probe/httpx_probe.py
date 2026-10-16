"""httpx behaviour probe against official Blender hosts (HEAD + tiny Range requests only).

Run: backend\\python\\python.exe -s test\\mesh_tuner\\probe\\httpx_probe.py
"""
import httpx

UA = "Img3D-BlenderRuntime/0.1"
ZIP = "https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip"
MIRROR = "https://mirror.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip"

print("httpx", httpx.__version__, "default UA:", httpx.Client().headers.get("user-agent"))
with httpx.Client(headers={"User-Agent": UA}, timeout=30, http2=False) as c:
    r = c.head(ZIP)
    print("HEAD direct", r.status_code, r.http_version, r.headers.get("content-length"), r.headers.get("etag"),
          r.headers.get("accept-ranges"))
    r = c.get(ZIP, headers={"Range": "bytes=0-15"})
    print("Range direct", r.status_code, r.headers.get("content-range"), r.content[:4])
    r = c.head(MIRROR)
    print("HEAD mirror (no follow)", r.status_code, r.headers.get("location"))
    r = c.head(MIRROR, follow_redirects=True)
    print("HEAD mirror (follow)", r.status_code, str(r.url), [str(h.url.host) for h in r.history],
          r.headers.get("content-length"), r.headers.get("etag"))
