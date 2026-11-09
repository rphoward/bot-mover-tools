#!/usr/bin/env python3
"""Check the final chat report before sending it to the owner.

  report_check.py report.txt        (or pipe the text on stdin)

Passes when the report is 5 lines or fewer (3-5 is the target) and has: a link,
counts (a number), what's excluded, and the gaps (or says there are none).
"""
import re, sys

def check(text):
    lines = [l for l in text.strip().splitlines() if l.strip()]
    probs = []
    if len(lines) > 5: probs.append(f"{len(lines)} lines (max 5)")
    if not re.search(r"https?://|grokbot://|\]\(", text): probs.append("no link")
    if not re.search(r"\d", text): probs.append("no counts")
    if not re.search(r"(?i)exclud|left out|not included|didn't come|not shipped|held back", text): probs.append("doesn't say what's excluded")
    if not re.search(r"(?i)gap|missing|loose end|didn't answer|refused|no gaps", text): probs.append("doesn't name the gaps")
    return probs

def main():
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    p = check(text)
    print("REPORT OK" if not p else "REPORT NEEDS WORK: " + "; ".join(p))
    return 0 if not p else 1

if __name__ == "__main__":
    sys.exit(main())
