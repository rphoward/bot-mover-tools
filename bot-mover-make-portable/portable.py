#!/usr/bin/env python3
"""Make hardcoded paths portable with one env var (default DATA_ROOT) that
falls back to the old path, so nothing breaks today.

  portable.py --old /old/root TARGET [TARGET...]            # dry run: shows the plan
  portable.py --old /old/root TARGET... --apply             # edits, after backups
        [--var DATA_ROOT] [--protect DIR ...] [--live-ok]
        [--smoke "CMD" [--moved DIR]]                       # run CMD with and without the var
  portable.py --lock requirements.txt --lock-out requirements.lock   # pin from a throwaway venv

Rules built in:
  * every edited file gets <file>.bak-YYYY-MM-DD first (then -2, -3...; never overwritten, never deleted)
  * files under a --protect folder (default: /home/box/agent-data, where live bots
    keep skills) are refused unless --live-ok (only with the owner's yes)
  * symlinks are never edited: listed with a setup step instead
  * .py: "OLD/x" -> os.path.join(DATA_ROOT, "x") with DATA_ROOT = os.environ.get(VAR, "OLD")
    .sh/.bash/.md/.txt: OLD -> ${VAR:-OLD}      other types: listed for a manual look
Use --var MODEL_CACHE --old <model dir> for an optional model cache variable.
"""
import argparse, datetime, os, re, shutil, subprocess, sys, tempfile, venv
from pathlib import Path

PY_EXT = {".py"}
SH_EXT = {".sh", ".bash", ".md", ".txt", ""}

def backup(p):
    base = f"{p}.bak-{datetime.date.today().isoformat()}"
    b, n = Path(base), 2
    while b.exists():
        b = Path(f"{base}-{n}"); n += 1
    shutil.copy2(p, b)
    return b

def py_rewrite(text, old, var):
    old = old.rstrip("/")
    rx = re.compile(r"(?<![A-Za-z_])(?P<q>[\"'])(?P<path>" + re.escape(old) + r"(?:/[^\"'\n{}]*)?)(?P=q)")
    n = 0
    def rep(m):
        nonlocal n
        n += 1
        rest = m.group("path")[len(old):].lstrip("/")
        return f"os.path.join({var}, {rest!r})" if rest else var
    hdr_rx = re.compile(rf"^\s*{re.escape(var)}\s*=\s*os\.environ")
    new = "\n".join(l if hdr_rx.match(l) else rx.sub(rep, l) for l in text.split("\n"))
    leftovers = len([l for l in new.splitlines() if old in l and not re.match(rf"\s*{var}\s*=\s*os\.environ", l)])
    if n and not re.search(rf"^{var}\s*=", new, re.M):
        lines = new.split("\n"); i = 0
        if lines and lines[0].startswith("#!"): i = 1
        while i < len(lines) and re.match(r"#.*coding[:=]", lines[i]): i += 1
        # skip a module docstring
        if i < len(lines) and re.match(r'\s*[ru]?("""|\'\'\')', lines[i]):
            q = re.match(r'\s*[ru]?("""|\'\'\')', lines[i]).group(1)
            if lines[i].count(q) >= 2:
                i += 1
            else:
                i += 1
                while i < len(lines) and q not in lines[i]: i += 1
                i += 1
        while i < len(lines) and lines[i].startswith("from __future__"): i += 1
        hdr = ["import os", f"{var} = os.environ.get({var!r}, {old!r})  # portable: set {var} to move the data"]
        lines[i:i] = hdr
        new = "\n".join(lines)
    return new, n, max(leftovers, 0)

def sh_rewrite(text, old, var):
    old = old.rstrip("/")
    rx = re.compile(r"(?<!:-)(?<![\w/.])" + re.escape(old) + r"(?=/|\b|$)")
    new, n = rx.subn("${" + var + ":-" + old + "}", text)
    return new, n, 0

