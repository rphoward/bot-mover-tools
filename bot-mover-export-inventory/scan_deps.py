#!/usr/bin/env python3
"""Read-only dependency scan for a Bot Mover export.

  scan_deps.py --answers answers.json --out WORKDIR
               [--skills-root /home/box/agent-data/workflows] [--data-root /workspace]
               [--root PREFIX ...] [--bots slug,slug]

Inputs
  answers.json  what each bot said about itself (see SKILL.md). Bots that refused
                or stayed silent keep status refused/silent; their profile fields
                are forced to null so nothing is invented.
Outputs (WORKDIR)
  inventory.json  bots + items (schema in SKILL.md)
  inventory.md    owner table + top 3 loose ends
Never writes anywhere except WORKDIR. Never follows symlinks for writing.
"""
import argparse, hashlib, json, os, re, sys, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot-mover-package-and-verify"))
try:
    import secret_scan  # the one secret-file rule, shared with package.py and verify.py
except ImportError as e:  # never fall back to a weaker check: a missed secret would ship
    sys.exit(f"scan_deps: cannot load secret_scan.py from ../bot-mover-package-and-verify ({e}); run bot-mover-tools first")

DB_EXT = {".db", ".sqlite", ".sqlite3", ".duckdb", ".faiss", ".index", ".ann", ".hnsw", ".lance", ".npy", ".npz", ".parquet", ".pkl"}
MODEL_EXT = {".onnx", ".safetensors", ".gguf", ".ggml", ".pt", ".pth", ".ckpt", ".h5", ".tflite", ".mlmodel"}
TEXT_EXT = {".md", ".txt", ".py", ".sh", ".ps1", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini", ".js", ".ts", ".csv", ".sql", ".html", ""}
PRIVATE_WORDS = re.compile(r"(?i)(^|[/_\-. ])(tax|taxes|legal|law|medical|health|finance|financial|bank|payroll|ssn|voice|history|household|personal|private|diary|journal|w2|1099|k1)([/_\-. ]|$)")
VENV_RX = re.compile(r"(^|/)(\.venv|venv|env|\.virtualenvs)/|/site-packages/|/(bin|Scripts)/python(3(\.\d+)?)?(\.exe)?$")
CACHE_RX = re.compile(r"(^|/)(__pycache__|\.cache|cache|caches|\.pytest_cache|node_modules|\.mypy_cache|\.ruff_cache)(/|$)")
BACKUP_RX = re.compile(r"(\.bak(-\d{4}-\d{2}-\d{2}(-\d+)?)?$|(^|/)backups?(/|$)|(^|/)backup-[^/]*|\.orig$|~$)")
MODEL_DIR_RX = re.compile(r"(^|/)(models?|hf_cache|huggingface|fastembed_cache|weights)(/|$)")
MAX_WALK = 5000

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()

def find_license(p, stop):
    d = p.parent
    stop = Path(stop)
    for _ in range(6):
        for n in ("LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"):
            if (d / n).is_file():
                return f"see {d / n}"
        if d == stop or d == d.parent:
            break
        d = d.parent
    return "unknown"

def classify(path, private_bot, data_root, skills_root):
    s = str(path).replace("\\", "/")
    full = s
    for r in (str(skills_root), str(data_root)):
        if s.startswith(r.rstrip("/") + "/"):
            s = s[len(r.rstrip("/")) + 1:]
            break
    secret = secret_scan.is_secret_file(s)
    ext = Path(s).suffix.lower()
    kind, cls, reason, status_hint = "source", "shared", None, None
    if secret:
        return "private_context", "secret", "secret file: never shipped", None
    if BACKUP_RX.search(s):
        return "source", "excluded", "backup: never shipped, never deleted", None
    if VENV_RX.search(s):
        return "runtime", "rebuildable", "venv: rebuilt by setup", "setup-required"
    if CACHE_RX.search(s):
        return "derived_index", "rebuildable", "cache: rebuilt on use", None
    if ext in MODEL_EXT or MODEL_DIR_RX.search(s):
        return "model", "rebuildable", "model weights: download on first use", "setup-required"
    if ext in DB_EXT or re.search(r"(^|/)(chroma|qdrant|lancedb|index)(/|$)", s):
        kind = "derived_index"
    elif re.search(r"(^|/)cards?[^/]*/", s):
        kind = "card"
    elif full.startswith(str(skills_root).rstrip("/") + "/"):
        kind = "source" if Path(s).name == "SKILL.md" else "helper"
    elif ext in {".py", ".sh", ".ps1", ".js", ".ts"}:
        kind = "helper"
    if private_bot or PRIVATE_WORDS.search(s):
        return "private_context", "private", "private: names only until the owner says yes for this item", None
    return kind, cls, reason, status_hint

def runtime_of(p):
    e = Path(p).suffix.lower()
    return {".py": "python", ".sh": "bash", ".ps1": "powershell", ".js": "node", ".ts": "node",
            ".db": "sqlite", ".sqlite": "sqlite", ".sqlite3": "sqlite"}.get(e, "none")

def path_regex(roots):
    alts = "|".join(re.escape(r.rstrip("/")) for r in sorted(roots, key=len, reverse=True))
    return re.compile(r"(?<![\w.\-])((?:%s)(?:/[^\s\"'`<>|)\]}:,;*?]*)?)" % alts)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answers", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--skills-root", default="/home/box/agent-data/workflows")
    ap.add_argument("--data-root", default="/workspace")
    ap.add_argument("--root", action="append", default=[], help="extra absolute prefixes to look for (default: data root, skills root, /home/box)")
    ap.add_argument("--bots", default="", help="comma list of bot slugs the owner picked (default all)")
    a = ap.parse_args(argv)

    answers = json.loads(Path(a.answers).read_text())
    skills_root = Path(a.skills_root).resolve()
    data_root = Path(a.data_root).resolve()
    roots = set(a.root) | {str(data_root), str(skills_root), "/workspace", "/home/box"}
    prx = path_regex(roots)
    picked = {s for s in a.bots.split(",") if s}

    items, bots = {}, []

    def add_item(path, bot, private_bot, provenance, missing=False, symlink_to=None):
        p = Path(path)
        key = str(p)
        if key in items:
            it = items[key]
            if bot not in it["used_by"]:
                it["used_by"].append(bot)
            if provenance not in it["provenance"]:
                it["provenance"].append(provenance)
            if private_bot and it["classification"] == "shared":
                it["classification"], it["kind"] = "private", "private_context"
            return it
        kind, cls, reason, hint = classify(key, private_bot, data_root, skills_root)
        it = {"id": "i" + hashlib.sha1(key.encode()).hexdigest()[:10], "path": key, "kind": kind,
              "classification": cls, "bytes": None, "sha256": None, "license": "unknown",
              "provenance": [provenance], "runtime": runtime_of(key), "status": "present",
              "used_by": [bot], "note": reason}
        if symlink_to is not None:
            it.update(status="setup-required", note=f"symlink to {symlink_to}: replace with a real file or a setup step", kind="runtime")
        elif missing:
            it["status"] = "missing"
        else:
            st = p.stat()
            it["bytes"] = st.st_size
            if cls != "secret":
                it["sha256"] = sha256(p)
            it["license"] = find_license(p, data_root if str(p).startswith(str(data_root)) else skills_root)
            if hint:
                it["status"] = hint
        inside = any(key == r or key.startswith(r.rstrip("/") + "/") for r in (str(data_root), str(skills_root)))
        if not inside and not missing and symlink_to is None:
            it["status"], it["note"] = "unsupported", "outside DATA_ROOT and the skills folder: not shipped; owner decides"
        items[key] = it
        return it

    def add_path(path, bot, private_bot, prov, depth=0):
        p = Path(path)
        if p.is_symlink():
            return add_item(p, bot, private_bot, prov, symlink_to=os.readlink(p))
        if not p.exists():
            # a venv python path that is missing is setup-required, not a loose data file
            it = add_item(p, bot, private_bot, prov, missing=True)
            if VENV_RX.search(str(p)):
                it["status"], it["kind"], it["classification"] = "setup-required", "runtime", "rebuildable"
                it["note"] = "venv: rebuilt by setup"
            return
        if p.is_dir():
            n = 0
            for dp, dns, fns in os.walk(p, followlinks=False):
                for dn in list(dns):
                    if (Path(dp) / dn).is_symlink():
                        add_item(Path(dp) / dn, bot, private_bot, prov, symlink_to=os.readlink(Path(dp) / dn))
                for fn in fns:
                    n += 1
                    if n > MAX_WALK:
                        add_item(p, bot, private_bot, prov)["note"] = f"folder has more than {MAX_WALK} files; listed partly"
                        return
                    add_path(Path(dp) / fn, bot, private_bot, prov, depth + 1)
            return
        add_item(p, bot, private_bot, prov)

    for b in answers.get("bots", []):
        slug = b["slug"]
        if picked and slug not in picked:
            continue
        status = b.get("status", "silent")
        private_bot = bool(b.get("private"))
        rec = {"slug": slug, "id": b.get("id"), "status": status, "private": private_bot,
               "name": b.get("name") or slug, "title": None, "description": None,
               "skills": list(b.get("skills", [])), "skills_source": b.get("skills_source", "bot" if status == "answered" else "owner"),
               "routines": [], "memory_summary": None, "connectors": [], "pins": [], "runnable": None, "loose_ends": []}
        if status == "answered":
            for k in ("title", "description", "memory_summary"):
                rec[k] = b.get(k)
            rec["routines"] = [dict(r, restore_state="paused") for r in b.get("routines", [])]
            rec["connectors"] = [{"app": c.get("app"), "use": c.get("use"), "sign_in_needed": c.get("sign_in_needed", True)} for c in b.get("connectors", [])]
            missing_fields = [k for k in ("title", "description") if not b.get(k)]
            if missing_fields:
                rec["loose_ends"].append(f"profile fields not reported: {', '.join(missing_fields)}")
        else:
            rec["loose_ends"].append(f"bot {status}: profile not provided (stale/unconfirmed); may need your OK in its own chat")
        for sk in rec["skills"]:
            sdir = skills_root / sk
            if not (sdir / "SKILL.md").is_file():
                add_item(sdir / "SKILL.md", slug, private_bot, f"skill {sk}", missing=True)
                rec["loose_ends"].append(f"skill {sk}: SKILL.md missing")
                continue
            for dp, dns, fns in os.walk(sdir, followlinks=False):
                for dn in list(dns):
                    q = Path(dp) / dn
                    if q.is_symlink():
                        add_item(q, slug, private_bot, f"skill folder {sk}", symlink_to=os.readlink(q))
                for fn in fns:
                    f = Path(dp) / fn
                    if f.is_symlink():
                        add_item(f, slug, private_bot, f"skill folder {sk}", symlink_to=os.readlink(f)); continue
                    add_item(f, slug, private_bot, f"skill folder {sk}")
                    if fn in ("requirements.txt", "requirements.lock", "pyproject.toml"):
                        rec["pins"].append({"file": str(f), "lines": [l.strip() for l in f.read_text(errors="replace").splitlines() if l.strip() and not l.startswith("#")]})
                    if f.suffix.lower() not in TEXT_EXT or f.stat().st_size > 2_000_000:
                        continue
                    for ln, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                        for m in prx.finditer(line):
                            ref = m.group(1).rstrip(".")
                            rp = Path(ref)
                            if rp == skills_root or str(rp).startswith(str(sdir)):
                                continue
                            if str(rp) in roots:
                                continue
                            add_path(rp, slug, private_bot, f"{sk}/{f.relative_to(sdir)}:{ln}")
                            if rp.name in ("requirements.txt", "requirements.lock") and rp.is_file():
                                rec["pins"].append({"file": str(rp), "lines": [l.strip() for l in rp.read_text(errors="replace").splitlines() if l.strip() and not l.startswith("#")]})
        for kf in b.get("key_files", []) if status == "answered" else []:
            add_path(Path(kf), slug, private_bot, "bot reported key file")
        bots.append(rec)

    # per-bot runnable + loose ends from items
    for rec in bots:
        mine = [i for i in items.values() if rec["slug"] in i["used_by"]]
        miss = [i for i in mine if i["status"] == "missing"]
        setup = [i for i in mine if i["status"] == "setup-required"]
        unsup = [i for i in mine if i["status"] == "unsupported"]
        for i in miss:
            rec["loose_ends"].append(f"missing file {i['path']} (from {i['provenance'][0]})")
        if any(i["kind"] == "symlink" or (i["note"] or "").startswith("symlink") for i in setup):
            rec["loose_ends"].append("symlinks need a real file or a setup step")
        for i in unsup:
            rec["loose_ends"].append(f"outside data root: {i['path']}")
        if rec["status"] != "answered" or miss:
            rec["runnable"] = False
        elif setup:
            rec["runnable"] = "setup-required"
        else:
            rec["runnable"] = True
        rec["data_bytes"] = sum(i["bytes"] or 0 for i in mine)

    inv = {"schema": "bot-mover-inventory/1", "created_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
           "skills_root": str(skills_root), "data_root": str(data_root),
           "coverage": "PARTIAL" if any(b["status"] != "answered" for b in bots) or any(i["status"] == "missing" for i in items.values()) else "COMPLETE",
           "bots": bots, "items": sorted(items.values(), key=lambda i: i["path"])}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "inventory.json").write_text(json.dumps(inv, indent=2))
    (out / "inventory.md").write_text(render_md(inv))
    print(f"inventory: {len(bots)} bots, {len(items)} items, coverage {inv['coverage']} -> {out/'inventory.json'}")
    return 0

