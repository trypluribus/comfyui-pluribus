"""Private, cancellable identity-analysis jobs for local ComfyUI media."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from typing import Callable, Sequence

from .identity_analyzers import (
    AnalysisCancelled,
    AnalyzedOccurrence,
    IdentityAnalyzer,
    MAX_CLUSTER_CANDIDATE_COMPARISONS,
    OpenCVYuNetSFaceAnalyzer,
    cluster_occurrences,
    stable_occurrence_id,
)
from .identity_models import (
    FaceOccurrence,
    IdentityCandidate,
    PersonLink,
    SourceRecord,
)
from .identity_models_install import IdentityModelInstaller
from .storage import ensure_private_dir, write_private_json


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,159}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
LOGGER = logging.getLogger(__name__)
DEFAULT_MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024 * 1024
DEFAULT_MAX_IMAGE_PIXELS = 100_000_000
DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES = 256 * 1024 * 1024


class IdentityCapacityError(ValueError):
    """Raised when the bounded local identity-analysis queue is full."""


class IdentityConflictError(ValueError):
    """Raised when producer links changed after a client last read them."""


def discover_comfyui_media_roots() -> list[str]:
    roots: list[str] = []
    configured = os.environ.get("PLURIBUS_IDENTITY_MEDIA_ROOTS", "")
    roots.extend(path for path in configured.split(os.pathsep) if path)
    try:
        import folder_paths  # type: ignore[import-not-found]

        for getter_name in (
            "get_input_directory",
            "get_output_directory",
            "get_temp_directory",
        ):
            getter = getattr(folder_paths, getter_name, None)
            if getter:
                value = getter()
                if value:
                    roots.append(value)
    except ImportError:
        pass
    return sorted({os.path.realpath(path) for path in roots if os.path.isdir(path)})


class LocalMediaResolver:
    """Resolve only files inside explicitly allowed ComfyUI media roots."""

    def __init__(
        self,
        roots: Sequence[str] | None = None,
        annotated_resolver: Callable[[str], str] | None = None,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        max_total_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
    ):
        selected = list(roots) if roots is not None else discover_comfyui_media_roots()
        self.roots = tuple(
            sorted({os.path.realpath(path) for path in selected if os.path.isdir(path)})
        )
        self.annotated_resolver = annotated_resolver
        self.max_source_bytes = max(1, max_source_bytes)
        self.max_total_bytes = max(1, max_total_bytes)
        self.max_image_pixels = max(1, max_image_pixels)
        if self.annotated_resolver is None:
            try:
                import folder_paths  # type: ignore[import-not-found]

                self.annotated_resolver = getattr(
                    folder_paths, "get_annotated_filepath", None
                )
            except ImportError:
                pass

    def resolve_many(
        self,
        values: object,
        cancel_event: threading.Event | None = None,
    ) -> tuple[list[SourceRecord], list[dict]]:
        if not isinstance(values, list):
            raise ValueError("sources must be a list.")
        if len(values) > 500:
            raise ValueError("sources may contain at most 500 entries.")
        records: list[SourceRecord] = []
        issues: list[dict] = []
        seen: set[str] = set()
        resolved_bytes = 0
        for index, value in enumerate(values):
            if cancel_event and cancel_event.is_set():
                raise AnalysisCancelled("Identity analysis was canceled.")
            if not isinstance(value, dict):
                raise ValueError("Each identity source must be an object.")
            source_key = str(
                value.get("sourceKey")
                or value.get("source_key")
                or value.get("path")
                or ""
            ).strip()
            supplied_ref = str(
                value.get("sourceRef") or value.get("source_ref") or ""
            ).lower()
            if not SHA256.fullmatch(supplied_ref):
                raise ValueError(
                    "Each identity sourceRef must be a lowercase SHA-256 digest "
                    "minted for the current workflow."
                )
            source_ref = supplied_ref
            if source_ref in seen:
                raise ValueError("Each identity sourceRef may appear only once.")
            seen.add(source_ref)
            label = str(
                value.get("displayLabel")
                or value.get("sourceLabel")
                or value.get("display_name")
                or os.path.basename(source_key)
                or f"Source {index + 1}"
            ).strip()[:200]
            path = self._resolve_path(source_key)
            if path is None:
                issues.append(
                    _issue(
                        f"source_unavailable_{source_ref[:12]}",
                        "warning",
                        "Source could not be opened",
                        "The source is missing or outside the configured ComfyUI media directories.",
                        source_ref=source_ref,
                        code="source_unavailable",
                    )
                )
                continue
            media_type = _media_type(path)
            byte_size = os.path.getsize(path)
            if byte_size > self.max_source_bytes:
                issues.append(
                    _issue(
                        f"source_file_limit_{source_ref[:12]}",
                        "warning",
                        "Source exceeds the local analysis file limit",
                        (
                            f"This source is larger than the {self.max_source_bytes:,}-byte "
                            "per-file limit. Use a shorter or compressed local copy."
                        ),
                        source_ref=source_ref,
                        code="source_file_too_large",
                    )
                )
                continue
            if resolved_bytes + byte_size > self.max_total_bytes:
                issues.append(
                    _issue(
                        f"source_total_limit_{source_ref[:12]}",
                        "warning",
                        "Identity sources exceed the local analysis byte budget",
                        (
                            f"Adding this source would exceed the {self.max_total_bytes:,}-byte "
                            "job limit. Analyze fewer sources, then run another job."
                        ),
                        source_ref=source_ref,
                        code="source_total_bytes_exceeded",
                    )
                )
                continue
            if media_type == "image":
                pixel_count = _image_pixel_count(path)
                if pixel_count is not None and pixel_count > self.max_image_pixels:
                    issues.append(
                        _issue(
                            f"source_pixel_limit_{source_ref[:12]}",
                            "warning",
                            "Image exceeds the local analysis pixel limit",
                            (
                                f"Images above {self.max_image_pixels:,} pixels are skipped "
                                "to keep local analysis responsive. Resize the image and retry."
                            ),
                            source_ref=source_ref,
                            code="source_image_pixels_exceeded",
                        )
                    )
                    continue
            record = SourceRecord(
                source_ref=source_ref,
                media_type=media_type,
                source_hash=_sha256_file(path, cancel_event),
                local_path=path,
                display_label=label,
                byte_size=byte_size,
            )
            records.append(record)
            resolved_bytes += byte_size
            if media_type not in {"image", "video"}:
                title = (
                    "Voice identity analysis is not available yet"
                    if media_type == "audio"
                    else "Source type is not visually analyzable"
                )
                issues.append(
                    _issue(
                        f"source_unsupported_{source_ref[:12]}",
                        "info",
                        title,
                        (
                            "The source remains in rights coverage, but the current local "
                            "identity model only analyzes images and video."
                        ),
                        source_ref=source_ref,
                        code="source_unsupported",
                    )
                )
                continue
        return records, issues

    def _resolve_path(self, source_key: str) -> str | None:
        if not source_key or not self.roots:
            return None
        candidates: list[str] = []
        if self.annotated_resolver is not None:
            try:
                annotated = self.annotated_resolver(source_key)
                if annotated:
                    candidates.append(str(annotated))
            except (OSError, TypeError, ValueError):
                pass
        normalized_key = re.sub(r"\s+\[(?:input|output|temp)\]\s*$", "", source_key)
        if os.path.isabs(normalized_key):
            candidates.append(normalized_key)
        else:
            candidates.extend(os.path.join(root, normalized_key) for root in self.roots)
        for candidate in candidates:
            real = os.path.realpath(candidate)
            if not os.path.isfile(real):
                continue
            if any(_is_within(real, root) for root in self.roots):
                return real
        return None


class IdentityAnalysisService:
    # Version 2 bound occurrence/candidate identifiers to source content hashes.
    # Version 3 makes incomplete analysis explicit and source scoped so an old
    # cache can never be interpreted as complete identity coverage.
    SCHEMA_VERSION = 3

    def __init__(
        self,
        data_dir: str,
        *,
        analyzer: IdentityAnalyzer | None = None,
        media_roots: Sequence[str] | None = None,
        model_installer: IdentityModelInstaller | None = None,
        similarity_threshold: float = 0.38,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        max_total_source_bytes: int = DEFAULT_MAX_TOTAL_SOURCE_BYTES,
        max_image_pixels: int = DEFAULT_MAX_IMAGE_PIXELS,
        max_evidence_artifact_bytes: int = DEFAULT_MAX_EVIDENCE_ARTIFACT_BYTES,
        max_pending_jobs: int = 4,
    ):
        self.data_dir = os.path.join(data_dir, "identity")
        self.jobs_dir = os.path.join(self.data_dir, "jobs")
        self.links_dir = os.path.join(self.data_dir, "links")
        self.cache_dir = os.path.join(self.data_dir, "cache")
        self.model_dir = os.path.join(self.data_dir, "models")
        for path in (
            self.data_dir,
            self.jobs_dir,
            self.links_dir,
            self.cache_dir,
            self.model_dir,
        ):
            ensure_private_dir(path)
        self.installer = model_installer or IdentityModelInstaller(self.model_dir)
        self._requires_installed_model_bundle = analyzer is None
        paths = self.installer.paths()
        self.analyzer = analyzer or OpenCVYuNetSFaceAnalyzer(
            paths["yunet"], paths["sface"]
        )
        self.resolver = LocalMediaResolver(
            media_roots,
            max_source_bytes=max_source_bytes,
            max_total_bytes=max_total_source_bytes,
            max_image_pixels=max_image_pixels,
        )
        self.similarity_threshold = similarity_threshold
        self.max_evidence_artifact_bytes = max(1, max_evidence_artifact_bytes)
        self.max_pending_jobs = max(1, min(32, max_pending_jobs))
        self._jobs: dict[str, dict] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._analysis_semaphore = asyncio.Semaphore(1)
        self._lock = threading.RLock()

    def capabilities(self) -> dict:
        analyzer_status = self.analyzer.status()
        model_bundle = self.installer.status()
        model_bundle_ready = (
            bool(model_bundle.get("installed"))
            or not self._requires_installed_model_bundle
        )
        return {
            "state": (
                "ready"
                if analyzer_status.available and model_bundle_ready
                else "unavailable"
            ),
            "analyzer": analyzer_status.public_dict(),
            "modelBundle": model_bundle,
            "mediaRootsConfigured": bool(self.resolver.roots),
            "supportedMediaTypes": ["image", "video"],
            "unsupportedIdentityMediaTypes": ["audio", "lora", "prompt"],
            "resourceLimits": {
                "maxSourceBytes": self.resolver.max_source_bytes,
                "maxTotalSourceBytes": self.resolver.max_total_bytes,
                "maxImagePixels": self.resolver.max_image_pixels,
                "maxVideoFrames": getattr(self.analyzer, "max_video_frames", None),
                "maxFacesPerFrame": getattr(
                    self.analyzer, "max_faces_per_frame", None
                ),
                "maxOccurrences": getattr(
                    self.analyzer, "max_total_occurrences", None
                ),
                "maxInMemoryCropBytes": getattr(
                    self.analyzer, "max_total_crop_bytes", None
                ),
                "maxCropSide": getattr(self.analyzer, "max_crop_side", None),
                "maxEvidenceArtifactBytes": self.max_evidence_artifact_bytes,
                "maxPendingJobs": self.max_pending_jobs,
                "maxClusterCandidateComparisons": (
                    MAX_CLUSTER_CANDIDATE_COMPARISONS
                ),
            },
            "privacy": {
                "execution": "local",
                "uploadsMedia": False,
                "persistsEmbeddings": False,
                "returnsEmbeddings": False,
                "requiresProducerConfirmation": True,
            },
        }

    async def install_models(self, body: object) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Request body must be an object.")
        return await asyncio.to_thread(
            self.installer.install, body.get("modelId"), body.get("confirm")
        )

    async def start_job(self, body: object) -> dict:
        if not isinstance(body, dict):
            raise ValueError("Request body must be an object.")
        requested_sources = self._validate_requested_sources(body.get("sources", []))
        requested_count = len(requested_sources)
        job_id = str(uuid.uuid4())
        now = int(time.time() * 1000)
        created_order = time.time_ns()
        workflow_ref = str(body.get("workflowRef") or body.get("workflow_ref") or "")[
            :160
        ]
        job = {
            "jobId": job_id,
            "state": "queued",
            "cacheKey": None,
            "cacheHit": False,
            "createdAt": now,
            "createdOrder": created_order,
            "updatedAt": now,
            "workflowRef": workflow_ref,
            "workflowName": str(
                body.get("workflowName") or body.get("workflow_name") or ""
            )[:200],
            "workflowFingerprint": str(
                body.get("workflowFingerprint")
                or body.get("workflow_fingerprint")
                or ""
            )[:128],
            "requestedSources": requested_count,
            "resolvedSources": 0,
            "progress": {
                "completed": 0,
                "total": requested_count,
                "phase": "queued",
            },
            "deleteRequested": False,
        }
        cancel_event = threading.Event()
        with self._lock:
            pending_jobs = sum(not task.done() for task in self._tasks.values())
            if pending_jobs >= self.max_pending_jobs:
                raise IdentityCapacityError(
                    "Too many local identity analyses are already queued. Wait for "
                    "the current job or cancel it before starting another."
                )
            self._jobs[job_id] = job
            self._cancel_events[job_id] = cancel_event
            self._write_job(job)
            task = asyncio.create_task(
                self._run_job(
                    job_id,
                    requested_sources,
                    requested_count,
                    cancel_event,
                )
            )
            self._tasks[job_id] = task
        return self.get_job(job_id)

    @staticmethod
    def _validate_requested_sources(values: object) -> list[dict]:
        if not isinstance(values, list):
            raise ValueError("sources must be a list.")
        if len(values) > 500:
            raise ValueError("sources may contain at most 500 entries.")
        normalized: list[dict] = []
        seen_source_refs: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                raise ValueError("Each identity source must be an object.")
            source_ref = str(
                value.get("sourceRef") or value.get("source_ref") or ""
            ).lower()
            if not SHA256.fullmatch(source_ref):
                raise ValueError(
                    "Each identity sourceRef must be a lowercase SHA-256 digest "
                    "minted for the current workflow."
                )
            if source_ref in seen_source_refs:
                raise ValueError("Each identity sourceRef may appear only once.")
            seen_source_refs.add(source_ref)
            source_key = value.get("sourceKey", value.get("source_key", value.get("path")))
            if not isinstance(source_key, str):
                raise ValueError("Each identity sourceKey must be a string.")
            if len(source_key) > 4096:
                raise ValueError("Each identity sourceKey may contain at most 4096 characters.")
            normalized.append(dict(value))
        return normalized

    def get_job(self, job_id: str) -> dict:
        job = self._get_job_record(job_id)
        payload = {
            "jobId": job["jobId"],
            "state": job["state"],
            "cacheHit": bool(job.get("cacheHit")),
            "createdAt": job.get("createdAt"),
            "updatedAt": job.get("updatedAt"),
            "progress": dict(job.get("progress") or {}),
        }
        if job["state"] == "completed":
            cached = self._read_cache(job["cacheKey"])
            if cached is None:
                payload.update(
                    {
                        "state": "failed",
                        "coverage": self._coverage(job.get("requestedSources", 0), []),
                        "candidates": [],
                        "occurrences": [],
                        "issues": [
                            _issue(
                                "identity_cache_missing",
                                "error",
                                "Identity evidence is unavailable",
                                "The private cached result was removed. Run analysis again.",
                                code="cache_missing",
                            )
                        ],
                    }
                )
            else:
                payload.update(self._public_result(job_id, cached))
                link_payload = self.get_links(job_id)
                links = link_payload["links"]
                payload["links"] = links
                payload["linksRevision"] = link_payload["revision"]
                links_by_candidate: dict[str, list[dict]] = {}
                for link in links:
                    links_by_candidate.setdefault(link["candidateId"], []).append(link)
                for candidate in payload["candidates"]:
                    candidate_links = links_by_candidate.get(
                        candidate["candidateId"], []
                    )
                    if not candidate_links:
                        continue
                    confirmed_links = [
                        link for link in candidate_links if link["state"] == "confirmed"
                    ]
                    selected_occurrences = {
                        str(occurrence_id)
                        for link in confirmed_links
                        for occurrence_id in link.get("occurrenceIds") or []
                    }
                    candidate_occurrences = set(candidate["occurrenceIds"])
                    fully_confirmed = bool(confirmed_links) and (
                        not candidate_occurrences
                        or selected_occurrences == candidate_occurrences
                    )
                    partial = bool(selected_occurrences) and not fully_confirmed
                    candidate["state"] = (
                        "confirmed" if fully_confirmed else "needs_review"
                    )
                    candidate["needsReview"] = not fully_confirmed
                    candidate["partiallyConfirmed"] = partial
                    candidate["confirmedPeople"] = [
                        {
                            "personId": link.get("personId", ""),
                            "displayName": link.get("displayName", ""),
                            "occurrenceCount": len(link.get("occurrenceIds") or []),
                        }
                        for link in confirmed_links
                    ]
                    if (
                        len(confirmed_links) == 1
                        and confirmed_links[0].get("displayName")
                        and not candidate.get("suggestedName")
                    ):
                        candidate["suggestedName"] = confirmed_links[0][
                            "displayName"
                        ]
        elif job["state"] in {"failed", "canceled"}:
            payload.update(
                {
                    "coverage": self._coverage(job.get("requestedSources", 0), []),
                    "candidates": [],
                    "occurrences": [],
                    "issues": list(job.get("issues") or []),
                }
            )
        return payload

    async def cancel_job(self, job_id: str) -> dict:
        job = self._get_job_record(job_id)
        event = self._cancel_events.get(job_id)
        if event and job["state"] in {"queued", "running", "cancel_requested"}:
            event.set()
            job.update(
                {"state": "cancel_requested", "updatedAt": int(time.time() * 1000)}
            )
            self._write_job(job)
        return self.get_job(job_id)

    async def delete_job(self, job_id: str) -> dict:
        job = self._get_job_record(job_id)
        task = self._tasks.get(job_id)
        event = self._cancel_events.get(job_id)
        if task and not task.done():
            job["deleteRequested"] = True
            job["state"] = "cancel_requested"
            self._write_job(job)
            if event:
                event.set()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=15)
            except asyncio.TimeoutError:
                return {"jobId": job_id, "state": "deleting", "deleted": False}
        self._delete_job_files(job)
        return {"jobId": job_id, "state": "deleted", "deleted": True}

    def evidence_manifest(self, job_id: str) -> dict:
        job = self._get_job_record(job_id)
        if job["state"] != "completed":
            raise ValueError("Evidence is available only for completed analysis jobs.")
        cached = self._read_cache(job["cacheKey"])
        if cached is None:
            raise ValueError("Evidence was deleted.")
        public = self._public_result(job_id, cached)
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "jobId": job_id,
            "modelVersion": cached.get("modelVersion"),
            "sourceHashes": cached.get("sourceHashes", []),
            "candidates": public["candidates"],
            "occurrences": public["occurrences"],
            "notice": (
                "Evidence crops are derived from local source frames. Identity "
                "clusters are suggestions and are not proof of identity or clearance."
            ),
        }

    def artifact_path(self, job_id: str, artifact_id: str) -> str:
        if not SAFE_ARTIFACT.fullmatch(str(artifact_id or "")):
            raise ValueError("Invalid evidence artifact identifier.")
        job = self._get_job_record(job_id)
        cached = self._read_cache(job["cacheKey"])
        if cached is None or artifact_id not in set(cached.get("artifacts") or []):
            raise ValueError("Evidence artifact was not minted for this job.")
        directory = self._cache_path(job["cacheKey"])
        path = os.path.realpath(os.path.join(directory, artifact_id))
        if not _is_within(path, directory) or not os.path.isfile(path):
            raise ValueError("Evidence artifact is unavailable.")
        return path

    def get_links(self, job_id: str) -> dict:
        job = self._get_job_record(job_id)
        path = self._links_path_for_job(job)
        if not os.path.isfile(path):
            return {"jobId": job_id, "links": [], "revision": 0}
        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        revision = self._links_revision(document)
        cached = self._read_cache(job.get("cacheKey", "")) or {}
        occurrences_by_candidate = {
            str(candidate.get("candidateId")): set(candidate.get("occurrenceIds") or [])
            for candidate in cached.get("candidates", [])
        }
        links = []
        for stored_link in list(document.get("links") or []):
            candidate_id = str(stored_link.get("candidateId") or "")
            if candidate_id not in occurrences_by_candidate:
                continue
            selected = stored_link.get("occurrenceIds")
            if selected:
                retained = sorted(
                    set(str(value) for value in selected)
                    & occurrences_by_candidate[candidate_id]
                )
                if not retained:
                    continue
                stored_link = {**stored_link, "occurrenceIds": retained}
            links.append(stored_link)
        return {"jobId": job_id, "links": links, "revision": revision}

    def put_links(self, job_id: str, body: object) -> dict:
        with self._lock:
            return self._put_links_locked(job_id, body)

    def _put_links_locked(self, job_id: str, body: object) -> dict:
        job = self._get_job_record(job_id)
        if job["state"] != "completed":
            raise ValueError("Links can be saved only for a completed analysis job.")
        self._require_current_workflow_job(job)
        if not isinstance(body, dict) or not isinstance(body.get("links"), list):
            raise ValueError("links must be a list.")
        base_revision = body.get("baseRevision", body.get("base_revision"))
        if (
            isinstance(base_revision, bool)
            or not isinstance(base_revision, int)
            or base_revision < 0
        ):
            raise ValueError("baseRevision must be a non-negative integer.")
        links_path = self._links_path_for_job(job)
        if os.path.isfile(links_path):
            with open(links_path, "r", encoding="utf-8") as handle:
                existing_document = json.load(handle)
            current_revision = self._links_revision(existing_document)
        else:
            current_revision = 0
        if base_revision != current_revision:
            raise IdentityConflictError(
                "Identity link revision conflict. Reload the current links before saving."
            )
        cached = self._read_cache(job["cacheKey"]) or {}
        candidate_occurrences = {
            str(candidate.get("candidateId")): set(candidate.get("occurrenceIds") or [])
            for candidate in cached.get("candidates", [])
        }
        normalized: list[PersonLink] = []
        seen_links: set[tuple[str, str, str]] = set()
        confirmed_occurrence_owners: dict[tuple[str, str], str] = {}
        for value in body["links"]:
            if not isinstance(value, dict):
                raise ValueError("Each person link must be an object.")
            candidate_id = str(
                value.get("candidateId") or value.get("candidate_id") or ""
            )
            person_id = str(value.get("personId") or value.get("person_id") or "")
            if candidate_id not in candidate_occurrences:
                raise ValueError("candidateId was not minted for this job.")
            state = str(value.get("state") or "confirmed")
            if state not in {"confirmed", "rejected", "unsure"}:
                raise ValueError("Person link state is not supported.")
            if state == "confirmed" and not SAFE_ID.fullmatch(person_id):
                raise ValueError("A confirmed link requires an opaque personId.")
            if state != "confirmed" and person_id and not SAFE_ID.fullmatch(person_id):
                raise ValueError("personId must be an opaque identifier when supplied.")
            link_key = (candidate_id, person_id, state)
            if link_key in seen_links:
                raise ValueError("Each candidate and person link may appear only once.")
            seen_links.add(link_key)
            selected_value = value.get("occurrenceIds", value.get("occurrence_ids"))
            selected_occurrences: tuple[str, ...] = ()
            if selected_value is not None:
                if not isinstance(selected_value, list) or not selected_value:
                    raise ValueError(
                        "occurrenceIds must be a non-empty list when supplied."
                    )
                if len(selected_value) > 2000:
                    raise ValueError("occurrenceIds may contain at most 2000 entries.")
                selected_occurrences = tuple(
                    sorted({str(occurrence_id) for occurrence_id in selected_value})
                )
                if not set(selected_occurrences) <= candidate_occurrences[candidate_id]:
                    raise ValueError(
                        "Every occurrenceId must belong to the selected candidate in this job."
                    )
            if state == "confirmed" and candidate_occurrences[candidate_id]:
                if not selected_occurrences:
                    raise ValueError(
                        "A confirmed person link must select at least one occurrence."
                    )
                for occurrence_id in selected_occurrences:
                    owner_key = (candidate_id, occurrence_id)
                    prior_owner = confirmed_occurrence_owners.get(owner_key)
                    if prior_owner and prior_owner != person_id:
                        raise ValueError(
                            "One occurrence cannot be confirmed for two different people."
                        )
                    confirmed_occurrence_owners[owner_key] = person_id
            normalized.append(
                PersonLink(
                    candidate_id=candidate_id,
                    person_id=person_id,
                    state=state,
                    display_name=str(value.get("displayName") or "")[:160],
                    occurrence_ids=selected_occurrences,
                )
            )
        links = [
            link.public_dict()
            for link in sorted(
                normalized, key=lambda item: (item.candidate_id, item.person_id)
            )
        ]
        next_revision = current_revision + 1
        write_private_json(
            links_path,
            {
                "schemaVersion": 3,
                "analysisJobId": job_id,
                "analysisCacheKey": job.get("cacheKey"),
                "revision": next_revision,
                "links": links,
            },
        )
        return {"jobId": job_id, "links": links, "revision": next_revision}

    def delete_links(self, job_id: str, body: object) -> dict:
        with self._lock:
            job = self._get_job_record(job_id)
            self._require_current_workflow_job(job)
            if not isinstance(body, dict):
                raise ValueError("Request body must be an object.")
            base_revision = body.get("baseRevision", body.get("base_revision"))
            if (
                isinstance(base_revision, bool)
                or not isinstance(base_revision, int)
                or base_revision < 0
            ):
                raise ValueError("baseRevision must be a non-negative integer.")
            path = self._links_path_for_job(job)
            if not os.path.isfile(path):
                if base_revision != 0:
                    raise IdentityConflictError(
                        "Identity link revision conflict. Reload the current links "
                        "before clearing them."
                    )
                return {
                    "jobId": job_id,
                    "links": [],
                    "deleted": False,
                    "revision": 0,
                }
            with open(path, "r", encoding="utf-8") as handle:
                existing_document = json.load(handle)
            current_revision = self._links_revision(existing_document)
            if base_revision != current_revision:
                raise IdentityConflictError(
                    "Identity link revision conflict. Reload the current links before "
                    "clearing them."
                )
            deleted = bool(existing_document.get("links"))
            next_revision = current_revision + 1 if deleted else current_revision
            if deleted:
                write_private_json(
                    path,
                    {
                        "schemaVersion": 3,
                        "analysisJobId": job_id,
                        "analysisCacheKey": job.get("cacheKey"),
                        "revision": next_revision,
                        "links": [],
                    },
                )
            return {
                "jobId": job_id,
                "links": [],
                "deleted": deleted,
                "revision": next_revision,
            }

    @contextmanager
    def guard_unlinked_person_ids(
        self, workflow_ref: str, person_ids: set[str]
    ):
        """Hold link serialization while a caller deletes an unlinked person."""

        normalized_ids = {str(value) for value in person_ids if str(value)}
        with self._lock:
            path, document, linked_person_ids = self._workflow_link_document(
                workflow_ref
            )
            if normalized_ids & linked_person_ids:
                raise IdentityConflictError(
                    "This person still has visual identity assignments. Remove those "
                    "assignments before deleting the person."
                )
            # Fence any link editor that read before the person was deleted.
            # The editor's stale baseRevision will conflict after this lock is
            # released instead of recreating a just-deleted local reference. Do
            # this before yielding so storage failure cannot follow a successful
            # draft deletion; an unnecessary revision bump is safe if deletion
            # itself later fails.
            write_private_json(
                path,
                {
                    "schemaVersion": 3,
                    "analysisJobId": str(document.get("analysisJobId") or ""),
                    "analysisCacheKey": document.get("analysisCacheKey"),
                    "revision": self._links_revision(document) + 1,
                    "links": list(document.get("links") or []),
                },
            )
            yield

    def _workflow_link_document(
        self, workflow_ref: str
    ) -> tuple[str, dict, set[str]]:
        path = self._links_path_for_workflow_ref(workflow_ref)
        if not os.path.isfile(path):
            return path, {"revision": 0, "links": []}, set()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                document = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise IdentityConflictError(
                "Visual identity assignments could not be verified. Reopen People "
                "before deleting this person."
            ) from exc
        links = document.get("links") if isinstance(document, dict) else None
        if not isinstance(links, list):
            raise IdentityConflictError(
                "Visual identity assignments could not be verified. Reopen People "
                "before deleting this person."
            )
        linked_person_ids: set[str] = set()
        for link in links:
            if not isinstance(link, dict):
                raise IdentityConflictError(
                    "Visual identity assignments could not be verified. Reopen People "
                    "before deleting this person."
                )
            person_id = link.get("personId", link.get("person_id"))
            if person_id in (None, ""):
                continue
            if not isinstance(person_id, str) or not SAFE_ID.fullmatch(person_id):
                raise IdentityConflictError(
                    "Visual identity assignments could not be verified. Reopen People "
                    "before deleting this person."
                )
            linked_person_ids.add(person_id)
        return path, document, linked_person_ids

    @staticmethod
    def _links_revision(document: object) -> int:
        if not isinstance(document, dict):
            return 0
        revision = document.get("revision")
        if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0:
            return revision
        # A pre-revision document represents one prior write.  Treating it as
        # revision 1 provides a safe migration path without accepting a stale
        # first-write client at revision 0.
        return 1

    async def _run_job(
        self,
        job_id: str,
        requested_sources: list[dict],
        requested_count: int,
        cancel_event: threading.Event,
    ) -> None:
        job = self._get_job_record(job_id)

        def progress(
            completed: int,
            total: int,
            _source_ref: str,
            phase: str = "reading_media",
        ) -> None:
            with self._lock:
                current = self._jobs.get(job_id)
                if not current:
                    return
                current["progress"] = {
                    "completed": completed,
                    "total": total,
                    "phase": phase,
                }
                current["updatedAt"] = int(time.time() * 1000)
                self._write_job(current)

        def frame_progress(
            completed: int,
            total: int,
            source_ref: str,
            sampled_frames: int,
            sampled_frame_total: int,
            frame_index: int,
            timestamp_ms: int,
        ) -> None:
            with self._lock:
                current = self._jobs.get(job_id)
                if not current:
                    return
                current["progress"] = {
                    "completed": completed,
                    "total": total,
                    "phase": "reading_media",
                    "sourceRef": source_ref,
                    "sampledFrames": sampled_frames,
                    "sampledFrameTotal": sampled_frame_total,
                    "frameIndex": frame_index,
                    "timestampMs": timestamp_ms,
                }
                current["updatedAt"] = int(time.time() * 1000)
                self._write_job(current)

        progress.frame = frame_progress

        try:
            async with self._analysis_semaphore:
                if cancel_event.is_set():
                    raise AnalysisCancelled("Identity analysis was canceled.")
                job.update(
                    {
                        "state": "running",
                        "updatedAt": int(time.time() * 1000),
                        "progress": {
                            "completed": 0,
                            "total": requested_count,
                            "phase": "resolving_sources",
                        },
                    }
                )
                self._write_job(job)
                inventory_records, resolver_issues = await asyncio.to_thread(
                    self.resolver.resolve_many, requested_sources, cancel_event
                )
                if cancel_event.is_set():
                    raise AnalysisCancelled("Identity analysis was canceled.")
                records = [
                    record
                    for record in inventory_records
                    if record.media_type in {"image", "video"}
                ]
                analyzer_status = await asyncio.to_thread(self.analyzer.status)
                model_bundle = await asyncio.to_thread(self.installer.status)
                if cancel_event.is_set():
                    raise AnalysisCancelled("Identity analysis was canceled.")
                model_bundle_ready = (
                    bool(model_bundle.get("installed"))
                    or not self._requires_installed_model_bundle
                )
                cache_key = self._cache_key(
                    inventory_records,
                    resolver_issues,
                    requested_count,
                    analyzer_status,
                    model_bundle_ready,
                )
                job.update(
                    {
                        "cacheKey": cache_key,
                        "resolvedSources": len(inventory_records),
                        "updatedAt": int(time.time() * 1000),
                        "progress": {
                            "completed": 0,
                            "total": len(records),
                            "phase": "reading_media",
                        },
                    }
                )
                self._write_job(job)

                cached = self._read_cache(cache_key)
                if cached is not None:
                    job.update(
                        {
                            "state": "completed",
                            "cacheHit": True,
                            "updatedAt": int(time.time() * 1000),
                            "progress": {
                                "completed": len(records),
                                "total": len(records),
                                "phase": "complete",
                            },
                        }
                    )
                    return

                if (
                    not analyzer_status.available
                    or not model_bundle_ready
                    or not records
                ):
                    issues = [
                        *resolver_issues,
                        *[dict(issue) for issue in analyzer_status.issues],
                    ]
                    if not model_bundle_ready:
                        issues.append(
                            _issue(
                                "identity_models_unverified",
                                "error",
                                "Local identity models are not verified",
                                (
                                    "Install the checksum-verified YuNet and SFace bundle "
                                    "before local appearance grouping."
                                ),
                                code="models_unverified",
                            )
                        )
                    if cancel_event.is_set():
                        raise AnalysisCancelled("Identity analysis was canceled.")
                    result = self._empty_result(
                        requested_count,
                        inventory_records,
                        issues,
                        analyzer_status.model_version,
                        analyzed_count=0,
                    )
                    self._write_cache(cache_key, result)
                    job.update(
                        {
                            "state": "completed",
                            "updatedAt": int(time.time() * 1000),
                            "progress": {
                                "completed": len(records),
                                "total": len(records),
                                "phase": "complete",
                            },
                        }
                    )
                    return

                analyzed = await asyncio.to_thread(
                    self.analyzer.analyze, records, cancel_event, progress
                )
                if cancel_event.is_set():
                    raise AnalysisCancelled("Identity analysis was canceled.")
                progress(len(records), len(records), "", phase="grouping_people")
                cache = await asyncio.to_thread(
                    self._build_result,
                    job["cacheKey"],
                    records,
                    inventory_records,
                    analyzed,
                    [
                        *resolver_issues,
                        *[dict(issue) for issue in getattr(analyzed, "issues", ())],
                    ],
                    requested_count,
                    lambda: progress(
                        len(records), len(records), "", phase="building_evidence"
                    ),
                    cancel_event,
                )
            self._write_cache(job["cacheKey"], cache)
            job.update(
                {
                    "state": "completed",
                    "updatedAt": int(time.time() * 1000),
                    "progress": {
                        "completed": len(records),
                        "total": len(records),
                        "phase": "complete",
                    },
                }
            )
        except AnalysisCancelled:
            cache_key = job.get("cacheKey")
            if cache_key and not self._other_jobs_reference_cache(job_id, cache_key):
                shutil.rmtree(self._cache_path(cache_key), ignore_errors=True)
            job.update(
                {
                    "state": "canceled",
                    "updatedAt": int(time.time() * 1000),
                    "issues": [
                        _issue(
                            "identity_analysis_canceled",
                            "info",
                            "Identity analysis canceled",
                            "No identity result was retained for this job.",
                            code="canceled",
                        )
                    ],
                }
            )
        except Exception:
            LOGGER.exception("Local identity analysis job %s failed", job_id)
            cache_key = job.get("cacheKey")
            if cache_key and not self._other_jobs_reference_cache(job_id, cache_key):
                shutil.rmtree(self._cache_path(cache_key), ignore_errors=True)
            job.update(
                {
                    "state": "failed",
                    "updatedAt": int(time.time() * 1000),
                    "issues": [
                        _issue(
                            "identity_analysis_failed",
                            "error",
                            "Local identity analysis failed",
                            (
                                "The local analyzer could not finish. Review the "
                                "ComfyUI server log for technical details, then retry."
                            ),
                            code="analysis_failed",
                        )
                    ],
                }
            )
        finally:
            self._write_job(job)
            self._tasks.pop(job_id, None)
            self._cancel_events.pop(job_id, None)
            if job.get("deleteRequested"):
                self._delete_job_files(job)

    def _build_result(
        self,
        cache_key: str,
        records: list[SourceRecord],
        inventory_records: list[SourceRecord],
        analyzed: list[AnalyzedOccurrence],
        initial_issues: list[dict],
        requested_count: int,
        building_evidence_callback=None,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        directory = self._cache_path(cache_key)
        ensure_private_dir(directory)
        labels = {record.source_ref: record.display_label for record in records}
        source_hashes = {record.source_ref: record.source_hash for record in records}
        # Preserve AnalysisOutput's coverage/limit metadata while attaching the
        # resolver's trusted content hash to each analyzer observation.
        for index, item in enumerate(analyzed):
            analyzed[index] = replace(
                item, source_hash=source_hashes.get(item.source_ref, "")
            )
        completed_source_refs = (
            set(analyzed.completed_source_refs)
            if hasattr(analyzed, "completed_source_refs")
            else {record.source_ref for record in records}
        )
        completed_records = [
            record for record in records if record.source_ref in completed_source_refs
        ]
        incomplete_records = [
            record for record in records if record.source_ref not in completed_source_refs
        ]
        retained_analyzed: list[AnalyzedOccurrence] = []
        omitted_source_refs: set[str] = set()
        evidence_limited_source_refs: set[str] = set()
        artifact_bytes = 0
        artifact_limit_reached = False
        for item in sorted(
            analyzed,
            key=lambda value: stable_occurrence_id(value, self.analyzer.model_version),
        ):
            next_bytes = artifact_bytes + len(item.crop_bytes)
            if next_bytes > self.max_evidence_artifact_bytes:
                artifact_limit_reached = True
                omitted_source_refs.add(item.source_ref)
                continue
            retained_analyzed.append(item)
            artifact_bytes = next_bytes
        clusters, ambiguity = cluster_occurrences(
            retained_analyzed,
            self.analyzer.model_version,
            self.similarity_threshold,
        )
        if cancel_event and cancel_event.is_set():
            raise AnalysisCancelled("Identity analysis was canceled.")
        if building_evidence_callback:
            building_evidence_callback()
        public_occurrences: list[dict] = []
        public_candidates: list[dict] = []
        artifacts: list[str] = []
        occurrence_by_id: dict[str, FaceOccurrence] = {}
        for cluster in clusters:
            if cancel_event and cancel_event.is_set():
                raise AnalysisCancelled("Identity analysis was canceled.")
            candidate_id = cluster["candidateId"]
            cluster_occurrence_ids: list[str] = []
            cluster_sources: set[str] = set()
            cluster_crop_ids: list[str] = []
            for item in cluster["items"]:
                if cancel_event and cancel_event.is_set():
                    raise AnalysisCancelled("Identity analysis was canceled.")
                occurrence_id = stable_occurrence_id(item, self.analyzer.model_version)
                extension = (
                    item.crop_extension
                    if item.crop_extension in {".jpg", ".png"}
                    else ".jpg"
                )
                crop_id = f"crop_{occurrence_id}{extension}"
                _write_private_bytes(os.path.join(directory, crop_id), item.crop_bytes)
                artifacts.append(crop_id)
                occurrence = FaceOccurrence(
                    occurrence_id=occurrence_id,
                    candidate_id=candidate_id,
                    source_ref=item.source_ref,
                    source_label=labels.get(item.source_ref, ""),
                    media_type=item.media_type,
                    frame_index=item.frame_index,
                    timestamp_ms=item.timestamp_ms,
                    bbox=item.bbox,
                    confidence=item.confidence,
                    crop_artifact_id=crop_id,
                    ambiguous=occurrence_id in ambiguity,
                )
                occurrence_by_id[occurrence_id] = occurrence
                cluster_occurrence_ids.append(occurrence_id)
                cluster_sources.add(item.source_ref)
                cluster_crop_ids.append(crop_id)
            sheet_id = f"sheet_{candidate_id}.png"
            if artifact_bytes >= self.max_evidence_artifact_bytes:
                artifact_limit_reached = True
                evidence_limited_source_refs.update(cluster_sources)
                evidence = []
            else:
                try:
                    _build_evidence_sheet(
                        os.path.join(directory, sheet_id),
                        directory,
                        candidate_id,
                        cluster_crop_ids[:16],
                    )
                    sheet_bytes = os.path.getsize(os.path.join(directory, sheet_id))
                    if (
                        artifact_bytes + sheet_bytes
                        <= self.max_evidence_artifact_bytes
                    ):
                        artifact_bytes += sheet_bytes
                        artifacts.append(sheet_id)
                        evidence = [sheet_id]
                    else:
                        artifact_limit_reached = True
                        evidence_limited_source_refs.update(cluster_sources)
                        os.remove(os.path.join(directory, sheet_id))
                        evidence = []
                except ImportError:
                    evidence = []
            public_candidates.append(
                {
                    "candidateId": candidate_id,
                    "occurrenceIds": sorted(cluster_occurrence_ids),
                    "sourceRefs": sorted(cluster_sources),
                    "confidence": cluster["confidence"],
                    "groupingBand": cluster["groupingBand"],
                    "groupingLabel": cluster["groupingLabel"],
                    "evidenceArtifactIds": evidence,
                    "suggestedName": "",
                    "suggestedRole": "",
                    "suggestionSource": "",
                    "needsReview": True,
                }
            )
        for occurrence_id in sorted(occurrence_by_id):
            occurrence = occurrence_by_id[occurrence_id]
            public_occurrences.append(
                {
                    "occurrenceId": occurrence.occurrence_id,
                    "candidateId": occurrence.candidate_id,
                    "sourceRef": occurrence.source_ref,
                    "sourceLabel": occurrence.source_label,
                    "mediaType": occurrence.media_type,
                    "frameIndex": occurrence.frame_index,
                    "timestampMs": occurrence.timestamp_ms,
                    "bbox": list(occurrence.bbox),
                    "confidence": occurrence.confidence,
                    "cropArtifactId": occurrence.crop_artifact_id,
                    "ambiguous": occurrence.ambiguous,
                }
            )
        _apply_source_label_suggestions(public_candidates, public_occurrences, labels)
        issues = list(initial_issues)
        if artifact_limit_reached:
            issues.append(
                _issue(
                    "identity_evidence_artifact_limit_reached",
                    "warning",
                    "Identity evidence reached its private storage limit",
                    (
                        f"The job retained up to {self.max_evidence_artifact_bytes:,} "
                        "bytes of private face evidence. Review the partial result or "
                        "analyze fewer sources."
                    ),
                    code="evidence_artifact_limit_reached",
                )
            )
        retained_source_refs = {occurrence.source_ref for occurrence in retained_analyzed}
        omitted_only_source_refs = omitted_source_refs - retained_source_refs
        for source_ref in sorted(omitted_source_refs):
            omitted_only = source_ref in omitted_only_source_refs
            issues.append(
                _issue(
                    f"evidence_omitted_{source_ref[:16]}",
                    "warning",
                    (
                        "Visual source needs manual person review"
                        if omitted_only
                        else "Some source appearances were omitted"
                    ),
                    (
                        "Face evidence from this source could not be retained within "
                        "the private storage budget. Review the source manually or "
                        "analyze fewer inputs."
                    ),
                    source_ref=source_ref,
                    code=(
                        "evidence_omitted_source"
                        if omitted_only
                        else "evidence_source_truncated"
                    ),
                )
            )
        for source_ref in sorted(evidence_limited_source_refs - omitted_source_refs):
            issues.append(
                _issue(
                    f"evidence_truncated_{source_ref[:16]}",
                    "warning",
                    "Some source evidence could not be retained",
                    (
                        "The private storage budget prevented Pluribus from retaining "
                        "all review evidence for this source. Review the original source "
                        "manually or analyze fewer inputs."
                    ),
                    source_ref=source_ref,
                    code="evidence_source_truncated",
                )
            )
        coverage_records = [
            record
            for record in completed_records
            if record.source_ref not in omitted_only_source_refs
        ]
        source_refs_with_faces = retained_source_refs
        no_face_records = [
            record
            for record in coverage_records
            if record.source_ref not in source_refs_with_faces
        ]
        for record in no_face_records:
            issues.append(
                _issue(
                    f"no_face_{record.source_ref[:16]}",
                    "warning",
                    "Visual source needs manual person review",
                    (
                        "No clear face was detected. The source may still contain "
                        "body, silhouette, masked, distant, or otherwise rights-bearing "
                        "performance."
                    ),
                    source_ref=record.source_ref,
                    code="no_face_detected",
                )
            )
        for occurrence_id in sorted(ambiguity):
            candidate_id = occurrence_by_id[occurrence_id].candidate_id
            issues.append(
                _issue(
                    f"ambiguous_{occurrence_id}",
                    "warning",
                    "Appearance needs identity review",
                    "This appearance was close to more than one identity cluster.",
                    candidate_id=candidate_id,
                    code="ambiguous_identity",
                )
            )
        issues, manual_review_sources = _manual_review_contract(
            inventory_records,
            issues,
            required_source_refs={record.source_ref for record in incomplete_records},
        )
        coverage = self._coverage(
            requested_count,
            inventory_records,
            len(public_occurrences),
            analyzed_count=len(coverage_records),
        )
        if manual_review_sources:
            coverage["manualReviewSources"] = len(manual_review_sources)
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "modelVersion": self.analyzer.model_version,
            "sourceHashes": [
                {"sourceRef": record.source_ref, "sourceHash": record.source_hash}
                for record in sorted(
                    inventory_records, key=lambda value: value.source_ref
                )
            ],
            "coverage": coverage,
            "candidates": sorted(
                public_candidates,
                key=lambda value: (-len(value["occurrenceIds"]), value["candidateId"]),
            ),
            "occurrences": public_occurrences,
            "issues": issues,
            "manualReviewRequired": bool(manual_review_sources),
            "manualReviewSources": manual_review_sources,
            "artifacts": sorted(set(artifacts)),
        }

    def _empty_result(
        self,
        requested_count: int,
        records: list[SourceRecord],
        issues: list[dict],
        model_version: str,
        *,
        analyzed_count: int = 0,
    ) -> dict:
        issues, manual_review_sources = _manual_review_contract(
            records,
            issues,
            required_source_refs={
                record.source_ref
                for record in records
                if record.media_type in {"image", "video"}
            },
        )
        coverage = self._coverage(
            requested_count, records, 0, analyzed_count=analyzed_count
        )
        if manual_review_sources:
            coverage["manualReviewSources"] = len(manual_review_sources)
        return {
            "schemaVersion": self.SCHEMA_VERSION,
            "modelVersion": model_version,
            "sourceHashes": [
                {"sourceRef": record.source_ref, "sourceHash": record.source_hash}
                for record in sorted(records, key=lambda value: value.source_ref)
            ],
            "coverage": coverage,
            "candidates": [],
            "occurrences": [],
            "issues": issues,
            "manualReviewRequired": bool(manual_review_sources),
            "manualReviewSources": manual_review_sources,
            "artifacts": [],
        }

    @staticmethod
    def _coverage(
        requested_count: int,
        records: Sequence[SourceRecord],
        occurrence_count: int = 0,
        *,
        analyzed_count: int | None = None,
    ) -> dict:
        image_count = sum(record.media_type == "image" for record in records)
        video_count = sum(record.media_type == "video" for record in records)
        audio_count = sum(record.media_type == "audio" for record in records)
        analyzed_count = len(records) if analyzed_count is None else analyzed_count
        return {
            "totalSources": requested_count,
            "analyzedSources": analyzed_count,
            "skippedSources": max(0, requested_count - analyzed_count),
            "imageCount": image_count,
            "videoCount": video_count,
            "audioCount": audio_count,
            "detectedOccurrences": occurrence_count,
        }

    def _public_result(self, job_id: str, cached: dict) -> dict:
        candidates = []
        for value in cached.get("candidates", []):
            candidate = IdentityCandidate(
                candidate_id=str(value["candidateId"]),
                occurrence_ids=tuple(value.get("occurrenceIds") or []),
                source_refs=tuple(value.get("sourceRefs") or []),
                confidence=float(value.get("confidence") or 0.0),
                grouping_band=str(value.get("groupingBand") or "mixed"),
                grouping_label=str(
                    value.get("groupingLabel") or "Mixed appearance - review"
                ),
                evidence_artifact_ids=tuple(value.get("evidenceArtifactIds") or []),
                suggested_name=str(value.get("suggestedName") or ""),
                suggested_role=str(value.get("suggestedRole") or ""),
                suggestion_source=str(value.get("suggestionSource") or ""),
                needs_review=bool(value.get("needsReview", True)),
            )
            candidates.append(candidate.public_dict(job_id))
        occurrences = []
        for value in cached.get("occurrences", []):
            occurrence = FaceOccurrence(
                occurrence_id=str(value["occurrenceId"]),
                candidate_id=str(value["candidateId"]),
                source_ref=str(value["sourceRef"]),
                source_label=str(value.get("sourceLabel") or ""),
                media_type=str(value.get("mediaType") or "image"),
                frame_index=int(value.get("frameIndex") or 0),
                timestamp_ms=int(value.get("timestampMs") or 0),
                bbox=tuple(int(item) for item in value.get("bbox", [0, 0, 1, 1])),
                confidence=float(value.get("confidence") or 0.0),
                crop_artifact_id=str(value["cropArtifactId"]),
                ambiguous=bool(value.get("ambiguous")),
            )
            occurrences.append(occurrence.public_dict(job_id))
        manual_review_sources = []
        for value in cached.get("manualReviewSources", [])[:500]:
            if not isinstance(value, dict):
                continue
            source_ref = str(value.get("sourceRef") or "")
            if not SHA256.fullmatch(source_ref):
                continue
            source_hash = str(value.get("sourceHash") or "")
            manual_review_sources.append(
                {
                    "sourceRef": source_ref,
                    "sourceHash": source_hash if SHA256.fullmatch(source_hash) else None,
                    "issueCodes": sorted(
                        {
                            str(code)[:160]
                            for code in value.get("issueCodes", [])[:100]
                            if str(code)
                        }
                    ),
                }
            )
        return {
            "coverage": dict(cached.get("coverage") or {}),
            "sourceHashes": [
                {
                    "sourceRef": str(value.get("sourceRef") or ""),
                    "sourceHash": str(value.get("sourceHash") or ""),
                }
                for value in cached.get("sourceHashes", [])[:500]
                if SHA256.fullmatch(str(value.get("sourceRef") or ""))
                and SHA256.fullmatch(str(value.get("sourceHash") or ""))
            ],
            "candidates": candidates,
            "occurrences": occurrences,
            "issues": [dict(issue) for issue in cached.get("issues", [])],
            "manualReviewRequired": bool(
                cached.get("manualReviewRequired") or manual_review_sources
            ),
            "manualReviewSources": manual_review_sources,
            "evidence": {
                "manifestUrl": f"/pluribus/identity/jobs/{job_id}/evidence",
                "localOnly": True,
                "embeddingsExposed": False,
            },
        }

    def _cache_key(
        self,
        records: Sequence[SourceRecord],
        issues: Sequence[dict],
        requested_count: int,
        analyzer_status,
        model_bundle_ready: bool,
    ) -> str:
        material = {
            "schemaVersion": self.SCHEMA_VERSION,
            "analyzerId": self.analyzer.analyzer_id,
            "modelVersion": self.analyzer.model_version,
            "analyzerAvailable": analyzer_status.available,
            "modelBundleVerified": model_bundle_ready,
            "analyzerIssues": sorted(
                str(issue.get("code") or issue.get("issueId") or "")
                for issue in analyzer_status.issues
            ),
            "similarityThreshold": self.similarity_threshold,
            "resourceLimits": {
                "maxSourceBytes": self.resolver.max_source_bytes,
                "maxTotalSourceBytes": self.resolver.max_total_bytes,
                "maxImagePixels": self.resolver.max_image_pixels,
                "detectorTopK": getattr(self.analyzer, "detector_top_k", None),
                "maxVideoFrames": getattr(self.analyzer, "max_video_frames", None),
                "maxFacesPerFrame": getattr(
                    self.analyzer, "max_faces_per_frame", None
                ),
                "maxOccurrences": getattr(
                    self.analyzer, "max_total_occurrences", None
                ),
                "maxFramePixels": getattr(self.analyzer, "max_frame_pixels", None),
                "maxInMemoryCropBytes": getattr(
                    self.analyzer, "max_total_crop_bytes", None
                ),
                "maxCropSide": getattr(self.analyzer, "max_crop_side", None),
                "maxEvidenceArtifactBytes": self.max_evidence_artifact_bytes,
                "maxPendingJobs": self.max_pending_jobs,
                "maxClusterCandidateComparisons": (
                    MAX_CLUSTER_CANDIDATE_COMPARISONS
                ),
            },
            "requestedSources": requested_count,
            "sources": [
                {
                    "sourceRef": record.source_ref,
                    "sourceHash": record.source_hash,
                    "mediaType": record.media_type,
                    "displayLabel": record.display_label,
                }
                for record in sorted(records, key=lambda value: value.source_ref)
            ],
            "skipped": sorted(
                str(issue.get("sourceRef") or issue.get("issueId") or "")
                for issue in issues
            ),
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _cache_path(self, cache_key: str) -> str:
        return os.path.join(self.cache_dir, cache_key)

    def _cache_json_path(self, cache_key: str) -> str:
        return os.path.join(self._cache_path(cache_key), "result.json")

    def _read_cache(self, cache_key: str) -> dict | None:
        path = self._cache_json_path(cache_key)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, cache_key: str, value: dict) -> None:
        ensure_private_dir(self._cache_path(cache_key))
        write_private_json(self._cache_json_path(cache_key), value)

    def _job_path(self, job_id: str) -> str:
        return os.path.join(self.jobs_dir, f"{job_id}.json")

    def _links_path_for_job(self, job: dict) -> str:
        workflow_ref = str(job.get("workflowRef") or "")
        if workflow_ref:
            return self._links_path_for_workflow_ref(workflow_ref)
        return os.path.join(self.jobs_dir, f"{job['jobId']}.links.json")

    def _links_path_for_workflow_ref(self, workflow_ref: str) -> str:
        workflow_key = hashlib.sha256(
            f"identity-links:{workflow_ref}".encode("utf-8")
        ).hexdigest()
        return os.path.join(self.links_dir, f"{workflow_key}.json")

    def _write_job(self, job: dict) -> None:
        write_private_json(self._job_path(job["jobId"]), job)

    def _get_job_record(self, job_id: str) -> dict:
        try:
            normalized = str(uuid.UUID(str(job_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid identity job identifier.") from exc
        with self._lock:
            if normalized in self._jobs:
                return self._jobs[normalized]
            path = self._job_path(normalized)
            if not os.path.isfile(path):
                raise ValueError("Identity analysis job was not found.")
            with open(path, "r", encoding="utf-8") as handle:
                job = json.load(handle)
            if job.get("state") in {"queued", "running", "cancel_requested"}:
                job.update(
                    {
                        "state": "failed",
                        "issues": [
                            _issue(
                                "identity_analysis_interrupted",
                                "warning",
                                "Identity analysis was interrupted",
                                "ComfyUI stopped before this local job completed. Run it again.",
                                code="interrupted",
                            )
                        ],
                    }
                )
                self._write_job(job)
            self._jobs[normalized] = job
            return job

    def _delete_job_files(self, job: dict) -> None:
        job_id = job["jobId"]
        cache_key = job.get("cacheKey")
        with self._lock:
            self._jobs.pop(job_id, None)
            paths = [self._job_path(job_id)]
            # Workflow-scoped confirmations intentionally survive transient job
            # deletion and cache hits. DELETE .../links removes them explicitly.
            if not job.get("workflowRef"):
                paths.append(self._links_path_for_job(job))
            for path in paths:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            if cache_key and not self._other_jobs_reference_cache(job_id, cache_key):
                cache_path = self._cache_path(cache_key)
                if _is_within(cache_path, self.cache_dir):
                    shutil.rmtree(cache_path, ignore_errors=True)

    def _other_jobs_reference_cache(self, excluded_job_id: str, cache_key: str) -> bool:
        for filename in os.listdir(self.jobs_dir):
            if not filename.endswith(".json") or filename.endswith(".links.json"):
                continue
            if filename == f"{excluded_job_id}.json":
                continue
            try:
                with open(
                    os.path.join(self.jobs_dir, filename), "r", encoding="utf-8"
                ) as handle:
                    if json.load(handle).get("cacheKey") == cache_key:
                        return True
            except (OSError, json.JSONDecodeError):
                continue
        return False

    @staticmethod
    def _job_order(job: dict) -> tuple[int, str]:
        created_order = job.get("createdOrder")
        if isinstance(created_order, int):
            return created_order, str(job.get("jobId") or "")
        return int(job.get("createdAt") or 0) * 1_000_000, str(
            job.get("jobId") or ""
        )

    def _require_current_workflow_job(self, job: dict) -> None:
        """Reject writes from an obsolete analysis view of one workflow.

        Links are deliberately workflow-scoped so cache-hit rescans preserve
        producer work.  That also means an older completed job must not be able
        to replace the shared link document after a newer analysis has started.
        """

        workflow_ref = str(job.get("workflowRef") or "")
        if not workflow_ref:
            return
        current_order = self._job_order(job)
        newer_job_id = ""
        seen_job_ids: set[str] = set()
        candidates = list(self._jobs.values())
        for filename in os.listdir(self.jobs_dir):
            if not filename.endswith(".json") or filename.endswith(".links.json"):
                continue
            path = os.path.join(self.jobs_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    stored = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(stored, dict):
                candidates.append(stored)
        for candidate in candidates:
            candidate_id = str(candidate.get("jobId") or "")
            if not candidate_id or candidate_id in seen_job_ids:
                continue
            seen_job_ids.add(candidate_id)
            if candidate_id == job.get("jobId"):
                continue
            if str(candidate.get("workflowRef") or "") != workflow_ref:
                continue
            if self._job_order(candidate) > current_order:
                newer_job_id = candidate_id
                break
        if newer_job_id:
            raise ValueError(
                "This identity analysis is stale because a newer workflow analysis "
                "exists. Reload the current People view before saving links."
            )


def _apply_source_label_suggestions(
    candidates: list[dict],
    occurrences: list[dict],
    labels: dict[str, str],
) -> None:
    """Suggest working labels only when explicit asset naming supports it.

    A character sheet can contain several people, so its name goes only to the
    uniquely dominant repeated candidate. Generic scene/storyboard filenames
    never become person names.
    """

    counts: dict[str, dict[str, int]] = {}
    for occurrence in occurrences:
        source_ref = str(occurrence.get("sourceRef") or "")
        candidate_id = str(occurrence.get("candidateId") or "")
        source_counts = counts.setdefault(source_ref, {})
        source_counts[candidate_id] = source_counts.get(candidate_id, 0) + 1

    suggestions: dict[str, list[tuple[str, str]]] = {}
    for source_ref, label in labels.items():
        explicit = _explicit_identity_label(label)
        if explicit is None:
            continue
        name, role, descriptor = explicit
        ranked = sorted(
            counts.get(source_ref, {}).items(), key=lambda item: (-item[1], item[0])
        )
        if not ranked:
            continue
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue
        if (
            descriptor in {"character_sheet", "identity_evidence"}
            and len(ranked) > 1
            and ranked[0][1] < 2
        ):
            continue
        suggestions.setdefault(ranked[0][0], []).append((name, role))

    for candidate in candidates:
        values = suggestions.get(str(candidate.get("candidateId") or ""), [])
        unique_names = {name.casefold(): name for name, _role in values}
        if len(unique_names) != 1:
            continue
        candidate["suggestedName"] = next(iter(unique_names.values()))
        roles = {role for _name, role in values if role}
        candidate["suggestedRole"] = next(iter(roles)) if len(roles) == 1 else ""
        candidate["suggestionSource"] = "source_label"


def _explicit_identity_label(label: str) -> tuple[str, str, str] | None:
    stem = os.path.splitext(os.path.basename(str(label or "").strip()))[0]
    normalized = re.sub(r"\s+", "_", stem.strip())
    # Asset builders commonly prefix project identifiers and separate the
    # human-readable subject with a double underscore.
    identity_segment = normalized.rsplit("__", 1)[-1]
    descriptor_pattern = (
        r"(character[_-]?sheet|identity[_-]?evidence|headshot|cast[_-]?portrait)"
    )
    match = re.fullmatch(
        rf"([A-Za-z][A-Za-z0-9'_-]{{0,79}}?)[_-]+{descriptor_pattern}",
        identity_segment,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    raw_identity = match.group(1)
    structured_role = ""
    if re.fullmatch(r"identity[_-]?evidence", match.group(2), re.IGNORECASE):
        performer_and_role = re.fullmatch(
            r"(.+?)[_-]+as[_-]+(.+)", raw_identity, re.IGNORECASE
        )
        if performer_and_role:
            raw_identity = performer_and_role.group(1)
            structured_role = re.sub(
                r"[_-]+", " ", performer_and_role.group(2)
            ).strip()
    raw_name = re.sub(r"[_-]+", " ", raw_identity).strip()
    lowered_tokens = {token.casefold() for token in raw_name.split()}
    generic = {
        "scene",
        "storyboard",
        "contact",
        "location",
        "prop",
        "wardrobe",
        "motion",
        "reference",
        "unknown",
    }
    if not raw_name or lowered_tokens & generic:
        return None
    name = " ".join(token.capitalize() for token in raw_name.split())
    descriptor = match.group(2).lower().replace("-", "_")
    descriptor = descriptor.replace("charactersheet", "character_sheet")
    descriptor = descriptor.replace("identityevidence", "identity_evidence")
    descriptor = descriptor.replace("castportrait", "cast_portrait")
    role = " ".join(token.capitalize() for token in structured_role.split()) or (
        "Character"
        if descriptor in {"character_sheet", "identity_evidence"}
        else "Performer"
    )
    return name, role, descriptor


def _build_evidence_sheet(
    destination: str,
    artifact_dir: str,
    candidate_id: str,
    crop_ids: Sequence[str],
) -> None:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    columns = 4
    cell_width, cell_height = 220, 250
    header_height = 54
    rows = max(1, (len(crop_ids) + columns - 1) // columns)
    canvas = Image.new(
        "RGB", (columns * cell_width, header_height + rows * cell_height), "#11100e"
    )
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.load_default()
    draw.text((16, 12), f"IDENTITY CANDIDATE {candidate_id}", fill="#f2eee8", font=font)
    draw.text(
        (16, 31),
        "AI SUGGESTION - PRODUCER CONFIRMATION REQUIRED",
        fill="#e28a2b",
        font=font,
    )
    for index, crop_id in enumerate(crop_ids):
        row, column = divmod(index, columns)
        x = column * cell_width + 10
        y = header_height + row * cell_height + 10
        with Image.open(os.path.join(artifact_dir, crop_id)) as crop:
            normalized = ImageOps.fit(crop.convert("RGB"), (200, 200))
            canvas.paste(normalized, (x, y))
        draw.text((x, y + 208), crop_id[5:17], fill="#c4bbb0", font=font)
    canvas.save(destination, format="PNG", compress_level=9, optimize=False)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass


def _issue(
    issue_id: str,
    severity: str,
    title: str,
    description: str,
    *,
    source_ref: str = "",
    candidate_id: str = "",
    code: str = "",
) -> dict:
    result = {
        "issueId": issue_id,
        "severity": severity,
        "title": title,
        "description": description,
        "message": description,
    }
    if code:
        result["code"] = code
    if source_ref:
        result["sourceRef"] = source_ref
    if candidate_id:
        result["candidateId"] = candidate_id
    return result


def _manual_review_contract(
    records: Sequence[SourceRecord],
    issues: Sequence[dict],
    *,
    required_source_refs: set[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Attach durable source context to every incomplete-analysis finding."""

    source_hashes = {record.source_ref: record.source_hash for record in records}
    required = set(required_source_refs or ())
    normalized: list[dict] = []
    issue_codes_by_source: dict[str, set[str]] = {}
    for value in issues:
        issue = dict(value)
        source_ref = str(issue.get("sourceRef") or "")
        if source_ref:
            source_hash = source_hashes.get(source_ref)
            issue["sourceHash"] = source_hash if SHA256.fullmatch(source_hash or "") else None
            issue["manualReviewRequired"] = True
            issue_codes_by_source.setdefault(source_ref, set()).add(
                str(issue.get("code") or issue.get("issueId") or "analysis_incomplete")
            )
        normalized.append(issue)

    for source_ref in sorted(required - set(issue_codes_by_source)):
        source_hash = source_hashes.get(source_ref)
        normalized.append(
            {
                **_issue(
                    f"analysis_incomplete_{source_ref[:16]}",
                    "warning",
                    "Visual source needs manual person review",
                    (
                        "Local identity analysis did not completely inspect this source. "
                        "Review the original media manually before treating identity "
                        "coverage as complete."
                    ),
                    source_ref=source_ref,
                    code="analysis_incomplete",
                ),
                "sourceHash": (
                    source_hash if SHA256.fullmatch(source_hash or "") else None
                ),
                "manualReviewRequired": True,
            }
        )
        issue_codes_by_source[source_ref] = {"analysis_incomplete"}

    manual_sources = [
        {
            "sourceRef": source_ref,
            "sourceHash": (
                source_hashes.get(source_ref)
                if SHA256.fullmatch(source_hashes.get(source_ref, ""))
                else None
            ),
            "issueCodes": sorted(codes),
        }
        for source_ref, codes in sorted(issue_codes_by_source.items())
    ]
    return normalized, manual_sources


def _media_type(path: str) -> str:
    extension = os.path.splitext(path)[1].lower()
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in VIDEO_EXTENSIONS:
        return "video"
    if extension in AUDIO_EXTENSIONS:
        return "audio"
    return "unknown"


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath(
            [os.path.realpath(path), os.path.realpath(root)]
        ) == os.path.realpath(root)
    except ValueError:
        return False


def _sha256_file(
    path: str,
    cancel_event: threading.Event | None = None,
) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            if cancel_event and cancel_event.is_set():
                raise AnalysisCancelled("Identity analysis was canceled.")
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _image_pixel_count(path: str) -> int | None:
    """Read dimensions without decoding pixels; skip the check without Pillow."""

    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Image.DecompressionBombError:
        return 2**63 - 1
    except OSError:
        return None
    return max(0, int(width)) * max(0, int(height))


def _write_private_bytes(path: str, value: bytes) -> None:
    directory = os.path.dirname(path)
    ensure_private_dir(directory)
    temporary = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temporary, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