def targets(paths):
    for t in paths:
        t = Path(t)
        if t.is_dir() and not t.is_symlink():
            for dp, dns, fns in os.walk(t):
                dns[:] = [d for d in dns if d not in (".venv", "venv", "__pycache__", ".git", "node_modules")]
                for fn in fns:
                    if ".bak-" in fn:
                        continue
                    yield Path(dp) / fn
        else:
            yield t

def under(p, roots):
    rp = str(Path(p).absolute())
    return any(rp == r or rp.startswith(r.rstrip("/") + "/") for r in roots)

def smoke(cmd, var, moved):
    env = {k: v for k, v in os.environ.items() if k != var}
    r1 = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)
    print(f"smoke (no {var}, old default): exit {r1.returncode} {(r1.stdout + r1.stderr).strip()[-200:]}")
    ok = r1.returncode == 0
    if moved:
        env[var] = moved
        r2 = subprocess.run(cmd, shell=True, env=env, capture_output=True, text=True)
        print(f"smoke ({var}={moved}): exit {r2.returncode} {(r2.stdout + r2.stderr).strip()[-200:]}")
        ok &= r2.returncode == 0
    return ok

def lock(req, out):
    out = Path(out)
    if out.exists():
        backup(out)
    with tempfile.TemporaryDirectory(prefix="bm-lock-") as d:
        venv.create(d, with_pip=True)
        py = Path(d) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(req)], check=True)
        frz = subprocess.run([str(py), "-m", "pip", "freeze", "--all", "--exclude", "pip", "--exclude", "setuptools"], check=True, capture_output=True, text=True).stdout
    out.write_text(f"# frozen {datetime.date.today().isoformat()} on {sys.platform} python {sys.version.split()[0]}\n" + frz)
    print(f"wrote {out} ({len(frz.splitlines())} pins)")

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*")
    ap.add_argument("--old"); ap.add_argument("--var", default="DATA_ROOT")
    ap.add_argument("--apply", action="store_true"); ap.add_argument("--live-ok", action="store_true")
    ap.add_argument("--protect", action="append", default=None)
    ap.add_argument("--smoke"); ap.add_argument("--moved")
    ap.add_argument("--lock"); ap.add_argument("--lock-out")
    a = ap.parse_args(argv)
    if a.lock:
        lock(a.lock, a.lock_out or Path(a.lock).with_suffix(".lock")); return 0
    if not a.old:
        ap.error("--old is required")
    protect = a.protect if a.protect is not None else ["/home/box/agent-data"]
    rc, changed, manual, links = 0, [], [], []
    for f in targets(a.targets):
        if f.is_symlink():
            links.append(f); continue
        if not f.is_file():
            continue
        try:
            text = f.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if a.old not in text:
            continue
        ext = f.suffix.lower()
        if ext in PY_EXT:
            new, n, left = py_rewrite(text, a.old, a.var)
        elif ext in SH_EXT:
            new, n, left = sh_rewrite(text, a.old, a.var)
        else:
            manual.append((f, "file type not auto-rewritten")); continue
        if left:
            manual.append((f, f"{left} occurrence(s) left (f-string or odd quoting)"))
        if not n:
            continue
        if under(f, protect) and not a.live_ok:
            print(f"REFUSED (live bot file; needs the owner's yes and --live-ok): {f}"); rc = 3; continue
        if a.apply:
            b = backup(f)
            f.write_text(new)
            shutil.copystat(b, f)
            changed.append((f, n, b))
        else:
            changed.append((f, n, None))
    verb = "changed" if a.apply else "would change"
    for f, n, b in changed:
        print(f"{verb}: {f} ({n} path(s))" + (f"  backup: {b}" if b else ""))
    for f, why in manual:
        print(f"manual look: {f} — {why}")
    for l in links:
        print(f"symlink (not edited): {l} -> {os.readlink(l)}  setup step: copy the real file into place, or recreate the link in setup")
    if not a.apply:
        print("dry run only. add --apply after the owner says yes.")
    if a.smoke:
        rc = rc or (0 if smoke(a.smoke, a.var, a.moved) else 1)
    return rc

if __name__ == "__main__":
    sys.exit(main())
