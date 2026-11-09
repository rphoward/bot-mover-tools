#!/usr/bin/env python3
"""Verify a Bot Mover bundle.

  verify.py folder BUNDLE_DIR                      # re-hash every file vs MANIFEST.json
  verify.py zips Z1.zip [Z2.zip ...] [--run-setup] [--run-tests] [--keep]
        # unzip ALL parts into one clean temp folder, then: re-hash, check no
        # venv/model/cache/backup files, secret screen, optional setup.sh + tests
Prints a short summary; exit 0 only if every check passed.
Test commands run from the bundle root with DATA_ROOT=<bundle>/data and
PY=<bundle>/.venv/bin/python (or python3 when there is no venv).
"""
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import secret_scan  # noqa: E402

FORBIDDEN = re.compile(r"(^|/)(\.venv|venv|__pycache__|\.cache|cache|caches|models?|node_modules|backups?)/|\.bak(-\d{4}-\d{2}-\d{2}(-\d+)?)?$|(^|/)backup-[^/]*|\.pyc$|\.(onnx|safetensors|gguf|ggml|pt|pth|ckpt)$")

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def check_folder(root):
    root = Path(root)
    m = json.loads((root / "MANIFEST.json").read_text())
    bad = []
    listed = set()
    for e in m["files"]:
        p = root / e["path"]; listed.add(e["path"])
        if not p.is_file():
            bad.append(f"missing {e['path']}")
        elif p.stat().st_size != e["bytes"] or sha256(p) != e["sha256"]:
            bad.append(f"hash mismatch {e['path']}")
    # MANIFEST.sha256 must agree too
    for line in (root / "MANIFEST.sha256").read_text().splitlines():
        if not line.strip():
            continue
        h, rel = line.split("  ", 1)
        if not (root / rel).is_file() or sha256(root / rel) != h:
            bad.append(f"MANIFEST.sha256 mismatch {rel}")
    extra = []
    for dp, dns, fns in os.walk(root):
        if ".venv" in Path(dp).relative_to(root).parts:
            continue
        for fn in fns:
            rel = (Path(dp) / fn).relative_to(root).as_posix()
            if rel not in listed and rel not in ("MANIFEST.json", "MANIFEST.sha256") and not rel.startswith(".venv/"):
                extra.append(rel)
    return m, bad, extra

def forbidden_entries(names):
    return [n for n in names if FORBIDDEN.search(n)]

def extract_all(zips, dest):
    names, tops = [], set()
    for z in zips:
        with zipfile.ZipFile(z) as zf:
            for i in zf.infolist():
                n = i.filename
                if n.startswith("/") or ".." in Path(n).parts:
                    raise SystemExit(f"unsafe path in {z}: {n}")
                if (i.external_attr >> 16) & 0o170000 == 0o120000:
                    raise SystemExit(f"symlink entry in {z}: {n}")
                names.append(n); tops.add(n.split("/", 1)[0])
            zf.extractall(dest)
    return names, tops

def run(cmd, cwd, env, timeout=900):
    r = subprocess.run(cmd, cwd=cwd, env=env, shell=isinstance(cmd, str), capture_output=True, text=True, timeout=timeout)
    tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
    return r.returncode, tail

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("folder"); f.add_argument("bundle")
    z = sub.add_parser("zips"); z.add_argument("zips", nargs="+")
    z.add_argument("--run-setup", action="store_true"); z.add_argument("--run-tests", action="store_true"); z.add_argument("--keep", action="store_true")
    a = ap.parse_args(argv)
    ok = True
    if a.cmd == "folder":
        m, bad, extra = check_folder(a.bundle)
        print(f"hashes: {len(m['files']) - len(bad)}/{len(m['files'])} match" + (f"; problems: {bad[:5]}" if bad else ""))
        if extra:
            print(f"files not in manifest: {extra[:5]}")
        return 0 if not bad else 1
    tmp = Path(tempfile.mkdtemp(prefix="bot-mover-verify-"))
    try:
        names, tops = extract_all(a.zips, tmp)
        if len(tops) != 1:
            print(f"FAIL: parts unzip into {len(tops)} folders, expected 1: {sorted(tops)}"); return 1
        root = tmp / tops.pop()
        print(f"unzipped {len(a.zips)} part(s) into one folder: {root.name}")
        fb = forbidden_entries(names)
        if fb:
            ok = False; print(f"FAIL: venv/model/cache/backup files in zip: {fb[:5]}")
        else:
            print("no venv/model/cache/backup files in zip")
        m, bad, extra = check_folder(root)
        if bad:
            ok = False; print(f"FAIL hashes: {bad[:5]}")
        else:
            print(f"hashes: {len(m['files'])}/{len(m['files'])} match")
        rep = secret_scan.scan([root])
        print("secret screen: " + secret_scan.summary(rep).replace("screened", "screened (not certified)", 1))
        # [REDACTED:...] markers are fine; any live pattern hit is a failure
        if rep["count"]:
            ok = False
        env = dict(os.environ, DATA_ROOT=str(root / "data"))
        if a.run_setup:
            if os.name == "nt":
                rc, tail = run(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(root / "setup.ps1")], root, env)
            else:
                rc, tail = run(["bash", str(root / "setup.sh")], root, env)
            print(f"setup exit {rc}: " + " | ".join(tail)); ok &= rc == 0
        if a.run_tests:
            vpy = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            env["PY"] = str(vpy if vpy.exists() else shutil.which("python3") or "python3")
            for t in m.get("test_commands", []):
                rc, tail = run(t, root, env)
                print(f"test `{t}` exit {rc}: " + " | ".join(tail)); ok &= rc == 0
        print("VERIFY " + ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    finally:
        if a.keep:
            print(f"kept {tmp}")
        else:
            shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
