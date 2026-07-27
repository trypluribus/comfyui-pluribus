"""Private local selection and durable sync for project-person portraits.

Only producer-confirmed occurrences are eligible. Local occurrence ids, source
refs, timestamps, crop names, and quality measurements never cross the network
boundary: the hosted request contains one re-encoded image plus opaque UUID and
hash material.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from typing import Any

from . import remote
from .bindings import BindingStore
from .identity_service import IdentityAnalysisService, IdentityPersistenceError
from .storage import ensure_private_dir, write_private_json


PORTRAIT_OUTBOX_SCHEMA_VERSION = 1
MAX_PORTRAITS_PER_PERSON = 5
MAX_LOCAL_PORTRAIT_BYTES = 1024 * 1024


class PortraitProjectionDeferred(ValueError):
    """A recoverable local evidence problem that must freeze hosted sync."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_hash(value: object) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def portrait_rank_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    """Deterministic best-first key for a confirmed local appearance."""

    return (
        1 if candidate.get("ambiguous") else 0,
        -int(candidate.get("bboxArea") or 0),
        -round(float(candidate.get("confidence") or 0.0), 6),
        -round(float(candidate.get("sharpness") or 0.0), 4),
        round(abs(float(candidate.get("brightness") or 128.0) - 128.0), 4),
        str(candidate.get("occurrenceId") or ""),
    )


def rank_confirmed_portraits(
    candidates: list[dict[str, Any]],
    limit: int = MAX_PORTRAITS_PER_PERSON,
) -> list[dict[str, Any]]:
    """Return at most five unique crop candidates in stable quality order."""

    ordered = sorted(candidates, key=portrait_rank_key)
    result: list[dict[str, Any]] = []
    seen_artifacts: set[str] = set()
    for candidate in ordered:
        artifact_id = str(candidate.get("cropArtifactId") or "")
        if not artifact_id or artifact_id in seen_artifacts:
            continue
        seen_artifacts.add(artifact_id)
        result.append(candidate)
        if len(result) >= max(0, min(MAX_PORTRAITS_PER_PERSON, limit)):
            break
    return result


def _read_json(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {
            "schemaVersion": PORTRAIT_OUTBOX_SCHEMA_VERSION,
            "operations": {},
            "projectErrors": {},
        }
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityPersistenceError(
            "Private portrait sync state could not be verified. Restart ComfyUI "
            "before saving more identity review work."
        ) from exc
    if (
        not isinstance(value, dict)
        or value.get("schemaVersion") != PORTRAIT_OUTBOX_SCHEMA_VERSION
        or not isinstance(value.get("operations"), dict)
    ):
        raise IdentityPersistenceError("The portrait sync outbox version is not supported.")
    project_errors = value.get("projectErrors")
    if project_errors is None:
        value["projectErrors"] = {}
    elif not isinstance(project_errors, dict):
        raise IdentityPersistenceError("The portrait sync error ledger is not supported.")
    return value


def _write_private_bytes(path: str, payload: bytes) -> None:
    directory = os.path.dirname(path) or "."
    ensure_private_dir(directory)
    descriptor, temporary = tempfile.mkstemp(
        dir=directory,
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
    )
    try:
        try:
            os.fchmod(descriptor, 0o600)
        except (AttributeError, OSError):
            pass
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.remove(temporary)
        except FileNotFoundError:
            pass
        raise


def _image_metrics(path: str) -> tuple[float, float]:
    from PIL import Image, ImageFilter, ImageStat

    with Image.open(path) as image:
        image.load()
        grayscale = image.convert("L")
        brightness = float(ImageStat.Stat(grayscale).mean[0])
        edges = grayscale.filter(ImageFilter.FIND_EDGES)
        sharpness = float(ImageStat.Stat(edges).var[0])
        return brightness, sharpness


def _sanitize_local_portrait(path: str) -> bytes:
    from PIL import Image, ImageOps

    with Image.open(path) as image:
        image.load()
        oriented = ImageOps.exif_transpose(image).convert("RGB")
        side = min(512, max(128, min(oriented.size)))
        fitted = ImageOps.fit(
            oriented,
            (side, side),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.45),
        )
        output = io.BytesIO()
        fitted.save(
            output,
            format="JPEG",
            quality=88,
            optimize=True,
            progressive=True,
        )
    payload = output.getvalue()
    if not 0 < len(payload) <= MAX_LOCAL_PORTRAIT_BYTES:
        raise ValueError("A sanitized project portrait exceeded the 1 MB limit.")
    return payload


class ProjectPortraitService:
    """Rebuildable local portrait projection with a crash-safe upload outbox."""

    def __init__(
        self,
        identity: IdentityAnalysisService,
        bindings: BindingStore,
        *,
        connection_path: str,
    ):
        self.identity = identity
        self.bindings = bindings
        self.connection_path = connection_path
        self.data_dir = os.path.join(identity.data_dir, "portraits")
        self.staging_dir = os.path.join(self.data_dir, "staged")
        self.outbox_path = os.path.join(self.data_dir, "outbox.json")
        ensure_private_dir(self.data_dir)
        ensure_private_dir(self.staging_dir)
        self._lock = threading.RLock()
        self._drain_task: asyncio.Task[list[dict[str, Any]]] | None = None
        self._drain_loop: asyncio.AbstractEventLoop | None = None
        self._unreadable_job_files: set[str] = set()

    def reconcile_completed_jobs(self) -> list[dict[str, Any]]:
        """Recover a crash between identity commit and portrait enqueue."""

        # Portrait choice is project-person scoped, not workflow scoped. A
        # project is projected only once every bound workflow with identity
        # history has an authoritative completed newest job. This conservative
        # freeze prevents one stale workflow from deleting another's current
        # best image during startup or retry.
        snapshots: list[tuple[str, str, list[dict[str, Any]]]] = []
        with self.identity._lock, self.bindings._lock:
            binding_data = self.bindings._read()
            latest_by_workflow = self._latest_jobs_by_workflow_locked()
            transition_project_ids = {
                str(project_id)
                for binding in binding_data.get("workflows", {}).values()
                if isinstance(binding, dict)
                for project_id in binding.get("portrait_retirement_project_ids", [])
                if project_id
            }
            project_ids = sorted(
                {
                    str(binding.get("project_id") or "")
                    for binding in binding_data.get("workflows", {}).values()
                    if isinstance(binding, dict)
                    and binding.get("project_id")
                }
                | transition_project_ids
            )
            for project_id in project_ids:
                try:
                    contexts = self._project_contexts_locked(
                        project_id,
                        binding_data,
                        latest_by_workflow,
                    )
                except PortraitProjectionDeferred as exc:
                    self._record_project_error(project_id, exc.code, str(exc))
                    continue
                except (OSError, ValueError):
                    continue
                if contexts is None:
                    self._record_project_error(
                        project_id,
                        "identity_analysis_in_progress",
                        "Portrait sync is paused until every current project identity scan completes.",
                    )
                    continue
                newest = (
                    max(
                        (context["job"] for context in contexts),
                        key=self.identity._job_order,
                    )
                    if contexts
                    else {"jobId": f"detached:{project_id}"}
                )
                snapshots.append(
                    (project_id, str(newest.get("jobId") or ""), contexts)
                )

        results = []
        for project_id, job_id, contexts in snapshots:
            try:
                results.append(
                    self._reconcile_project(project_id, job_id, contexts)
                )
            except (OSError, ValueError):
                continue
        return results

    def reconcile_job(self, job_id: str) -> dict[str, Any]:
        """Project every current workflow into one bounded project-person set."""

        with self.identity._lock, self.bindings._lock:
            job = self.identity._get_job_record(job_id)
            if job.get("state") != "completed":
                raise ValueError("Portraits require a completed identity analysis job.")
            self.identity._require_current_workflow_job(job)
            workflow_ref = str(job.get("workflowRef") or "")
            if not workflow_ref:
                raise ValueError("Portraits require a workflow-scoped identity job.")
            binding = deepcopy(
                self.bindings._find(self.bindings._read(), workflow_ref)
            )
            project_id = str(binding.get("project_id") or "")
            if not project_id:
                return {"jobId": job_id, "queued": 0, "state": "saved_local"}
            binding_data = self.bindings._read()
            try:
                contexts = self._project_contexts_locked(
                    project_id,
                    binding_data,
                    self._latest_jobs_by_workflow_locked(),
                )
            except PortraitProjectionDeferred as exc:
                return self._block_project(project_id, job_id, exc)
        if contexts is None:
            return self._block_project(
                project_id,
                job_id,
                PortraitProjectionDeferred(
                    "identity_analysis_in_progress",
                    "Portrait sync is paused until every current project identity scan completes.",
                ),
            )
        return self._reconcile_project(project_id, job_id, contexts)

    def _latest_jobs_by_workflow_locked(self) -> dict[str, dict[str, Any]]:
        jobs_by_id: dict[str, dict[str, Any]] = {}
        self._unreadable_job_files = set()
        for value in self.identity._jobs.values():
            if isinstance(value, dict) and value.get("jobId"):
                jobs_by_id[str(value["jobId"])] = deepcopy(value)
        for filename in sorted(os.listdir(self.identity.jobs_dir)):
            if not filename.endswith(".json") or filename.endswith(".links.json"):
                continue
            job_id = filename[:-5]
            try:
                jobs_by_id[job_id] = deepcopy(self.identity._get_job_record(job_id))
            except (OSError, ValueError):
                self._unreadable_job_files.add(job_id)
                continue

        latest_by_workflow: dict[str, dict[str, Any]] = {}
        for job in jobs_by_id.values():
            workflow_ref = str(job.get("workflowRef") or "")
            if not workflow_ref:
                continue
            current = latest_by_workflow.get(workflow_ref)
            if current is None or self.identity._job_order(job) > self.identity._job_order(
                current
            ):
                latest_by_workflow[workflow_ref] = job
        return latest_by_workflow

    def _workflow_has_portrait_history(
        self,
        project_id: str,
        workflow_ref: str,
    ) -> bool:
        del workflow_ref
        with self._lock:
            outbox = _read_json(self.outbox_path)
            return any(
                isinstance(operation, dict)
                and str(operation.get("projectId") or "") == project_id
                for operation in outbox["operations"].values()
            )

    def _project_contexts_locked(
        self,
        project_id: str,
        binding_data: dict[str, Any],
        latest_by_workflow: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        contexts: list[dict[str, Any]] = []
        bindings = binding_data.get("workflows")
        project_bindings = [
            binding
            for binding in (bindings.values() if isinstance(bindings, dict) else [])
            if isinstance(binding, dict)
            and str(binding.get("project_id") or "") == project_id
        ]
        if self._unreadable_job_files and any(
            self._workflow_has_portrait_history(
                project_id,
                str(binding.get("workflow_ref") or ""),
            )
            for binding in project_bindings
        ):
            raise PortraitProjectionDeferred(
                "identity_evidence_unavailable",
                "Portrait sync is paused because a local identity job record is unreadable. Restore or re-run the scan, then retry sync.",
            )
        for binding in project_bindings:
            workflow_ref = str(binding.get("workflow_ref") or "")
            job = latest_by_workflow.get(workflow_ref)
            if job is None:
                if self._workflow_has_portrait_history(project_id, workflow_ref):
                    raise PortraitProjectionDeferred(
                        "identity_evidence_unavailable",
                        "Portrait sync is paused because a bound workflow's authoritative identity job is unavailable. Restore or re-run that scan, then retry sync.",
                    )
                continue
            if job.get("state") != "completed":
                return None
            self.identity._require_current_workflow_job(job)
            job_id = str(job.get("jobId") or "")
            cached = self.identity._read_cache(str(job.get("cacheKey") or ""))
            if not isinstance(cached, dict):
                raise PortraitProjectionDeferred(
                    "identity_evidence_unavailable",
                    "Portrait sync is paused because confirmed local appearance evidence is unavailable. Re-run identity analysis, then retry sync.",
                )
            if not all(
                isinstance(cached.get(field), list)
                for field in ("candidates", "occurrences", "artifacts")
            ):
                raise PortraitProjectionDeferred(
                    "identity_evidence_unavailable",
                    "Portrait sync is paused because confirmed local appearance evidence is invalid. Re-run identity analysis, then retry sync.",
                )
            try:
                links_path = self.identity._links_path_for_job(job)
                if os.path.isfile(links_path):
                    with open(links_path, encoding="utf-8") as handle:
                        raw_document = json.load(handle)
                    if not isinstance(raw_document, dict):
                        raise ValueError("Identity review links are malformed.")
                    raw_links = raw_document.get("links")
                    if not isinstance(raw_links, list):
                        raise ValueError("Identity review links are malformed.")
                else:
                    if self._workflow_has_portrait_history(project_id, workflow_ref):
                        raise ValueError("Identity review links are unavailable.")
                    raw_links = []
                self._validate_projection_evidence(cached, raw_links)
                filtered_links = self.identity._get_links_locked(job_id).get("links") or []
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise PortraitProjectionDeferred(
                    "identity_evidence_unavailable",
                    "Portrait sync is paused because confirmed local identity ownership is inconsistent. Restore or re-run identity review, then retry sync.",
                ) from exc
            contexts.append(
                {
                    "job": deepcopy(job),
                    "jobId": job_id,
                    "workflowRef": workflow_ref,
                    "binding": deepcopy(binding),
                    "cached": deepcopy(cached),
                    "links": deepcopy(filtered_links),
                }
            )
        contexts.sort(key=lambda context: str(context["workflowRef"]))
        return contexts

    @staticmethod
    def _validate_projection_evidence(
        cached: dict[str, Any],
        raw_links: list[Any],
    ) -> None:
        candidates: dict[str, dict[str, Any]] = {}
        for candidate in cached.get("candidates", []):
            if not isinstance(candidate, dict):
                raise ValueError("Invalid identity candidate.")
            candidate_id = str(candidate.get("candidateId") or "")
            if not candidate_id or candidate_id in candidates:
                raise ValueError("Duplicate identity candidate id.")
            occurrence_ids = candidate.get("occurrenceIds")
            if not isinstance(occurrence_ids, list) or len(occurrence_ids) != len(
                {str(value) for value in occurrence_ids}
            ):
                raise ValueError("Invalid candidate occurrence membership.")
            candidates[candidate_id] = candidate

        occurrences: dict[str, dict[str, Any]] = {}
        for occurrence in cached.get("occurrences", []):
            if not isinstance(occurrence, dict):
                raise ValueError("Invalid identity occurrence.")
            occurrence_id = str(occurrence.get("occurrenceId") or "")
            if not occurrence_id or occurrence_id in occurrences:
                raise ValueError("Duplicate identity occurrence id.")
            occurrences[occurrence_id] = occurrence

        artifacts = cached.get("artifacts", [])
        artifact_ids = {str(value) for value in artifacts}
        if len(artifact_ids) != len(artifacts) or "" in artifact_ids:
            raise ValueError("Duplicate identity artifact id.")

        for candidate_id, candidate in candidates.items():
            for raw_occurrence_id in candidate.get("occurrenceIds", []):
                occurrence_id = str(raw_occurrence_id)
                occurrence = occurrences.get(occurrence_id)
                if (
                    not occurrence
                    or str(occurrence.get("candidateId") or "") != candidate_id
                    or str(occurrence.get("cropArtifactId") or "") not in artifact_ids
                ):
                    raise ValueError("Candidate occurrence evidence is inconsistent.")

        seen_links: set[tuple[str, str, str]] = set()
        for raw_link in raw_links:
            if not isinstance(raw_link, dict):
                raise ValueError("Invalid stored identity link.")
            state = str(raw_link.get("state") or "confirmed")
            if state not in {"confirmed", "rejected", "unsure"}:
                raise ValueError("Unsupported stored identity link state.")
            candidate_id = str(raw_link.get("candidateId") or "")
            candidate = candidates.get(candidate_id)
            if not candidate:
                raise ValueError("Stored candidate evidence is missing.")
            selected_present = (
                "occurrenceIds" in raw_link or "occurrence_ids" in raw_link
            )
            selected = raw_link.get(
                "occurrenceIds", raw_link.get("occurrence_ids")
            )
            if selected_present and (
                not isinstance(selected, list) or not selected
            ):
                raise ValueError("Stored occurrence ownership is malformed.")
            if state != "confirmed":
                continue
            person_id = str(raw_link.get("personId") or "")
            if not person_id:
                raise ValueError("Confirmed person ownership is missing.")
            link_key = (candidate_id, person_id, state)
            if link_key in seen_links:
                raise ValueError("Duplicate stored identity link.")
            seen_links.add(link_key)
            membership = {str(value) for value in candidate.get("occurrenceIds", [])}
            selected_ids = (
                [str(value) for value in selected]
                if selected_present
                else list(membership)
            )
            if len(selected_ids) != len(set(selected_ids)) or any(
                occurrence_id not in membership for occurrence_id in selected_ids
            ):
                raise ValueError("Confirmed occurrence ownership is inconsistent.")

    def _reconcile_project(
        self,
        project_id: str,
        job_id: str,
        contexts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            desired = self._desired_project_portraits(
                project_id=project_id,
                contexts=contexts,
            )
        except PortraitProjectionDeferred as exc:
            return self._block_project(project_id, job_id, exc)
        except (ValueError, TypeError, OverflowError):
            return self._block_project(
                project_id,
                job_id,
                PortraitProjectionDeferred(
                    "identity_evidence_unavailable",
                    "Portrait sync is paused because confirmed local appearance measurements are invalid. Re-run identity analysis, then retry sync.",
                ),
            )
        # Image measurement/re-encoding is intentionally outside the identity
        # locks. Revalidate the exact jobs, links, and person aliases before the
        # outbox commit, then hold those locks through the local write so a new
        # scan or review decision cannot race a stale project projection.
        with self.identity._lock, self.bindings._lock:
            try:
                current_contexts = self._project_contexts_locked(
                    project_id,
                    self.bindings._read(),
                    self._latest_jobs_by_workflow_locked(),
                )
            except PortraitProjectionDeferred as exc:
                return self._block_project(project_id, job_id, exc)
            if (
                current_contexts is None
                or self._contexts_projection_hash(current_contexts)
                != self._contexts_projection_hash(contexts)
            ):
                return self._block_project(
                    project_id,
                    job_id,
                    PortraitProjectionDeferred(
                        "identity_projection_changed",
                        "Portrait sync paused because project identity review changed during projection. Retry sync to use the current review.",
                    ),
                )
            return self._reconcile_project_outbox(
                project_id=project_id,
                job_id=job_id,
                desired=desired,
            )

    @staticmethod
    def _contexts_projection_hash(contexts: list[dict[str, Any]]) -> str:
        return _stable_hash([
            {
                "jobId": context.get("jobId"),
                "workflowRef": context.get("workflowRef"),
                "links": context.get("links"),
                "personDrafts": (
                    context.get("binding", {}).get("person_drafts")
                    if isinstance(context.get("binding"), dict)
                    else None
                ),
                "personDraftTombstones": (
                    context.get("binding", {}).get("person_draft_tombstones")
                    if isinstance(context.get("binding"), dict)
                    else None
                ),
            }
            for context in contexts
        ])

    def _reconcile_project_outbox(
        self,
        *,
        project_id: str,
        job_id: str,
        desired: list[dict[str, Any]],
    ) -> dict[str, Any]:
        with self._lock:
            outbox = _read_json(self.outbox_path)
            operations = outbox["operations"]
            desired_by_key = {
                str(value["clientPortraitId"]): value
                for value in desired
            }
            next_operations: dict[str, dict[str, Any]] = {}
            matched_keys: set[str] = set()
            unresolved_generation_conflict = False
            for old_id, raw_operation in operations.items():
                if not isinstance(raw_operation, dict):
                    continue
                operation = deepcopy(raw_operation)
                if str(operation.get("projectId") or "") != project_id:
                    next_operations[str(old_id)] = operation
                    continue
                conflict_generation = operation.pop(
                    "conflictStorageGeneration", None
                )
                if conflict_generation:
                    conflict_content = operation.pop(
                        "conflictClientContentSha256", None
                    )
                    try:
                        safe_conflict_generation = str(
                            uuid.UUID(str(conflict_generation))
                        )
                    except (ValueError, TypeError, AttributeError):
                        safe_conflict_generation = ""
                    if (
                        safe_conflict_generation
                        and isinstance(conflict_content, str)
                        and conflict_content == operation.get("contentSha256")
                    ):
                        operation["storageGeneration"] = safe_conflict_generation
                    else:
                        # Fresh projection may adopt a newer hosted generation
                        # only when the server proves it contains the exact
                        # frozen client bytes for this local portrait key.
                        operation["conflictStorageGeneration"] = conflict_generation
                        operation["conflictClientContentSha256"] = conflict_content
                        unresolved_generation_conflict = True
                match_key = str(operation.get("clientPortraitId") or "")
                replacement = desired_by_key.get(match_key)
                if replacement:
                    matched_keys.add(match_key)
                    state = str(operation.get("state") or "waiting_for_person")
                    if state == "retire_pending":
                        # Retirement may already have reached the workspace even
                        # when its response was lost. Finish that idempotent
                        # lifecycle before reusing this content-derived client
                        # key with a potentially different rank/request hash.
                        prior_replacement = operation.get("afterRetire")
                        if (
                            isinstance(prior_replacement, dict)
                            and prior_replacement.get("stagedFile")
                            != replacement.get("stagedFile")
                        ):
                            self._remove_staged(prior_replacement)
                        operation["afterRetire"] = deepcopy(replacement)
                        operation["operationId"] = str(old_id)
                        next_operations[str(old_id)] = operation
                        continue
                    # Frozen upload material stays frozen after a reservation may
                    # have reached the server. Re-ranking never mutates an
                    # idempotency key's request body.
                    same_upload = self._same_frozen_upload(operation, replacement)
                    if state in {"synced", "pending"} and not same_upload:
                        operation["state"] = "retire_pending"
                        operation["afterRetire"] = deepcopy(replacement)
                        operation["operationId"] = str(old_id)
                        next_operations[str(old_id)] = operation
                        continue
                    if state in {"synced", "pending"}:
                        if state == "synced":
                            self._remove_staged(replacement)
                        operation["workflowRefs"] = deepcopy(
                            replacement.get("workflowRefs") or []
                        )
                        operation["jobIds"] = deepcopy(
                            replacement.get("jobIds") or []
                        )
                        operation.pop("afterRetire", None)
                        operation["operationId"] = str(old_id)
                        next_operations[str(old_id)] = operation
                        continue
                    # A waiting or fully retired row has no live reservation to
                    # freeze. It can adopt the latest global rank directly.
                    old_staged = str(operation.get("stagedFile") or "")
                    if old_staged and old_staged != replacement.get("stagedFile"):
                        self._remove_staged(operation)
                    replacement["operationId"] = str(old_id)
                    next_operations[str(old_id)] = replacement
                    continue

                state = str(operation.get("state") or "waiting_for_person")
                if state in {"synced", "pending", "retire_pending"}:
                    abandoned_replacement = operation.pop("afterRetire", None)
                    if isinstance(abandoned_replacement, dict):
                        self._remove_staged(abandoned_replacement)
                    operation["state"] = "retire_pending"
                    operation["operationId"] = str(old_id)
                    next_operations[str(old_id)] = operation
                else:
                    self._remove_staged(operation)

            for key, operation in desired_by_key.items():
                if key in matched_keys:
                    continue
                operation_id = self._operation_id(operation)
                operation["operationId"] = operation_id
                next_operations[operation_id] = operation

            outbox["operations"] = next_operations
            if unresolved_generation_conflict:
                outbox["projectErrors"][project_id] = {
                    "code": "stale_portrait_generation",
                    "message": "Portrait sync found a newer hosted generation with different or missing material proof. It was not changed.",
                    "updatedAt": int(time.time()),
                }
            else:
                outbox["projectErrors"].pop(project_id, None)
            write_private_json(self.outbox_path, outbox)
            pending = sum(
                1
                for value in next_operations.values()
                if str(value.get("projectId") or "") == project_id
                if value.get("state") not in {"synced", "retired"}
            )
            return {
                "jobId": job_id,
                "queued": len(desired),
                "pending": pending,
                "state": (
                    "projection_blocked"
                    if unresolved_generation_conflict
                    else "sync_pending" if pending else "synced"
                ),
            }

    @staticmethod
    def _same_frozen_upload(
        current: dict[str, Any],
        desired: dict[str, Any],
    ) -> bool:
        return all(
            current.get(key) == desired.get(key)
            for key in (
                "contentSha256",
                "mimeType",
                "sizeBytes",
                "displayOrder",
                "makePrimary",
            )
        )

    def _project_sync_result(
        self,
        project_id: str,
        job_id: str,
        *,
        queued: int,
        frozen: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            outbox = _read_json(self.outbox_path)
            pending = sum(
                1
                for value in outbox["operations"].values()
                if isinstance(value, dict)
                and str(value.get("projectId") or "") == project_id
                and value.get("state") not in {"synced", "retired"}
            )
        return {
            "jobId": job_id,
            "queued": queued,
            "pending": pending,
            "state": "sync_pending" if pending else "saved_local",
            **({"frozen": True} if frozen else {}),
        }

    def _record_project_error(self, project_id: str, code: str, message: str) -> None:
        with self._lock:
            outbox = _read_json(self.outbox_path)
            outbox["projectErrors"][project_id] = {
                "code": code,
                "message": message,
                "updatedAt": int(time.time()),
            }
            write_private_json(self.outbox_path, outbox)

    def _block_project(
        self,
        project_id: str,
        job_id: str,
        error: PortraitProjectionDeferred,
    ) -> dict[str, Any]:
        self._record_project_error(project_id, error.code, str(error))
        result = self._project_sync_result(
            project_id,
            job_id,
            queued=0,
            frozen=True,
        )
        return {
            **result,
            "state": "projection_blocked",
            "code": error.code,
            "message": str(error),
        }

    def _desired_portraits(
        self,
        *,
        job_id: str,
        workflow_ref: str,
        project_id: str,
        binding: dict[str, Any],
        cached: dict[str, Any],
        links: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Compatibility wrapper for a one-workflow project projection."""

        return self._desired_project_portraits(
            project_id=project_id,
            contexts=[{
                "jobId": job_id,
                "workflowRef": workflow_ref,
                "binding": binding,
                "cached": cached,
                "links": links,
            }],
        )

    def _candidate_groups_for_context(
        self,
        context: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        job_id = str(context.get("jobId") or "")
        workflow_ref = str(context.get("workflowRef") or "")
        binding = context.get("binding")
        binding = binding if isinstance(binding, dict) else {}
        cached = context.get("cached")
        cached = cached if isinstance(cached, dict) else {}
        links = context.get("links")
        links = links if isinstance(links, list) else []
        occurrences = {
            str(value.get("occurrenceId") or ""): value
            for value in cached.get("occurrences", [])
            if isinstance(value, dict) and value.get("occurrenceId")
        }
        candidate_occurrences = {
            str(value.get("candidateId") or ""): [
                str(item) for item in value.get("occurrenceIds", [])
            ]
            for value in cached.get("candidates", [])
            if isinstance(value, dict) and value.get("candidateId")
        }
        drafts = binding.get("person_drafts")
        drafts = drafts if isinstance(drafts, dict) else {}
        tombstones = binding.get("person_draft_tombstones")
        tombstones = tombstones if isinstance(tombstones, dict) else {}
        local_ids = set(drafts) | set(tombstones)
        grouped_occurrences: dict[str, dict[str, Any]] = {}
        confirmed_owners: dict[tuple[str, str], str] = {}

        for link in links:
            if not isinstance(link, dict) or link.get("state") != "confirmed":
                continue
            raw_person_id = str(link.get("personId") or "")
            if not raw_person_id:
                continue
            try:
                scoped_person_id = (
                    BindingStore._resolve_project_scoped_person_in_binding(
                        binding, raw_person_id
                    )
                )
                person_key = BindingStore._resolve_person_alias_in_binding(
                    binding, scoped_person_id
                )
            except ValueError as exc:
                code = (
                    "identity_requires_review"
                    if "identity_requires_review" in str(exc)
                    else "identity_evidence_unavailable"
                )
                raise PortraitProjectionDeferred(
                    code,
                    "Portrait sync is paused because a confirmed identity belongs to a prior or unverifiable project scope. Review that person explicitly in the current project before retrying sync.",
                ) from exc
            draft = drafts.get(person_key)
            canonical_person_id = ""
            if isinstance(draft, dict):
                marker = draft.get("workspaceAlias")
                if isinstance(marker, dict):
                    canonical_person_id = str(marker.get("canonicalPersonId") or "")
                canonical_person_id = canonical_person_id or str(
                    draft.get("canonicalPersonId") or ""
                )
            elif scoped_person_id not in local_ids:
                canonical_person_id = scoped_person_id
            group_key = (
                f"hosted:{canonical_person_id}"
                if canonical_person_id
                else f"local:{workflow_ref}:{person_key}"
            )
            selected = link.get("occurrenceIds")
            selected_ids = (
                [str(value) for value in selected]
                if isinstance(selected, list)
                else candidate_occurrences.get(str(link.get("candidateId") or ""), [])
            )
            value = grouped_occurrences.setdefault(
                group_key,
                {
                    "canonicalPersonId": canonical_person_id,
                    "occurrences": {},
                },
            )
            if canonical_person_id:
                value["canonicalPersonId"] = canonical_person_id
            for occurrence_id in selected_ids:
                owner_key = (job_id, occurrence_id)
                prior_owner = confirmed_owners.get(owner_key)
                if prior_owner and prior_owner != group_key:
                    raise PortraitProjectionDeferred(
                        "identity_evidence_unavailable",
                        "Portrait sync is paused because one appearance has multiple confirmed owners. Reload identity review and resolve the conflict before retrying sync.",
                    )
                confirmed_owners[owner_key] = group_key
                value["occurrences"].setdefault(
                    (job_id, occurrence_id),
                    {"occurrenceId": occurrence_id, "personKey": person_key},
                )

        result: dict[str, dict[str, Any]] = {}
        for group_key, person in sorted(grouped_occurrences.items()):
            candidates: list[dict[str, Any]] = []
            for occurrence_ref in sorted(person["occurrences"]):
                selected = person["occurrences"][occurrence_ref]
                occurrence_id = str(selected["occurrenceId"])
                occurrence = occurrences.get(occurrence_id)
                if not occurrence:
                    raise PortraitProjectionDeferred(
                        "identity_evidence_unavailable",
                        "Portrait sync is paused because a confirmed appearance is missing from local identity evidence. Re-run identity analysis, then retry sync.",
                    )
                artifact_id = str(occurrence.get("cropArtifactId") or "")
                try:
                    artifact_path = self.identity.artifact_path(job_id, artifact_id)
                    brightness, sharpness = _image_metrics(artifact_path)
                except (OSError, ValueError) as exc:
                    raise PortraitProjectionDeferred(
                        "identity_evidence_unavailable",
                        "Portrait sync is paused because a confirmed local appearance crop is unavailable. Re-run identity analysis, then retry sync.",
                    ) from exc
                bbox = occurrence.get("bbox")
                bbox_area = 0
                if isinstance(bbox, list) and len(bbox) == 4:
                    bbox_area = max(0, int(bbox[2])) * max(0, int(bbox[3]))
                candidates.append(
                    {
                        **occurrence,
                        # Both keys are made workflow/job scoped for stable
                        # global tie-breaking and artifact deduplication.
                        "occurrenceId": f"{workflow_ref}:{job_id}:{occurrence_id}",
                        "cropArtifactId": f"{job_id}:{artifact_id}",
                        "artifactPath": artifact_path,
                        "bboxArea": bbox_area,
                        "brightness": brightness,
                        "sharpness": sharpness,
                        "jobId": job_id,
                        "workflowRef": workflow_ref,
                        "personKey": str(selected["personKey"]),
                    }
                )
            result[group_key] = {
                "canonicalPersonId": str(person.get("canonicalPersonId") or ""),
                "candidates": candidates,
            }
        return result

    def _desired_project_portraits(
        self,
        *,
        project_id: str,
        contexts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for context in contexts:
            for group_key, group in self._candidate_groups_for_context(context).items():
                target = grouped.setdefault(
                    group_key,
                    {
                        "canonicalPersonId": str(group.get("canonicalPersonId") or ""),
                        "candidates": [],
                    },
                )
                target["candidates"].extend(group.get("candidates") or [])

        result: list[dict[str, Any]] = []
        for group_key, person in sorted(grouped.items()):
            selected: list[dict[str, Any]] = []
            selected_by_content: dict[str, dict[str, Any]] = {}
            for candidate in sorted(person["candidates"], key=portrait_rank_key):
                try:
                    payload = _sanitize_local_portrait(candidate["artifactPath"])
                except (OSError, ValueError) as exc:
                    raise PortraitProjectionDeferred(
                        "identity_evidence_unavailable",
                        "Portrait sync is paused because a confirmed local appearance crop cannot be safely decoded. Re-run identity analysis, then retry sync.",
                    ) from exc
                content_sha256 = hashlib.sha256(payload).hexdigest()
                existing = selected_by_content.get(content_sha256)
                if existing:
                    existing["workflowRefs"].add(str(candidate.get("workflowRef") or ""))
                    existing["jobIds"].add(str(candidate.get("jobId") or ""))
                    continue
                if len(selected) >= MAX_PORTRAITS_PER_PERSON:
                    continue
                item = {
                    "candidate": candidate,
                    "payload": payload,
                    "contentSha256": content_sha256,
                    "workflowRefs": {str(candidate.get("workflowRef") or "")},
                    "jobIds": {str(candidate.get("jobId") or "")},
                }
                selected.append(item)
                selected_by_content[content_sha256] = item

            canonical_person_id = str(person.get("canonicalPersonId") or "")
            for display_order, selected_item in enumerate(selected):
                candidate = selected_item["candidate"]
                payload = selected_item["payload"]
                content_sha256 = str(selected_item["contentSha256"])
                workflow_ref = str(candidate.get("workflowRef") or "")
                person_key = str(candidate.get("personKey") or "")
                identity_scope = canonical_person_id or (
                    f"local:{workflow_ref}:{person_key}"
                )
                client_portrait_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        (
                            "https://trypluribus.com/project-person-portrait/"
                            f"{project_id}/{identity_scope}/{content_sha256}"
                        ),
                    )
                )
                staged_file = (
                    _stable_hash(
                        {
                            "workflowRef": workflow_ref,
                            "personKey": person_key,
                            "clientPortraitId": client_portrait_id,
                        }
                    )[:40]
                    + ".jpg"
                )
                _write_private_bytes(
                    os.path.join(self.staging_dir, staged_file), payload
                )
                result.append(
                    {
                        "workflowRef": workflow_ref,
                        "workflowRefs": sorted(
                            value for value in selected_item["workflowRefs"] if value
                        ),
                        "jobIds": sorted(
                            value for value in selected_item["jobIds"] if value
                        ),
                        "projectId": project_id,
                        "personKey": person_key,
                        "canonicalPersonId": canonical_person_id or None,
                        "clientPortraitId": client_portrait_id,
                        "contentSha256": content_sha256,
                        "mimeType": "image/jpeg",
                        "sizeBytes": len(payload),
                        "displayOrder": display_order,
                        "makePrimary": display_order == 0,
                        "stagedFile": staged_file,
                        "state": "pending" if canonical_person_id else "waiting_for_person",
                        "attemptCount": 0,
                    }
                )
        return result

    @staticmethod
    def _operation_id(operation: dict[str, Any]) -> str:
        return _stable_hash(
            {
                "workflowRef": operation.get("workflowRef"),
                "projectId": operation.get("projectId"),
                "personKey": operation.get("personKey"),
                "clientPortraitId": operation.get("clientPortraitId"),
            }
        )[:40]

    def _remove_staged(self, operation: dict[str, Any]) -> None:
        staged_file = str(operation.get("stagedFile") or "")
        if not staged_file or os.path.basename(staged_file) != staged_file:
            return
        try:
            os.remove(os.path.join(self.staging_dir, staged_file))
        except FileNotFoundError:
            pass

    def _canonical_person_for_operation(self, operation: dict[str, Any]) -> str:
        state = str(operation.get("state") or "")
        frozen_person_id = str(operation.get("canonicalPersonId") or "")
        if state in {"pending", "synced", "retire_pending"} and frozen_person_id:
            # A reservation may already exist under this exact hosted owner.
            # Alias changes create separate desired material; they must never
            # redirect cleanup for the old owner to the new survivor.
            return frozen_person_id
        workflow_ref = str(operation.get("workflowRef") or "")
        with self.bindings._lock:
            try:
                binding = self.bindings._find(self.bindings._read(), workflow_ref)
            except ValueError:
                # A previously hosted operation can still be retired safely
                # after its local workflow binding has disappeared because the
                # canonical project-person id is frozen in the durable outbox.
                return str(operation.get("canonicalPersonId") or "")
            person_key = BindingStore._resolve_person_alias_in_binding(
                binding, operation.get("personKey")
            )
            drafts = binding.get("person_drafts")
            draft = drafts.get(person_key) if isinstance(drafts, dict) else None
            if isinstance(draft, dict):
                marker = draft.get("workspaceAlias")
                if isinstance(marker, dict) and marker.get("canonicalPersonId"):
                    return str(marker["canonicalPersonId"])
                return str(draft.get("canonicalPersonId") or "")
            return str(operation.get("canonicalPersonId") or person_key)

    async def drain_pending_async(self) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        task = self._drain_task
        if task is None or task.done() or self._drain_loop is not loop:
            task = loop.create_task(self._drain_once())
            self._drain_task = task
            self._drain_loop = loop
        return deepcopy(await asyncio.shield(task))

    def _blocked_project_reasons(self) -> dict[str, dict[str, Any]]:
        reasons: dict[str, dict[str, Any]] = {}
        with self.identity._lock, self.bindings._lock:
            binding_data = self.bindings._read()
            latest_by_workflow = self._latest_jobs_by_workflow_locked()
            project_workflows: dict[str, list[str]] = {}
            for binding in binding_data.get("workflows", {}).values():
                if not isinstance(binding, dict) or not binding.get("project_id"):
                    continue
                project_workflows.setdefault(str(binding["project_id"]), []).append(
                    str(binding.get("workflow_ref") or "")
                )
            for project_id, workflow_refs in project_workflows.items():
                try:
                    contexts = self._project_contexts_locked(
                        project_id,
                        binding_data,
                        latest_by_workflow,
                    )
                except (OSError, ValueError) as exc:
                    reasons[project_id] = {
                        "code": getattr(exc, "code", "identity_evidence_unavailable"),
                        "message": str(exc),
                    }
                    continue
                if contexts is None:
                    reasons[project_id] = {
                        "code": "identity_analysis_in_progress",
                        "message": "Portrait sync is paused until every current project identity scan completes.",
                    }
            with self._lock:
                outbox = _read_json(self.outbox_path)
                for project_id, error in outbox["projectErrors"].items():
                    if isinstance(error, dict):
                        reasons[str(project_id)] = deepcopy(error)
        return reasons

    @staticmethod
    def _public_project_error(
        project_id: str,
        error: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "operationId": f"project:{project_id}",
            "state": "projection_blocked",
            "projectId": project_id,
            "displayOrder": None,
            "attemptCount": 0,
            "lastStatus": None,
            "code": str(error.get("code") or "identity_projection_blocked"),
            "message": str(error.get("message") or "Portrait sync is paused."),
        }

    async def _drain_once(self) -> list[dict[str, Any]]:
        states = []
        deferred_waiting: set[str] = set()
        while True:
            blocked_projects = self._blocked_project_reasons()
            cleanup_operation = None
            with self._lock:
                outbox = _read_json(self.outbox_path)
                next_item = next(
                    (
                        (operation_id, deepcopy(operation))
                        for operation_id, operation in sorted(
                            outbox["operations"].items(),
                            key=lambda item: (
                                {
                                    "retire_pending": 0,
                                    "pending": 1,
                                    "waiting_for_person": 2,
                                }.get(
                                    str(item[1].get("state") or ""), 3
                                )
                                if isinstance(item[1], dict)
                                else 4,
                                item[0],
                            ),
                        )
                        if isinstance(operation, dict)
                        and operation.get("state")
                        in {"waiting_for_person", "pending", "retire_pending"}
                        and operation_id not in deferred_waiting
                        and str(operation.get("projectId") or "")
                        not in blocked_projects
                    ),
                    None,
                )
            if next_item is None:
                return states + [
                    self._public_project_error(project_id, error)
                    for project_id, error in sorted(blocked_projects.items())
                ]
            operation_id, operation = next_item
            canonical_person_id = self._canonical_person_for_operation(operation)
            state = str(operation.get("state") or "")
            if not canonical_person_id:
                if state == "waiting_for_person":
                    states.append(self._public_state(operation))
                    deferred_waiting.add(operation_id)
                    continue
                if state == "retire_pending":
                    cleanup_operation = None
                    with self._lock:
                        outbox = _read_json(self.outbox_path)
                        current = outbox["operations"].get(operation_id)
                        if isinstance(current, dict):
                            current["state"] = "retired"
                            write_private_json(self.outbox_path, outbox)
                            cleanup_operation = deepcopy(current)
                    if cleanup_operation is not None:
                        # Terminal state is durable before best-effort local
                        # cleanup. A crash here cannot replay a hosted write.
                        self._remove_staged(cleanup_operation)
                    continue

            if state == "waiting_for_person":
                with self._lock:
                    outbox = _read_json(self.outbox_path)
                    current = outbox["operations"].get(operation_id)
                    if isinstance(current, dict):
                        current["canonicalPersonId"] = canonical_person_id
                        current["state"] = "pending"
                        write_private_json(self.outbox_path, outbox)
                continue

            if state == "retire_pending" and not operation.get("storageGeneration"):
                project_id = str(operation.get("projectId") or "")
                content_sha256 = str(operation.get("contentSha256") or "").lower()
                if (
                    len(content_sha256) != 64
                    or any(value not in "0123456789abcdef" for value in content_sha256)
                ):
                    self._record_project_error(
                        project_id,
                        "portrait_generation_unavailable",
                        "Portrait cleanup is paused because its exact local content proof is unavailable. Re-run identity review, then retry sync.",
                    )
                    states.append(self._public_state(operation, last_status=409))
                    return states + [
                        self._public_project_error(
                            project_id,
                            {
                                "code": "portrait_generation_unavailable",
                                "message": "Portrait cleanup is paused because its exact local content proof is unavailable. Re-run identity review, then retry sync.",
                            },
                        )
                    ]
                status, response = (
                    await remote.resolve_project_person_portrait_generation(
                        self.connection_path,
                        project_id,
                        canonical_person_id,
                        str(operation.get("clientPortraitId") or ""),
                        content_sha256,
                    )
                )
                cleanup_operation = None
                recovered_generation = False
                with self._lock:
                    outbox = _read_json(self.outbox_path)
                    current = outbox["operations"].get(operation_id)
                    if (
                        not isinstance(current, dict)
                        or current.get("state") != state
                        or current.get("storageGeneration")
                    ):
                        continue
                    current["attemptCount"] = int(current.get("attemptCount") or 0) + 1
                    current["canonicalPersonId"] = canonical_person_id
                    proof = response.get("proof") if isinstance(response, dict) else None
                    if 200 <= status < 300:
                        if isinstance(proof, dict) and proof.get("found") is False:
                            current["state"] = "retired"
                            current["syncedAt"] = int(time.time())
                            current.pop("lastStatus", None)
                            current.pop("requiresReconnect", None)
                            outbox["projectErrors"].pop(project_id, None)
                            write_private_json(self.outbox_path, outbox)
                            cleanup_operation = deepcopy(current)
                        elif (
                            isinstance(proof, dict)
                            and proof.get("found") is True
                            and proof.get("materialMatches") is True
                        ):
                            try:
                                generation = str(
                                    uuid.UUID(str(proof.get("storageGeneration")))
                                )
                            except (ValueError, TypeError, AttributeError):
                                generation = ""
                            if generation:
                                current["storageGeneration"] = generation
                                current.pop("lastStatus", None)
                                current.pop("lastError", None)
                                current.pop("requiresReconnect", None)
                                outbox["projectErrors"].pop(project_id, None)
                                write_private_json(self.outbox_path, outbox)
                                recovered_generation = True
                            else:
                                status = 502
                        if cleanup_operation is None and not recovered_generation:
                            current["lastStatus"] = int(status if status >= 400 else 409)
                            current["lastError"] = "portrait_generation_proof_mismatch"
                            outbox["projectErrors"][project_id] = {
                                "code": "portrait_generation_proof_mismatch",
                                "message": "Portrait cleanup is paused because the hosted portrait no longer matches its exact frozen local content. Re-run identity review before retrying.",
                                "updatedAt": int(time.time()),
                            }
                            write_private_json(self.outbox_path, outbox)
                    else:
                        current["lastStatus"] = int(status)
                        if status == 401:
                            current["requiresReconnect"] = True
                        if isinstance(response, dict) and response.get("state"):
                            current["lastRemoteState"] = str(response["state"])[:80]
                        write_private_json(self.outbox_path, outbox)
                    public = self._public_state(current)
                states.append(public)
                if cleanup_operation is not None:
                    self._remove_staged(cleanup_operation)
                    continue
                if recovered_generation:
                    # The exact generation receipt is durable. The next loop
                    # performs the separately idempotent CAS retirement.
                    continue
                project_error = self._blocked_project_reasons().get(project_id)
                if project_error:
                    states.append(self._public_project_error(project_id, project_error))
                return states

            if state == "pending":
                staged_file = str(operation.get("stagedFile") or "")
                if not staged_file or os.path.basename(staged_file) != staged_file:
                    self._record_project_error(
                        str(operation.get("projectId") or ""),
                        "portrait_generation_unavailable",
                        "Portrait cleanup is paused because its opaque hosted generation cannot be recovered safely. Re-run identity review, then retry sync.",
                    )
                    states.append(self._public_state(operation, last_status=409))
                    return states
                staged_path = os.path.join(self.staging_dir, staged_file)
                try:
                    with open(staged_path, "rb") as handle:
                        payload = handle.read(MAX_LOCAL_PORTRAIT_BYTES + 1)
                except OSError:
                    states.append(self._public_state(operation, last_status=0))
                    return states
                if hashlib.sha256(payload).hexdigest() != operation.get("contentSha256"):
                    raise IdentityPersistenceError(
                        "A queued portrait no longer matches its frozen content hash."
                    )
                status, response = await remote.upload_project_person_portrait(
                    self.connection_path,
                    str(operation.get("projectId") or ""),
                    canonical_person_id,
                    str(operation.get("clientPortraitId") or ""),
                    payload,
                    content_sha256=str(operation.get("contentSha256") or ""),
                    mime_type=str(operation.get("mimeType") or ""),
                    display_order=int(operation.get("displayOrder") or 0),
                    make_primary=bool(operation.get("makePrimary")),
                )
            else:
                status, response = await remote.retire_project_person_portrait(
                    self.connection_path,
                    str(operation.get("projectId") or ""),
                    canonical_person_id,
                    str(operation.get("clientPortraitId") or ""),
                    str(operation.get("storageGeneration") or ""),
                )

            with self._lock:
                outbox = _read_json(self.outbox_path)
                current = outbox["operations"].get(operation_id)
                if not isinstance(current, dict) or current.get("state") != state:
                    continue
                current["attemptCount"] = int(current.get("attemptCount") or 0) + 1
                current["canonicalPersonId"] = canonical_person_id
                stale_retirement = (
                    state == "retire_pending"
                    and status == 409
                    and isinstance(response, dict)
                    and response.get("pluginCode") == "stale_portrait_generation"
                )
                if stale_retirement:
                    conflict_generation = response.get("currentStorageGeneration")
                    conflict_content = response.get("currentClientContentSha256")
                    try:
                        conflict_generation = str(uuid.UUID(str(conflict_generation)))
                    except (ValueError, TypeError, AttributeError):
                        conflict_generation = None
                    if conflict_generation:
                        current["conflictStorageGeneration"] = conflict_generation
                    if (
                        isinstance(conflict_content, str)
                        and len(conflict_content) == 64
                        and all(value in "0123456789abcdef" for value in conflict_content)
                    ):
                        current["conflictClientContentSha256"] = conflict_content
                    current["lastStatus"] = 409
                    current["lastError"] = "stale_portrait_generation"
                    project_id = str(current.get("projectId") or "")
                    outbox["projectErrors"][project_id] = {
                        "code": "stale_portrait_generation",
                        "message": "Portrait sync found a newer hosted generation. Run Sync retry to reproject current review before cleanup continues.",
                        "updatedAt": int(time.time()),
                    }
                    write_private_json(self.outbox_path, outbox)
                    states.append(self._public_state(current, last_status=409))
                    return states + [
                        self._public_project_error(
                            project_id,
                            outbox["projectErrors"][project_id],
                        )
                    ]
                if 200 <= status < 300:
                    if state == "pending":
                        portrait = response.get("portrait") if isinstance(response, dict) else None
                        generation = (
                            portrait.get("storageGeneration")
                            if isinstance(portrait, dict)
                            else None
                        )
                        try:
                            generation = str(uuid.UUID(str(generation)))
                        except (ValueError, TypeError, AttributeError):
                            current["lastStatus"] = 502
                            current["lastError"] = "invalid_portrait_generation_receipt"
                            write_private_json(self.outbox_path, outbox)
                            states.append(self._public_state(current, last_status=502))
                            return states
                        current["storageGeneration"] = generation
                        current.pop("lastError", None)
                    synced_at = int(time.time())
                    after_retire = current.get("afterRetire")
                    if state == "retire_pending" and isinstance(after_retire, dict):
                        replacement = deepcopy(after_retire)
                        replacement["operationId"] = operation_id
                        replacement["state"] = (
                            "pending"
                            if replacement.get("canonicalPersonId")
                            else "waiting_for_person"
                        )
                        replacement["attemptCount"] = 0
                        replacement["retiredBeforeUploadAt"] = synced_at
                        outbox["operations"][operation_id] = replacement
                        current = replacement
                    else:
                        current["state"] = "synced" if state == "pending" else "retired"
                        current["syncedAt"] = synced_at
                        current.pop("lastStatus", None)
                        current.pop("requiresReconnect", None)
                        cleanup_operation = deepcopy(current)
                else:
                    current["lastStatus"] = int(status)
                    if status == 401:
                        current["requiresReconnect"] = True
                    if isinstance(response, dict) and response.get("state"):
                        current["lastRemoteState"] = str(response["state"])[:80]
                write_private_json(self.outbox_path, outbox)
                public = self._public_state(current)
                states.append(public)
                if not 200 <= status < 300:
                    return states
            if cleanup_operation is not None:
                # The opaque generation receipt and terminal state must win
                # the crash race. Local staged bytes are removed only after
                # that durable write succeeds.
                self._remove_staged(cleanup_operation)

    def sync_status(self) -> list[dict[str, Any]]:
        blocked_projects = self._blocked_project_reasons()
        with self._lock:
            outbox = _read_json(self.outbox_path)
            operations = [
                self._public_state(value)
                for _, value in sorted(outbox["operations"].items())
                if isinstance(value, dict) and value.get("state") != "retired"
            ]
        return operations + [
            self._public_project_error(project_id, error)
            for project_id, error in sorted(blocked_projects.items())
        ]

    @staticmethod
    def _public_state(
        operation: dict[str, Any],
        *,
        last_status: int | None = None,
    ) -> dict[str, Any]:
        return {
            "operationId": operation.get("operationId"),
            "state": operation.get("state"),
            "projectId": operation.get("projectId"),
            "displayOrder": operation.get("displayOrder"),
            "attemptCount": operation.get("attemptCount", 0),
            "lastStatus": (
                last_status if last_status is not None else operation.get("lastStatus")
            ),
            "requiresReconnect": bool(operation.get("requiresReconnect")),
            "code": operation.get("lastError"),
        }
