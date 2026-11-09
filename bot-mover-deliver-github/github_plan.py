#!/usr/bin/env python3
"""Plan a GitHub delivery. Pure logic: it never calls GitHub. The bot shows the
plan to the owner, gets a yes, then does exactly this with the GitHub connector.

  github_plan.py BUNDLE_DIR --repo NAME [--repo-exists] [--visibility private|public]
                 [--public-confirmed] [--lfs-approved] [--open-prs prs.json]
                 [--date YYYY-MM-DD] [--out plan.json]

prs.json: list of open PRs from the connector: [{"number":3,"title":"...","head":"branch"}]
Rules: new repos are private unless --visibility public AND --public-confirmed;
never push to main (branch + PR); reuse an open Bot Mover export PR; never merge;
files over 100 MiB are never in the commit list without --lfs-approved (they go
to the zip/Drive and get a README link); files over 50 MiB get a warning.
Exit 0 ok, 3 refused (needs an owner yes), 2 bad input.
"""
import argparse, datetime, json, os, sys
from pathlib import Path

BLOCK = 100 * 1024 * 1024
WARN = 50 * 1024 * 1024
BRANCH_PREFIX = "bot-mover/export-"

def build_plan(files, repo, repo_exists=False, visibility="private", public_confirmed=False,
               lfs_approved=False, open_prs=(), date=None):
    date = date or datetime.date.today().isoformat()
    refusals, warnings = [], []
    if visibility == "public" and not public_confirmed:
        refusals.append("public repo needs the one-line warning and the owner's yes (--public-confirmed)")
    reuse = next((p for p in open_prs if str(p.get("head", "")).startswith(BRANCH_PREFIX)
                  or str(p.get("title", "")).lower().startswith("bot mover export")), None)
    branch = reuse["head"] if reuse else f"{BRANCH_PREFIX}{date}"
    if branch in ("main", "master"):
        refusals.append("never push straight to main")
    commit, lfs, held = [], [], []
    for rel, size in files:
        if size > BLOCK:
            (lfs if lfs_approved else held).append({"path": rel, "bytes": size})
            if lfs_approved:
                commit.append(rel)
            continue
        if size > WARN:
            warnings.append(f"{rel} is {size / 1048576:.0f} MiB (GitHub warns over 50 MiB)")
        commit.append(rel)
    return {
        "repo": repo, "create_repo": not repo_exists,
        "visibility": "public" if (visibility == "public" and public_confirmed) else "private",
        "branch": branch, "base": "main",
        "pr_action": f"update open PR #{reuse['number']}" if reuse else "open a new PR",
        "merge": False, "change_sharing": False,
        "commit_files": commit, "lfs_files": lfs, "held_for_zip_or_drive": held,
        "readme_note": ("Big files are not in Git; get them from the zip or Drive folder: " + ", ".join(h["path"] for h in held)) if held else None,
        "warnings": warnings, "refusals": refusals,
    }

def list_files(root):
    root = Path(root)
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in (".venv", ".git")]
        for fn in fns:
            p = Path(dp) / fn
            out.append((p.relative_to(root).as_posix(), p.stat().st_size))
    return sorted(out)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle"); ap.add_argument("--repo", required=True)
    ap.add_argument("--repo-exists", action="store_true")
    ap.add_argument("--visibility", choices=["private", "public"], default="private")
    ap.add_argument("--public-confirmed", action="store_true"); ap.add_argument("--lfs-approved", action="store_true")
    ap.add_argument("--open-prs"); ap.add_argument("--date"); ap.add_argument("--out")
    a = ap.parse_args(argv)
    prs = json.loads(Path(a.open_prs).read_text()) if a.open_prs else []
    plan = build_plan(list_files(a.bundle), a.repo, a.repo_exists, a.visibility, a.public_confirmed, a.lfs_approved, prs, a.date)
    js = json.dumps(plan, indent=2)
    if a.out:
        Path(a.out).write_text(js)
    print(f"repo {plan['repo']} ({plan['visibility']}{', new' if plan['create_repo'] else ''}); branch {plan['branch']}; {plan['pr_action']}; never merge")
    print(f"{len(plan['commit_files'])} files to commit; {len(plan['held_for_zip_or_drive'])} held for zip/Drive; {len(plan['lfs_files'])} via LFS")
    for w in plan["warnings"]: print("warning: " + w)
    for r in plan["refusals"]: print("REFUSED: " + r)
    return 3 if plan["refusals"] else 0

if __name__ == "__main__":
    sys.exit(main())
