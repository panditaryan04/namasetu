"""Run the full evaluation and write docs/eval-results.md, docs/eval-results.json and the README summary.

Usage:
  python scripts/run_eval.py            # fresh temporary database seeded from the SAMPLE data (reproducible)
  python scripts/run_eval.py --use-db   # evaluate the configured DATABASE_URL and store an EvalRun there
  python scripts/run_eval.py --check    # do not write docs; only exit non-zero if a safety check fails
  python scripts/run_eval.py --use-db --check --if-missing   # container start: store a first run if none

Exit code 1 when any safety check is non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def fresh_environment() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="namasetu-eval-"))
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{tmp.as_posix()}/eval.db",
            "REDIS_URL": "",
            "ICD11_MODE": "mock",
            "ICD_CLIENT_ID": "",
            "ICD_CLIENT_SECRET": "",
            "ICD11_CACHE_DIR": str(tmp / "icd11-cache"),
            "SEMANTIC_MODE": os.environ.get("SEMANTIC_MODE", "off"),
            "RANKER_ARTIFACT": str(tmp / "ranker.json"),
        }
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-db", action="store_true", help="evaluate DATABASE_URL and store the run there")
    ap.add_argument("--check", action="store_true", help="do not write docs")
    ap.add_argument("--if-missing", action="store_true", help="with --use-db: skip if a run exists")
    args = ap.parse_args()
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    if not args.use_db:
        fresh_environment()

    from app.core.logging import configure_logging
    from app.db.migrate import upgrade_to_head
    from app.db.seed import load_bundle, seed_sample
    from app.db.session import get_sessionmaker
    from app.eval.harness import latest_run, run_all, store
    from app.eval.report import markdown, readme_block, replace_block
    from app.main import create_app

    configure_logging("WARNING")
    upgrade_to_head()
    with get_sessionmaker()() as session:
        if args.use_db and args.if_missing and latest_run(session) is not None:
            print("an evaluation run is already stored; skipping")
            return 0
        if not args.use_db:
            seed_sample(session, load_bundle(use_embedded=True))
            session.commit()
        out = run_all(session, create_app())
        if args.use_db:
            run = store(session, out, "run_eval.py")
            print(f"stored EvalRun {run.id}")

    docs = ROOT.parent / "docs"
    if not args.check:
        (docs / "eval-results.md").write_text(markdown(out), encoding="utf-8", newline="\n")
        (docs / "eval-results.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1, default=str) + "\n", encoding="utf-8", newline="\n"
        )
        # The frontend tests render this real run (the frontend image is built without docs/).
        fixture = ROOT.parent / "frontend" / "tests" / "fixtures" / "eval-run.json"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text(
            json.dumps(out, ensure_ascii=False, default=str) + "\n", encoding="utf-8", newline="\n"
        )
        readme = ROOT.parent / "README.md"
        readme.write_text(
            replace_block(readme.read_text(encoding="utf-8"), readme_block(out)),
            encoding="utf-8",
            newline="\n",
        )
        print("wrote docs/eval-results.*, the frontend fixture and the README summary")

    checks = out["results"]["safety"]["checks"]
    for name, n in checks.items():
        print(f"  {'FAIL' if n else 'ok  '} {name}: {n}")
    s = out["summary"]
    print(
        f"hybrid top-1 {s['hybrid_top1']} on {s['test_queries']} TEST queries; "
        f"FHIR {s['fhir_resources']} resources / {s['fhir_errors']} errors; {out['duration_s']} s"
    )
    if not out["safety_ok"]:
        print("SAFETY CHECKS FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
