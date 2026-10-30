"""Celery application. Without REDIS_URL, tasks run eagerly in-process (LOCAL mode)."""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("namasetu", include=["app.workers.tasks"])

if settings.redis_url:
    celery_app.conf.update(broker_url=settings.redis_url, result_backend=settings.redis_url)
else:
    celery_app.conf.update(
        broker_url="memory://",
        result_backend="cache+memory://",
        task_always_eager=True,
        task_eager_propagates=True,
        task_store_eager_result=True,  # so /admin/tasks/{id} can report eager results
    )

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_track_started=True,
)
