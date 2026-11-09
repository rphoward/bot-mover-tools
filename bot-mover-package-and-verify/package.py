#!/usr/bin/env python3
"""Build a dated export folder from inventory.json. Never overwrites: if the
dated folder exists, a -2, -3... suffix is used. Reads sources, never edits them.

  package.py --inventory WORK/inventory.json --out-parent WORK/exports
             [--name bot-export] [--include-private ID|PATH|BOT|memory:BOT ...]
             [--requirements FILE] [--lock FILE] [--test-cmd "CMD" ...]

Layout written:
  <name>-YYYY-MM-DD/README.md HANDOFF.md setup.sh setup.ps1 [requirements.txt requirements.lock]
                   MANIFEST.json MANIFEST.sha256 screen-report.json
                   bots/<slug>/bot.md (+ routines.md, connectors.md)
                   skills/<slug>/...   data/<path relative to DATA_ROOT>/...
Never ships: venvs, model weights, caches, backups, __pycache__, secret files,
private items without an OK, missing/unsupported items, symlinks.
"""
import argparse, datetime, hashlib, json, os, re, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import secret_scan  # noqa: E402

NEVER_RX = re.compile(r"(^|/)(\.venv|venv|__pycache__|\.cache|cache|caches|models?|node_modules|\.git|backups?)(/|$)|\.bak(-\d{4}-\d{2}-\d{2}(-\d+)?)?$|(^|/)backup-[^/]*|\.pyc$|\.(onnx|safetensors|gguf|ggml|pt|pth|ckpt|h5|tflite)$")

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def relpath(p, *roots):
    s = str(p)
    for r in roots:
        r = str(r).rstrip("/")
        if s.startswith(r + "/"):
            return s[len(r) + 1:]
    return s

def dated_dir(parent, name, today=None):
    today = today or datetime.date.today().isoformat()
    base = Path(parent) / f"{name}-{today}"
    p, n = base, 2
    while p.exists():
        p = Path(f"{base}-{n}"); n += 1
    p.mkdir(parents=True)
    return p

def yq(v):
    if v is None:
        return "null"
    return json.dumps(str(v))  # JSON strings are valid YAML scalars

