from __future__ import annotations

from app.core.config import SAMPLE_DIR
from app.workers.tasks import ingest_icd11, ingest_namaste, ingest_synonyms_file, ping


def test_celery_runs_eagerly_without_redis() -> None:
    assert ping.delay().get(timeout=5) == "pong"


def test_ingest_tasks_are_idempotent_against_seeded_db() -> None:
    icd = ingest_icd11.delay().get(timeout=30)
    assert icd["mode"] == "mock" and icd["status"] == "unchanged" and icd["release"] == "icd11@SAMPLE-1"

    nam = ingest_namaste.delay(str(SAMPLE_DIR / "namaste_sample.csv"), "SAMPLE-1").get(timeout=30)
    assert nam["status"] == "unchanged" and nam["rejected_rows"] == 0

    syn = ingest_synonyms_file.delay(str(SAMPLE_DIR / "synonyms.yaml")).get(timeout=30)
    assert syn["status"] == "unchanged"
