"""WHO ICD-11 source: LIVE (WHO ICD-API v2, OAuth2 client credentials) or MOCK (bundled SAMPLE JSON).

Both return the same ICD11Snapshot, so everything downstream is mode-agnostic.

LIVE notes (verify against https://icd.who.int/docs/icd-api/APIDoc-Version2/):
- Token: POST {token_url} grant_type=client_credentials, scope=icdapi_access.
- Entities: GET https://id.who.int/icd/release/11/{release}/mms[/{id}] with headers
  API-Version: v2, Accept-Language: en. Fields used: code, blockId, classKind, title.@value,
  definition.@value, indexTerm[].label.@value, child[].
- The TM2 subtree is located by title match inside the traditional-medicine chapter (configurable),
  and DISORDER vs PATTERN is derived from whether an ancestor title contains "pattern".
- No biomedical links are inferred in LIVE mode: WHO linkage fields are not auto-detected, so links come
  only from the bundled WHO-index sample (MOCK) or from clinicians.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field

from app.core.config import Settings
from app.db.models import SYSTEM_BIO, SYSTEM_TM2
from app.ingest.schemas import ConceptIn, IngestReport, LinkIn, TermIn

logger = logging.getLogger("namasetu.icd11")

ProgressFn = Callable[[int, str], None]


class ICDEntity(BaseModel):
    code: str
    title: str
    parent: str | None = None
    class_kind: str = "category"
    definition: str | None = None
    index_terms: list[str] = Field(default_factory=list)
    bio_links: list[dict[str, str]] = Field(default_factory=list)
    uri: str | None = None


class ICD11Snapshot(BaseModel):
    release: str
    mode: Literal["live", "mock"]
    is_sample: bool
    tm2: list[ConceptIn]
    bio: list[ConceptIn]
    links: list[LinkIn]
    report: IngestReport


class ICD11Client(Protocol):
    mode: Literal["live", "mock"]

    def fetch(self, progress: ProgressFn | None = None) -> ICD11Snapshot: ...


# ---------------------------------------------------------------- shared conversion


def entities_to_concepts(entities: list[ICDEntity], system: str) -> list[ConceptIn]:
    """Convert entities to concepts. Blocks become categories; patterns inherit from ancestors."""
    by_code = {e.code: e for e in entities}

    def is_pattern(e: ICDEntity) -> bool:
        node: ICDEntity | None = e
        seen: set[str] = set()
        while node is not None and node.code not in seen:
            seen.add(node.code)
            if "pattern" in node.title.lower():
                return True
            node = by_code.get(node.parent) if node.parent else None
        return False

    out: list[ConceptIn] = []
    for e in entities:
        if e.class_kind in {"chapter", "block", "window"}:
            entity_type = "category"
        elif system == SYSTEM_TM2 and is_pattern(e):
            entity_type = "pattern"
        else:
            entity_type = "disorder"
        terms = [TermIn(term=e.title, kind="primary")]
        terms += [TermIn(term=t, kind="index_term") for t in e.index_terms if t and t != e.title]
        extra: dict[str, object] = {"index_terms": e.index_terms}
        if e.uri:
            extra["uri"] = e.uri
        out.append(
            ConceptIn(
                system=system,
                code=e.code,
                display=e.title,
                discipline="ayurveda" if system == SYSTEM_TM2 else "biomedicine",
                term_en=e.title,
                definition=e.definition,
                parent_code=e.parent,
                entity_type=entity_type,
                class_kind=e.class_kind,
                extra=extra,
                terms=terms,
            )
        )
    return out


def links_from_entities(tm2: list[ICDEntity], bio_codes: set[str], report: IngestReport) -> list[LinkIn]:
    links: list[LinkIn] = []
    for e in tm2:
        for link in e.bio_links:
            target = link.get("code", "")
            if target not in bio_codes:
                report.warnings.append(f"{e.code}: biomedical link target {target} not ingested; dropped")
                continue
            links.append(
                LinkIn(
                    source_system=SYSTEM_TM2,
                    source_code=e.code,
                    target_system=SYSTEM_BIO,
                    target_code=target,
                    relationship=link.get("relationship", "relatedto"),
                    origin=link.get("source", "who-index"),
                )
            )
    return links


# ---------------------------------------------------------------- MOCK


class MockICD11Client:
    """Serves bundled SAMPLE JSON through the same interface as the live client."""

    mode: Literal["live", "mock"] = "mock"

    def __init__(self, tm2: dict[str, Any], bio: dict[str, Any]) -> None:
        self._tm2 = tm2
        self._bio = bio

    @classmethod
    def from_files(cls, tm2_path: Path, bio_path: Path) -> MockICD11Client:
        return cls(
            json.loads(tm2_path.read_text(encoding="utf-8")), json.loads(bio_path.read_text(encoding="utf-8"))
        )

    def fetch(self, progress: ProgressFn | None = None) -> ICD11Snapshot:
        report = IngestReport(source="icd11")
        tm2 = [ICDEntity.model_validate(e) for e in self._tm2["entities"]]
        bio = [ICDEntity.model_validate(e) for e in self._bio["entities"]]
        report.total_rows = report.accepted = len(tm2) + len(bio)
        links = links_from_entities(tm2, {b.code for b in bio}, report)
        if progress:
            progress(report.total_rows, "loaded SAMPLE ICD-11 JSON")
        release = str(self._tm2.get("release", "SAMPLE"))
        return ICD11Snapshot(
            release=release,
            mode="mock",
            is_sample=True,
            tm2=entities_to_concepts(tm2, SYSTEM_TM2),
            bio=entities_to_concepts(bio, SYSTEM_BIO),
            links=links,
            report=report,
        )


# ---------------------------------------------------------------- LIVE


class ICD11ApiError(RuntimeError):
    pass


def _value(field: Any) -> str | None:
    if isinstance(field, dict):
        v = field.get("@value")
        return str(v) if v else None
    return None


class LiveICD11Client:
    mode: Literal["live", "mock"] = "live"

    def __init__(
        self,
        settings: Settings,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (settings.icd_client_id and settings.icd_client_secret):
            raise ICD11ApiError("ICD_CLIENT_ID and ICD_CLIENT_SECRET are required for LIVE mode")
        self.s = settings
        self.http = http or httpx.Client(timeout=30)
        self.sleep = sleep
        self.cache_dir = settings.icd11_cache_path / settings.icd11_release
        self._token: str | None = None
        self._token_expiry = 0.0
        self._last_request = 0.0
        self.requests_made = 0
        self.cache_hits = 0

    # -- transport

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        resp = self._send(
            lambda: self.http.post(
                self.s.icd11_token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.s.icd_client_id,
                    "client_secret": self.s.icd_client_secret,
                    "scope": "icdapi_access",
                },
            )
        )
        body = resp.json()
        self._token = str(body["access_token"])
        self._token_expiry = time.monotonic() + float(body.get("expires_in", 3600)) - 60
        return self._token

    def _send(self, do: Callable[[], httpx.Response]) -> httpx.Response:
        delay = 1.0
        for attempt in range(self.s.icd11_max_retries + 1):
            wait = self._last_request + 1.0 / self.s.icd11_max_rps - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = do()
                self.requests_made += 1
            except httpx.TransportError as exc:
                if attempt == self.s.icd11_max_retries:
                    raise ICD11ApiError(f"network error: {type(exc).__name__}") from exc
                self.sleep(delay)
                delay *= 2
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self.s.icd11_max_retries:
                    raise ICD11ApiError(f"HTTP {resp.status_code} after {attempt + 1} attempts")
                retry_after = resp.headers.get("Retry-After", "")
                self.sleep(float(retry_after) if retry_after.isdigit() else delay)
                delay *= 2
                continue
            if resp.status_code == 401:
                self._token = None
            if resp.status_code >= 400:
                raise ICD11ApiError(f"HTTP {resp.status_code} for {resp.request.url}")
            return resp
        raise ICD11ApiError("unreachable")  # pragma: no cover

    def get_json(self, uri: str) -> dict[str, Any]:
        url = uri.replace("http://id.who.int", self.s.icd11_api_base.rstrip("/"))
        cache_file = self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.json"
        if cache_file.exists():
            self.cache_hits += 1
            data: dict[str, Any] = json.loads(cache_file.read_text(encoding="utf-8"))
            return data
        headers = {
            "Authorization": f"Bearer {self._get_token()}",
            "Accept": "application/json",
            "Accept-Language": "en",
            "API-Version": "v2",
        }
        data = self._send(lambda: self.http.get(url, headers=headers)).json()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    # -- walking

    def _entity(self, uri: str, data: dict[str, Any], parent: str | None) -> ICDEntity:
        code = data.get("code") or data.get("blockId") or uri.rstrip("/").rsplit("/", 1)[-1]
        terms = [t for t in (_value(x.get("label")) for x in data.get("indexTerm", [])) if t]
        return ICDEntity(
            code=str(code),
            title=_value(data.get("title")) or str(code),
            parent=parent,
            class_kind=str(data.get("classKind", "category")),
            definition=_value(data.get("definition")),
            index_terms=terms,
            uri=uri,
        )

    def _walk(self, root_uri: str, root_parent: str | None, progress: ProgressFn | None) -> list[ICDEntity]:
        out: list[ICDEntity] = []
        stack: list[tuple[str, str | None]] = [(root_uri, root_parent)]
        while stack:
            uri, parent = stack.pop()
            data = self.get_json(uri)
            entity = self._entity(uri, data, parent)
            out.append(entity)
            if progress and len(out) % 25 == 0:
                progress(len(out), f"walked {len(out)} entities")
            stack.extend((child, entity.code) for child in reversed(data.get("child", [])))
        return out

    def _chapters(self) -> dict[str, str]:
        root = self.get_json(f"{self.s.icd11_api_base}/icd/release/11/{self.s.icd11_release}/mms")
        chapters: dict[str, str] = {}
        for uri in root.get("child", []):
            code = self.get_json(uri).get("code")
            if code:
                chapters[str(code)] = uri
        return chapters

    def _tm2_roots(self, chapter_uri: str, report: IngestReport) -> list[str]:
        """Blocks (up to 2 levels down) whose title contains the configured TM2 match string."""
        needle = self.s.icd11_tm2_title_match.lower()
        level = [chapter_uri]
        for _ in range(2):
            nxt: list[str] = []
            for uri in level:
                for child in self.get_json(uri).get("child", []):
                    if needle in (_value(self.get_json(child).get("title")) or "").lower():
                        return [child]
                    nxt.append(child)
            level = nxt
        report.warnings.append(
            f"no block titled like '{self.s.icd11_tm2_title_match}' in chapter "
            f"{self.s.icd11_tm_chapter}; ingested the whole chapter"
        )
        return [chapter_uri]

    def fetch(self, progress: ProgressFn | None = None) -> ICD11Snapshot:
        report = IngestReport(source="icd11")
        chapters = self._chapters()
        if self.s.icd11_tm_chapter not in chapters:
            raise ICD11ApiError(
                f"chapter {self.s.icd11_tm_chapter} not found in release {self.s.icd11_release}"
            )
        tm2: list[ICDEntity] = []
        for root in self._tm2_roots(chapters[self.s.icd11_tm_chapter], report):
            tm2 += self._walk(root, None, progress)
        bio: list[ICDEntity] = []
        for ch in self.s.biomed_chapter_list:
            if ch not in chapters:
                report.warnings.append(f"biomedical chapter {ch} not found; skipped")
                continue
            bio += self._walk(chapters[ch], None, progress)
        report.total_rows = report.accepted = len(tm2) + len(bio)
        report.warnings.append("LIVE mode: no WHO linkage field configured; biomedical links not imported")
        logger.info(
            "icd11 fetch done",
            extra={"extra_fields": {"requests": self.requests_made, "cache_hits": self.cache_hits}},
        )
        return ICD11Snapshot(
            release=self.s.icd11_release,
            mode="live",
            is_sample=False,
            tm2=entities_to_concepts(tm2, SYSTEM_TM2),
            bio=entities_to_concepts(bio, SYSTEM_BIO),
            links=[],
            report=report,
        )


def get_icd11_client(settings: Settings, sample_dir: Path) -> ICD11Client:
    if settings.effective_icd11_mode == "live":
        return LiveICD11Client(settings)
    return MockICD11Client.from_files(
        sample_dir / "icd11_tm2_sample.json", sample_dir / "icd11_bio_sample.json"
    )
