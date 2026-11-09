#!/usr/bin/env python3
"""Zip a bundle folder as one zip, or as standalone parts that each stay under
the size limit and all unzip into the same folder (no raw .partNN byte splits).

  split_zips.py BUNDLE_DIR --out OUT_DIR [--limit-mb 95] [--name NAME]

Writes NAME.zip (fits) or NAME-1ofN.zip ... plus NAME-zips.json (name, bytes, sha256, files).
Every entry is stored under "<bundle folder name>/". Skips .venv. Exit 2 if any
single file is bigger than the limit (it can't go in a standalone part: keep it
on Drive as its own file, or use Git LFS only if the owner asks).
Default limit 95 MB (95,000,000 bytes) keeps under the ~100 MB caps.
"""
import argparse, hashlib, json, os, sys, zipfile
from pathlib import Path

STORE_EXT = {".zip", ".gz", ".bz2", ".xz", ".7z", ".png", ".jpg", ".jpeg", ".mp4", ".mp3", ".pdf", ".bin"}

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def plan(files, limit):
    """first-fit decreasing by raw size (raw size bounds compressed size + small overhead)."""
    bins = []
    for rel, size in sorted(files, key=lambda x: -x[1]):
        cost = size + 512 + 2 * len(rel)
        for b in bins:
            if b["bytes"] + cost <= limit:
                b["files"].append(rel); b["bytes"] += cost; break
        else:
            bins.append({"files": [rel], "bytes": cost})
    return bins

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle"); ap.add_argument("--out", required=True)
    ap.add_argument("--limit-mb", type=float, default=95.0); ap.add_argument("--name")
    a = ap.parse_args(argv)
    root = Path(a.bundle).resolve(); top = root.name
    name = a.name or top
    limit = int(a.limit_mb * 1_000_000) - 64 * 1024  # room for central directory
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    files = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != ".venv"]
        for fn in fns:
            p = Path(dp) / fn
            if p.is_symlink():
                print(f"skipping symlink {p}", file=sys.stderr); continue
            files.append((p.relative_to(root).as_posix(), p.stat().st_size))
    big = [(r, s) for r, s in files if s + 512 > limit]
    if big:
        for r, s in big:
            print(f"TOO BIG for a standalone part: {r} ({s} bytes). Ship it as its own Drive file or ask about Git LFS.", file=sys.stderr)
        return 2
    bins = plan(files, limit)
    n = len(bins)
    names = [f"{name}.zip"] if n == 1 else [f"{name}-{i}of{n}.zip" for i in range(1, n + 1)]
    for zn in names:
        if (out / zn).exists():
            print(f"refusing to overwrite {out / zn}; pick a new --out or --name", file=sys.stderr); return 3
    rec = []
    for zn, b in zip(names, bins):
        zp = out / zn
        with zipfile.ZipFile(zp, "w", allowZip64=True) as zf:
            for rel in sorted(b["files"]):
                ct = zipfile.ZIP_STORED if Path(rel).suffix.lower() in STORE_EXT else zipfile.ZIP_DEFLATED
                zf.write(root / rel, f"{top}/{rel}", compress_type=ct)
        size = zp.stat().st_size
        if size >= int(a.limit_mb * 1_000_000):
            print(f"part {zn} came out {size} bytes, over the limit", file=sys.stderr); return 4
        rec.append({"name": zn, "bytes": size, "sha256": sha256(zp), "files": len(b["files"])})
    (out / f"{name}-zips.json").write_text(json.dumps({"bundle": top, "limit_bytes": int(a.limit_mb * 1_000_000), "parts": rec}, indent=2))
    for r in rec:
        print(f"{r['name']}  {r['bytes'] / 1_000_000:.1f} MB  {r['files']} files")
    print(f"{len(rec)} zip(s); unzip all into one folder -> {top}/")
    return 0

if __name__ == "__main__":
    sys.exit(main())
