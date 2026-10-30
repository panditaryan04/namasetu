"""Summarise an HL7 Validator CLI output file (-output x.json) and exit non-zero on any error.

Usage: python scripts/check_fhir_validation.py path/to/validation.json
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path


def main() -> int:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    outcomes = (
        [e["resource"] for e in data.get("entry", [])] if data.get("resourceType") == "Bundle" else [data]
    )
    total: collections.Counter[str] = collections.Counter()
    failed = 0
    for oo in outcomes:
        name = next(
            (x.get("valueString", "?") for x in oo.get("extension", []) if "file" in x.get("url", "")), "?"
        )
        counts = collections.Counter(i["severity"] for i in oo.get("issue", []))
        total.update(counts)
        errors = counts["error"] + counts["fatal"]
        failed += bool(errors)
        status = "FAIL" if errors else "ok  "
        print(f"{status}  {Path(name).name:45} errors={errors} warnings={counts['warning']}")
        for i in oo.get("issue", []):
            if i["severity"] in {"error", "fatal"}:
                where = (i.get("expression") or [""])[0]
                print(f"      {where}: {i.get('details', {}).get('text') or i.get('diagnostics')}")
    print(f"\n{len(outcomes) - failed}/{len(outcomes)} files without errors; totals {dict(total)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
