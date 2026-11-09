#!/usr/bin/env python3
"""Screen files for secrets and sensitive numbers. Never prints the values.

  secret_scan.py scan PATH [PATH...] [--json OUT] [--fail-on-find]
  secret_scan.py redact-copy SRC DST [--json OUT]     # copy SRC tree to DST with redactions

Result is labeled "screened", never "certified": patterns can miss things.
Whole-secret files (.env, private keys, credential files) are never copied by
redact-copy; they are reported as kind=secret_file instead.
"""
import argparse, json, os, re, shutil, sys
from pathlib import Path

SECRET_FILE_NAMES = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.pem|.*\.key|id_rsa.*|id_ed25519.*|id_ecdsa.*|\.netrc|\.pgpass|"
    r"credentials(\.json)?|.*\.p12|.*\.pfx|\.npmrc|\.pypirc|token(s)?\.json|client_secret.*\.json)$",
    re.I)
SAFE_ENV_NAMES = re.compile(r"\.env\.(example|sample|template)$", re.I)

PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("openai_style_key", re.compile(r"\bsk-(proj-|ant-|live_|test_)?[A-Za-z0-9_\-]{20,}\b")),
    ("stripe_key", re.compile(r"\b(rk|pk|sk)_(live|test)_[A-Za-z0-9]{16,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{20,}=*")),
    ("url_password", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s:/@]+:[^\s@/]{3,}@")),
    # key = value assignments for secret-ish names (also .env style lines)
    ("assigned_secret", re.compile(
        r"(?im)^(?P<pre>\s*(export\s+)?[\"']?[A-Z0-9_.\-]*(api[_\-]?key|secret|token|passw(or)?d|passwd|pwd|auth|credential|private[_\-]?key|access[_\-]?key)[A-Z0-9_.\-]*[\"']?\s*[:=]\s*)(?P<val>[\"']?[^\s\"'#,}]{6,}[\"']?)")),
    ("ssn", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    ("routing_number", re.compile(r"(?i)\b(routing|aba|rtn)\b[^\n\d]{0,25}(?P<val>\d{9})\b")),
    ("account_number", re.compile(r"(?i)\b(account|acct|iban)\b[^\n\d]{0,25}(?P<val>[A-Z]{0,4}\d[\d -]{4,24}\d)\b")),
    ("card_number", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
]

def luhn_ok(digits):
    s, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9: d -= 9
        s += d; alt = not alt
    return s % 10 == 0

def _keep(kind, m):
    if kind == "card_number":
        d = re.sub(r"\D", "", m.group(0))
        return 13 <= len(d) <= 19 and luhn_ok(d) and len(set(d)) > 1
    if kind == "assigned_secret":
        raw = m.group("val")
        v = raw.strip("\"'")
        if raw[:1] not in "\"'" and ("(" in v or "[" in v):
            return False  # unquoted code expression (a call), not a literal secret
        # skip obvious placeholders and env lookups
        if re.match(r"(?i)^(\[REDACTED|\$\{?|<|%|os\.environ|getenv|process\.env|fill[_-]?in|changeme|xxx+|\*+|none|null|true|false|placeholder|your[_-])", v):
            return False
        return True
    return True

def redact_text(text):
    """Return (new_text, findings) where findings = [(kind, line_no)]. Values never returned."""
    findings, spans = [], []
    for kind, rx in PATTERNS:
        for m in rx.finditer(text):
            if not _keep(kind, m):
                continue
            g = "val" if "val" in rx.groupindex and m.group("val") else 0
            a, b = m.span(g)
            if any(a < y and x < b for x, y in spans):
                continue
            spans.append((a, b))
            findings.append((kind, text.count("\n", 0, a) + 1, a, b))
    out, last = [], 0
    for kind, line, a, b in sorted(findings, key=lambda f: f[2]):
        out.append(text[last:a]); out.append(f"[REDACTED:{kind}]"); last = b
    out.append(text[last:])
    return "".join(out), [(k, l) for k, l, _, _ in sorted(findings, key=lambda f: f[2])]

def is_secret_file(path):
    p = str(path).replace("\\", "/")
    return bool(SECRET_FILE_NAMES.search(p)) and not SAFE_ENV_NAMES.search(p)

def read_text(path, limit=20 * 1024 * 1024):
    try:
        if os.path.getsize(path) > limit:
            return None
        data = Path(path).read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:8192]:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1")

def iter_files(root):
    root = Path(root)
    if root.is_file() or root.is_symlink():
        yield root; return
    for dp, dns, fns in os.walk(root):
        for fn in fns:
            yield Path(dp) / fn

def scan(paths):
    report = {"label": "screened", "note": "pattern screen only; not a certification", "findings": [], "secret_files": [], "files_scanned": 0}
    for p in paths:
        for f in iter_files(p):
            if f.is_symlink():
                continue
            report["files_scanned"] += 1
            if is_secret_file(f):
                report["secret_files"].append(str(f)); continue
            t = read_text(f)
            if t is None:
                continue
            _, fs = redact_text(t)
            for kind, line in fs:
                report["findings"].append({"file": str(f), "line": line, "kind": kind})
    report["count"] = len(report["findings"]) + len(report["secret_files"])
    return report

def redact_copy_file(src, dst, report, rel=None):
    rel = rel or str(src)
    if is_secret_file(src):
        report["secret_files"].append(rel); return False
    t = read_text(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if t is None:
        shutil.copy2(src, dst); return True
    new, fs = redact_text(t)
    for kind, line in fs:
        report["findings"].append({"file": rel, "line": line, "kind": kind})
    if fs:
        dst.write_text(new, encoding="utf-8")
        shutil.copystat(src, dst)
    else:
        shutil.copy2(src, dst)
    return True

def redact_copy(src, dst):
    report = {"label": "screened", "note": "pattern screen only; not a certification", "findings": [], "secret_files": [], "files_scanned": 0}
    src, dst = Path(src), Path(dst)
    for f in iter_files(src):
        rel = f.relative_to(src) if src.is_dir() else Path(f.name)
        report["files_scanned"] += 1
        redact_copy_file(f, dst / rel if src.is_dir() else dst, report, str(rel))
    report["count"] = len(report["findings"]) + len(report["secret_files"])
    return report

def summary(report):
    kinds = {}
    for f in report["findings"]:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    parts = [f"{v} {k}" for k, v in sorted(kinds.items())]
    if report["secret_files"]:
        parts.append(f"{len(report['secret_files'])} secret file(s) withheld")
    return f"screened {report['files_scanned']} files: " + (", ".join(parts) if parts else "no secret patterns found")

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan"); s.add_argument("paths", nargs="+"); s.add_argument("--json"); s.add_argument("--fail-on-find", action="store_true")
    r = sub.add_parser("redact-copy"); r.add_argument("src"); r.add_argument("dst"); r.add_argument("--json")
    a = ap.parse_args(argv)
    rep = scan(a.paths) if a.cmd == "scan" else redact_copy(a.src, a.dst)
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=2))
    print(summary(rep))
    for f in rep["findings"]:
        print(f"  {f['kind']}: {f['file']} line {f['line']}")
    for f in rep["secret_files"]:
        print(f"  secret_file: {f}")
    return 1 if (a.cmd == "scan" and a.fail_on_find and rep["count"]) else 0

if __name__ == "__main__":
    sys.exit(main())