def write_manifest(root, meta):
    files = []
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            p = Path(dp) / fn
            rel = p.relative_to(root).as_posix()
            if rel in ("MANIFEST.json", "MANIFEST.sha256"):
                continue
            files.append({"path": rel, "bytes": p.stat().st_size, "sha256": sha256(p)})
    meta = dict(meta, files=files)
    (root / "MANIFEST.json").write_text(json.dumps(meta, indent=2))
    lines = [f"{f['sha256']}  {f['path']}" for f in files]
    lines.insert(0, f"{sha256(root / 'MANIFEST.json')}  MANIFEST.json")
    (root / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")
    return files

def human(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{int(n)} B" if u == "B" else f"{n:.1f} {u}"
        n /= 1024

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--out-parent", required=True)
    ap.add_argument("--name", default="bot-export")
    ap.add_argument("--include-private", action="append", default=[])
    ap.add_argument("--requirements")
    ap.add_argument("--lock")
    ap.add_argument("--test-cmd", action="append", default=[])
    ap.add_argument("--date", help="override YYYY-MM-DD (tests)")
    a = ap.parse_args(argv)

    inv = json.loads(Path(a.inventory).read_text())
    skills_root, data_root = Path(inv["skills_root"]), Path(inv["data_root"])
    ok_private = set(a.include_private)
    out = dated_dir(a.out_parent, a.name, a.date)
    screen = {"label": "screened", "note": "pattern screen only; not a certification", "findings": [], "secret_files": [], "files_scanned": 0}
    shipped, held, skipped = [], [], []

    def private_ok(it):
        return it["id"] in ok_private or it["path"] in ok_private or any(b in ok_private for b in it["used_by"])

    bots_by = {b["slug"]: b for b in inv["bots"]}
    for it in inv["items"]:
        p = Path(it["path"])
        cls, st = it["classification"], it["status"]
        why = None
        if st in ("missing", "unsupported"):
            why = st
        elif (it.get("note") or "").startswith("symlink"):
            why = "symlink (setup step)"
        elif cls in ("rebuildable", "excluded"):
            why = it.get("note") or cls
        elif cls == "secret" or secret_scan.is_secret_file(p):
            why = "secret file: never shipped"
        elif NEVER_RX.search(relpath(p, skills_root, data_root)):
            why = "never shipped (venv/model/cache/backup)"
        elif cls == "private" and not private_ok(it):
            held.append(it); continue
        if why:
            skipped.append((it, why)); continue
        if str(p).startswith(str(skills_root) + "/"):
            dest = out / "skills" / p.relative_to(skills_root)
        elif str(p).startswith(str(data_root) + "/"):
            dest = out / "data" / p.relative_to(data_root)
        else:
            skipped.append((it, "outside roots")); continue
        screen["files_scanned"] += 1
        rel = dest.relative_to(out).as_posix()
        if secret_scan.redact_copy_file(p, dest, screen, rel):
            shipped.append((it, rel))

    for b in inv["bots"]:
        d = out / "bots" / b["slug"]; d.mkdir(parents=True, exist_ok=True)
        withheld = b["private"] and b["slug"] not in ok_private
        if b["status"] != "answered":
            body = f"_Profile not provided: this bot {b['status']}. Status: stale/unconfirmed. Ask it again (it may need your OK in its own chat)._\n"
        elif withheld:
            body = "_Private bot: description withheld until the owner says yes for this bot._\n"
        else:
            body, _ = secret_scan.redact_text((b["description"] or "").strip() + "\n")
        fm = ["---", f"id: {yq(b['id'])}", f"name: {yq(b['name'])}", f"title: {yq(None if withheld else b['title'])}",
              f"status: {b['status']}", f"private: {str(b['private']).lower()}", f"runnable: {json.dumps(b['runnable'])}",
              f"skills: {json.dumps(b['skills'])}", "---", ""]
        (d / "bot.md").write_text("\n".join(fm) + body)
        if b["routines"] and not withheld:
            R = ["# Routines (text only — restore PAUSED, turn on one at a time)", ""]
            for r in b["routines"]:
                txt, _ = secret_scan.redact_text(str(r.get("prompt", "")))
                R += [f"## {r.get('name','routine')}", f"- schedule: {r.get('schedule','?')}", f"- state when exported: {r.get('state','?')}",
                      "- restore state: PAUSED", "", txt, ""]
            (d / "routines.md").write_text("\n".join(R))
        if b["connectors"]:
            C = ["# Connectors (names only, no tokens — sign in fresh)", "", "| app | used for | sign-in needed |", "|---|---|---|"]
            C += [f"| {c['app']} | {c.get('use') or ''} | {'yes' if c.get('sign_in_needed', True) else 'no'} |" for c in b["connectors"]]
            (d / "connectors.md").write_text("\n".join(C) + "\n")
        if b.get("memory_summary") and f"memory:{b['slug']}" in ok_private:
            t, _ = secret_scan.redact_text(b["memory_summary"])
            (d / "memory-summary.md").write_text("# Memory summary (owner OK'd)\n\n" + t + "\n")

    if a.requirements:
        shutil.copy2(a.requirements, out / "requirements.txt")
    if a.lock:
        shutil.copy2(a.lock, out / "requirements.lock")
    for t in ("setup.sh", "setup.ps1"):
        shutil.copy2(HERE / "templates" / t, out / t)
    os.chmod(out / "setup.sh", 0o755)

    partial = inv["coverage"] == "PARTIAL" or bool(held) or any(w in ("missing", "unsupported") for _, w in skipped)
    coverage = "PARTIAL" if partial else "COMPLETE"
    nb = len(inv["bots"]); ns = len({r.split('/')[1] for _, r in shipped if r.startswith("skills/")})
    write_handoff(out, inv, held, skipped, screen, coverage)
    write_readme(out, inv, a.test_cmd, coverage, nb, ns, held, skipped)
    (out / "screen-report.json").write_text(json.dumps(screen, indent=2))
    meta = {"bundle": out.name, "schema": "bot-mover-bundle/1",
            "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "coverage": coverage, "data_root_var": "DATA_ROOT", "data_dir": "data",
            "test_commands": a.test_cmd, "bots": nb, "skills": ns,
            "excludes": ["venvs", "model weights/caches", "fetch/build caches", "backups", "__pycache__", "secret files", "private items without OK"],
            "screen": secret_scan.summary(screen)}
    files = write_manifest(out, meta)
    print(f"packaged {out} ({coverage}): {len(files)} files, {nb} bots, {ns} skills, {len(held)} private held, {len(skipped)} skipped")
    print(secret_scan.summary(screen))
    return 0

def write_handoff(out, inv, held, skipped, screen, coverage):
    L = [f"# Handoff ({coverage})", "", "## Bots", "", "| bot | status | runnable | private |", "|---|---|---|---|"]
    L += [f"| {b['name']} | {b['status']}{' (stale/unconfirmed)' if b['status'] != 'answered' else ''} | {b['runnable']} | {'yes' if b['private'] else 'no'} |" for b in inv["bots"]]
    L += ["", "## Private items held back (names only — say yes per item to include)", ""]
    L += [f"- {Path(i['path']).name} — {Path(i['path']).parent} — {human(i['bytes'])}" for i in held] or ["none"]
    L += ["", "## Not shipped", "", "| item | why | rebuild |", "|---|---|---|"]
    for i, why in skipped:
        rb = "run setup.sh / setup.ps1" if "venv" in why else ("downloads on first use" if "model" in why else ("rebuilt on use" if "cache" in why else ""))
        L.append(f"| {i['path']} | {why} | {rb} |")
    L += ["", "## Dependency gaps", ""]
    gaps = [f"- {b['name']}: {le}" for b in inv["bots"] for le in b["loose_ends"]]
    L += gaps or ["none"]
    L += ["", "## Redactions (values never shown)", "", secret_scan.summary(screen)]
    L += [f"- {f['kind']} in {f['file']} line {f['line']}" for f in screen["findings"]]
    L += [f"- secret file withheld: {f}" for f in screen["secret_files"]]
    (out / "HANDOFF.md").write_text("\n".join(L) + "\n")

def write_readme(out, inv, tests, coverage, nb, ns, held, skipped):
    tc = "\n".join(tests) or "# no test commands recorded"
    tc_ps = "\n".join(t.replace('"$PY"', "& $py").replace("$PY", "$py").replace("$DATA_ROOT", "$env:DATA_ROOT") for t in tests) or "# no test commands recorded"
    R = f"""# {out.name} ({coverage})

What's here: {nb} bot profile(s) in `bots/`, {ns} skill folder(s) in `skills/`, the files they use in `data/`.
See `HANDOFF.md` for what was left out and why. Secret screen: see `screen-report.json` (screened, not certified).

**Not included:** Python venvs, model weights and caches (download on first use), build caches, backups,
secret files, and private items you didn't OK. **Doesn't come back:** chat history, app sign-ins, live sessions, secrets.

## 1. Put it back together
If you have several zips (`name-1ofN.zip` ...), unzip **all of them into the same folder**. They fill one folder.

## 2. Setup
Mac / Linux:
```bash
cd /path/to/{out.name}
bash setup.sh
export DATA_ROOT="$(pwd)/data"
PY="$(pwd)/.venv/bin/python"
```
Windows PowerShell:
```powershell
cd C:\\path\\to\\{out.name}
.\\setup.ps1
$env:DATA_ROOT = (Join-Path (Get-Location).Path "data")
$py = Join-Path (Get-Location).Path ".venv\\Scripts\\python.exe"
```
Setup installs from `requirements.lock`, and falls back to `requirements.txt` if the lock doesn't fit your computer.

## 3. Test commands (run from this folder)
```bash
{tc}
```
```powershell
{tc_ps}
```

## 4. Check the files arrived whole
```bash
sha256sum -c MANIFEST.sha256      # Mac: shasum -a 256 -c MANIFEST.sha256
```

## 5. Bring the bots back
1. Reconnect each app (see `bots/<bot>/connectors.md`) with a fresh sign-in.
2. Make the bot, paste `bots/<bot>/bot.md` body as its description, add its skills from `skills/`.
3. Recreate routines from `routines.md` **paused**, then turn them on one at a time.
4. Before turning anything on, check for half-done sends so nothing goes out twice.
5. Cursor: put each bot in `.cursor/skills/<bot>/SKILL.md` with its skills next to it and call it as `/<bot>`.
   Use `.cursor/agents` only for helper/worker agents. Schedules are a separate Automations setup.
"""
    (out / "README.md").write_text(R)

if __name__ == "__main__":
    sys.exit(main())
