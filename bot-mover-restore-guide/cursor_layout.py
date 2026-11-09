#!/usr/bin/env python3
"""Lay a Bot Mover bundle out for Cursor. Writes into a NEW folder; the bundle is not changed.

  cursor_layout.py BUNDLE_DIR --out PROJECT_DIR

PROJECT_DIR/.cursor/skills/<bot>/SKILL.md   one per bot (call it as /<bot>)
PROJECT_DIR/.cursor/skills/<skill>/...      the bot's skills and helpers
PROJECT_DIR/data/...                        the bundle's data (set DATA_ROOT to it)
Skips bots whose profile wasn't provided or is withheld. Refuses a non-empty --out.
.cursor/agents is only for helper/worker agents; schedules are a separate Automations setup.
"""
import argparse, json, re, shutil, sys
from pathlib import Path

def read_bot(p):
    t = p.read_text()
    m = re.match(r"---\n(.*?)\n---\n(.*)", t, re.S)
    fm = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(": ")
        try:
            fm[k] = json.loads(v)
        except ValueError:
            fm[k] = v
    return fm, m.group(2).strip()

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle"); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    b, out = Path(a.bundle), Path(a.out)
    if out.exists() and any(out.iterdir()):
        print(f"refusing: {out} is not empty"); return 3
    sk = out / ".cursor" / "skills"; sk.mkdir(parents=True)
    made, skipped = [], []
    for bm in sorted((b / "bots").glob("*/bot.md")):
        fm, body = read_bot(bm)
        slug = bm.parent.name
        if fm.get("status") != "answered" or body.startswith("_"):
            skipped.append(slug); continue
        skills = fm.get("skills") or []
        if slug in skills or (sk / slug).exists():
            slug = slug + "-bot"  # a skill already uses this name
        d = sk / slug; d.mkdir()
        desc = (fm.get("title") or fm.get("name") or slug).replace("\n", " ")
        lines = ["---", f"name: {slug}", f"description: {json.dumps('use this when you want ' + desc)}", "---", "", body, ""]
        if skills:
            lines += ["## Skills this bot uses", ""] + [f"- /{s} (in .cursor/skills/{s}/)" for s in skills]
        lines += ["", "Set DATA_ROOT to this project's data folder before running helper scripts."]
        (d / "SKILL.md").write_text("\n".join(lines) + "\n")
        for s in skills:
            src = b / "skills" / s
            if src.is_dir() and not (sk / s).exists():
                shutil.copytree(src, sk / s)
        made.append(slug)
    if (b / "data").is_dir():
        shutil.copytree(b / "data", out / "data")
    print(f"cursor layout: {len(made)} bot(s) {made}; skipped {skipped}; set DATA_ROOT={out / 'data'}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