def human(n):
    n = n or 0
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024

def render_md(inv):
    L = [f"# Inventory ({inv['coverage']})", "", f"made {inv['created_at']}", "",
         "| bot | answered? | job | skills | data size | private |", "|---|---|---|---|---|---|"]
    for b in inv["bots"]:
        job = (b["description"] or "— not provided —").strip().splitlines()[0][:80]
        L.append(f"| {b['name']} | {b['status']} | {job} | {len(b['skills'])} | {human(b['data_bytes'])} | {'yes' if b['private'] else 'no'} |")
    pri = [i for i in inv["items"] if i["classification"] in ("private", "secret")]
    if pri:
        L += ["", "## Private or secret items (names only)", "", "| item | where | size | class |", "|---|---|---|---|"]
        for i in pri:
            L.append(f"| {Path(i['path']).name} | {Path(i['path']).parent} | {human(i['bytes'])} | {i['classification']} |")
    order = {"missing": 0, "refused": 1, "silent": 1, "setup": 2, "outside": 3}
    loose = []
    for b in inv["bots"]:
        for le in b["loose_ends"]:
            rank = next((v for k, v in order.items() if k in le), 4)
            loose.append((rank, f"{b['name']}: {le}"))
    loose.sort()
    L += ["", "## Top 3 loose ends", ""]
    L += [f"{n}. {t}" for n, (_, t) in enumerate(loose[:3], 1)] or ["none"]
    if len(loose) > 3:
        L.append(f"(+{len(loose) - 3} more in inventory.json)")
    return "\n".join(L) + "\n"

if __name__ == "__main__":
    sys.exit(main())
