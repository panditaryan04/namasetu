"""Regenerate data/sample/embedded_db.json (the single-file seed for LOCAL mode) from the sample sources.

Usage: python scripts/build_embedded_db.py [--check]
--check exits non-zero if the committed file is out of date (used in CI).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import SAMPLE_DIR
from app.db.seed import EMBEDDED_FILE, build_embedded_bundle


def render() -> str:
    return json.dumps(build_embedded_bundle(), ensure_ascii=False, indent=1) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = SAMPLE_DIR / EMBEDDED_FILE
    content = render()
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != content:
            print(f"{target} is out of date; run scripts/build_embedded_db.py")
            return 1
        print(f"{target.name} is up to date")
        return 0
    target.write_text(content, encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(content):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
