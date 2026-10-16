"""Probe the central directory of a remote Blender ZIP using HTTP Range requests only.

Usage: python -I zip_cd_probe.py <url> <out_dir> [max_cd_bytes]
Fetches the last 64 KiB (EOCD + maybe ZIP64 locator), then the central directory
(only if its size <= max_cd_bytes, default 4 MiB). Never downloads file data.
"""
import json
import os
import struct
import sys
import urllib.request

UA = "Img3D-probe/0.1"


def fetch(url, rng):
    req = urllib.request.Request(url, headers={"Range": rng, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        if r.status != 206:
            raise SystemExit(f"expected 206, got {r.status} for {rng}")
        return r.status, r.headers, r.read()


def main():
    url, out_dir = sys.argv[1], sys.argv[2]
    max_cd = int(sys.argv[3]) if len(sys.argv) > 3 else 4 * 1024 * 1024
    os.makedirs(out_dir, exist_ok=True)
    status, hdrs, tail = fetch(url, "bytes=-65536")
    total = int(hdrs["Content-Range"].split("/")[1])
    tail_start = total - len(tail)
    report = {"url": url, "tail_status": status, "content_range": hdrs.get("Content-Range"), "total": total}

    eocd = tail.rfind(b"PK\x05\x06")
    (_, disk, cd_disk, n_disk, n_total, cd_size, cd_off, clen) = struct.unpack("<IHHHHIIH", tail[eocd:eocd + 22])
    report["eocd"] = {"offset": tail_start + eocd, "entries": n_total, "cd_size": cd_size, "cd_offset": cd_off,
                      "comment_len": clen}
    zip64 = tail.rfind(b"PK\x06\x07")
    if zip64 != -1 and (cd_off == 0xFFFFFFFF or n_total == 0xFFFF or cd_size == 0xFFFFFFFF):
        z64_off = struct.unpack("<IIQI", tail[zip64:zip64 + 20])[2]
        rel = z64_off - tail_start
        rec = tail[rel:rel + 56]
        (_, _, _, _, _, _, n_disk, n_total, cd_size, cd_off) = struct.unpack("<IQHHIIQQQQ", rec)
        report["zip64"] = {"entries": n_total, "cd_size": cd_size, "cd_offset": cd_off}
    report["is_zip64"] = "zip64" in report

    if cd_size > max_cd:
        report["cd_fetched"] = False
        print(json.dumps(report, indent=2))
        return
    if cd_off >= tail_start:
        cd = tail[cd_off - tail_start:cd_off - tail_start + cd_size]
    else:
        _, _, cd = fetch(url, f"bytes={cd_off}-{cd_off + cd_size - 1}")
    report["cd_fetched"] = True
    names, p = [], 0
    methods = {}
    total_uncomp = 0
    while p < len(cd) and cd[p:p + 4] == b"PK\x01\x02":
        (sig, vmade, vneed, flag, meth, mt, md, crc, csz, usz, nlen, xlen, klen, dsk, iattr, eattr,
         loff) = struct.unpack("<IHHHHHHIIIHHHHHII", cd[p:p + 46])
        name = cd[p + 46:p + 46 + nlen].decode("utf-8" if flag & 0x800 else "cp437")
        names.append(name)
        methods[meth] = methods.get(meth, 0) + 1
        if usz != 0xFFFFFFFF:
            total_uncomp += usz
        p += 46 + nlen + xlen + klen
    tops = sorted({n.split("/", 1)[0] for n in names})
    longest = max(names, key=len)
    exe = [n for n in names if n.lower().endswith(".exe")]
    root_files = sorted(n for n in names if n.count("/") == 1 and not n.endswith("/"))
    root_dirs = sorted({n.split("/")[1] for n in names if n.count("/") >= 2})
    report.update({
        "parsed_entries": len(names),
        "top_level": tops,
        "root_files": root_files,
        "root_dirs": root_dirs,
        "exe_entries": exe,
        "max_path_len": len(longest),
        "longest_path": longest,
        "compression_methods": methods,
        "approx_uncompressed_bytes": total_uncomp,
    })
    with open(os.path.join(out_dir, "zip_names.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(names))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
