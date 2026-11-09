#!/usr/bin/env python3
"""Compare what Drive says it holds with what we meant to upload.

  readback_check.py --expected NAME-zips.json --got drive.json

drive.json: what the Drive connector reports after upload, as a list of
{"name": "...", "size": 123} (size in bytes; "bytes" also accepted).
Prints one line per part; exit 0 only if every part is there with the same size.
"""
import argparse, json, sys
from pathlib import Path

def compare(expected, got):
    have = {g["name"]: int(g.get("size", g.get("bytes", -1))) for g in got}
    rows, ok = [], True
    for p in expected:
        s = have.get(p["name"])
        if s is None:
            rows.append(f"MISSING {p['name']}"); ok = False
        elif s != p["bytes"]:
            rows.append(f"SIZE MISMATCH {p['name']}: expected {p['bytes']}, Drive has {s}"); ok = False
        else:
            rows.append(f"ok {p['name']} {s} bytes")
    return ok, rows

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--expected", required=True); ap.add_argument("--got", required=True)
    a = ap.parse_args(argv)
    exp = json.loads(Path(a.expected).read_text())
    exp = exp["parts"] if isinstance(exp, dict) else exp
    ok, rows = compare(exp, json.loads(Path(a.got).read_text()))
    print("\n".join(rows)); print("READBACK " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
