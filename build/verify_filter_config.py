#!/usr/bin/env python3
"""verify_filter_config.py — the filter's two config files must agree.

WHY THIS IS PINNED
------------------
Filtering is split across two hand-edited files: `config/template.yml` *defines* named rules and
the `filter:` line in `config/source.yml` *applies* them. These are edited in the GitHub web editor
far more often than the code is touched, and they break in three ways:

  1. `filter:` names a template that doesn't exist   -> the build fails ~2 minutes in, after
     downloading Tuliprox, for what is really a typo.
  2. a template is defined but never referenced      -> WORSE, because it fails silently: the edit
     looks done, the build goes green, and nothing changed. (This already happened once, with
     leftover DROP_SHOPPING / DROP_ADULT templates.)
  3. a regex doesn't compile                         -> same late, expensive failure as (1).

All three are caught here in milliseconds, with no network and no credentials.

Run: python3 build/verify_filter_config.py
"""
from __future__ import annotations

import pathlib
import re
import sys

try:
    import yaml
except ImportError:
    print("SKIP: PyYAML is not available for this interpreter")
    raise SystemExit(0)

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCE = REPO / "config" / "source.yml"
TEMPLATE = REPO / "config" / "template.yml"

# Tuliprox references a template as !NAME! inside a filter expression.
TEMPLATE_REF = re.compile(r"!([A-Z0-9_]+)!")
# A rule body looks like:  Group ~ "(?i)^...$"   — pull the quoted regex out of it.
QUOTED_REGEX = re.compile(r'~\s*"((?:[^"\\]|\\.)*)"')


def main() -> int:
    source = yaml.safe_load(SOURCE.read_text(encoding="utf-8")) or {}
    template = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8")) or {}

    defined = {t["name"]: str(t.get("value", "")) for t in (template.get("templates") or [])}

    referenced: set[str] = set()
    targets = 0
    for src in source.get("sources") or []:
        for target in src.get("targets") or []:
            targets += 1
            referenced |= set(TEMPLATE_REF.findall(target.get("filter") or ""))

    problems: list[str] = []

    if not targets:
        problems.append("source.yml declares no targets")

    missing = sorted(referenced - set(defined))
    if missing:
        problems.append(
            f"filter references undefined template(s): {', '.join(missing)} "
            f"— the build will fail on config parse")

    unused = sorted(set(defined) - referenced)
    if unused:
        problems.append(
            f"template(s) defined but never referenced by any filter: {', '.join(unused)} "
            f"— they do NOTHING; add them to filter: or delete them")

    for name, value in sorted(defined.items()):
        for pattern in QUOTED_REGEX.findall(value):
            # Tuliprox's regex engine is Rust, Python's is not identical, but a pattern that fails
            # to compile in Python is malformed in any engine — unbalanced parens, bad escapes.
            try:
                re.compile(pattern)
            except re.error as exc:
                problems.append(f"template {name}: regex does not compile — {exc}")

    print(f"targets: {targets}   templates defined: {len(defined)}   referenced: {len(referenced)}")
    for name in sorted(referenced):
        print(f"  applied: {name}")

    if problems:
        print()
        for problem in problems:
            print(f"FAILED: {problem}")
        return 1

    print("\nok: every referenced template exists, every defined template is used, "
          "every regex compiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
