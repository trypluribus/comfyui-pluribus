"""Crash-consistent local identity decisions and workspace-sync intent.

Identity analysis links and workflow person drafts historically lived in two
independently atomic JSON files.  This coordinator keeps their legacy storage
formats, but commits one producer decision through a private write-ahead
journal and records a durable outbox entry for later hosted synchronization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
from copy import deepcopy
from typing import Any, Callable

from . import remote
from .bindings import (
    BindingStore,
    SCHEMA_VERSION as BINDINGS_SCHEMA_VERSION,
    _normalize_person_draft,
    _normalize_person_tombstone,
    _normalize_workspace_alias,
    _normalize_workspace_alias_history,
    _require_identifier,
    _require_uuid,
)
from .identity_service import (
    IdentityAnalysisService,
    IdentityConflictError,
    IdentityPersistenceError,
    SAFE_ID,
)
from .storage import ensure_private_dir, write_private_json


DECISION_SCHEMA_VERSION = 1
OUTBOX_SCHEMA_VERSION = 1
MAX_MERGE_DRAFTS = 100
MAX_OCCURRENCES = 2000
_DECISION_ALIASES = {
    "same": "confirmed",
    "different": "confirmed",
    "confirmed": "confirmed",
    "unsure": "unsure",
    "false_positive": "rejected",
    "rejected": "rejected",
}
_ACTIONS = {"assign", "combine"}
_TARGET_FIELDS = (
    "draftId",
    "canonicalPersonId",
    "displayName",
    "role",
    "talentEmail",
    "representative",
    "notes",
)


def _stable_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _stable_hash(value: object) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _read_json_strict(path: str, default: dict[str, Any]) -> dict[str, Any]:
    if not os.path.isfile(path):
        return deepcopy(default)
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise IdentityPersistenceError(
            "Private identity state could not be verified. Restart ComfyUI before "
            "saving another identity decision."
        ) from exc
    if not isinstance(value, dict):
        raise IdentityPersistenceError(
            "Private identity state could not be verified. Restart ComfyUI before "
            "saving another identity decision."
        )
    return value


def occurrence_source_index(cached: object) -> dict[str, str]:
    """Return the complete occurrence-to-source mapping for one analysis cache."""

    result: dict[str, str] = {}
    if not isinstance(cached, dict):
        return result
    for occurrence in cached.get("occurrences", []):
        if not isinstance(occurrence, dict):
            continue
        occurrence_id = str(occurrence.get("occurrenceId") or "")
        source_ref = str(occurrence.get("sourceRef") or "")
        if occurrence_id and source_ref:
            result[occurrence_id] = source_ref
    return result


def _candidate_indexes(
    cached: object,
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    occurrence_ids: dict[str, list[str]] = {}
    source_refs: dict[str, list[str]] = {}
    if not isinstance(cached, dict):
        return occurrence_ids, source_refs
    for candidate in cached.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("candidateId") or "")
        if not candidate_id:
            continue
        occurrence_ids[candidate_id] = sorted(
            {str(value) for value in candidate.get("occurrenceIds", []) if str(value)}
        )
        source_refs[candidate_id] = sorted(
            {str(value) for value in candidate.get("sourceRefs", []) if str(value)}
        )
    return occurrence_ids, source_refs


def _alias_map(
    drafts: dict[str, Any], tombstones: dict[str, Any]
) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for draft_id, value in drafts.items():
        if not isinstance(value, dict):
            continue
        canonical = str(value.get("canonicalPersonId") or "")
        aliases[str(draft_id)] = canonical or str(draft_id)

    def resolve(value: str) -> str:
        current = value
        seen: set[str] = set()
        while current in tombstones:
            if current in seen:
                raise ValueError("Person alias tombstones contain a cycle.")
            seen.add(current)
            tombstone = _normalize_person_tombstone(tombstones[current])
            current = tombstone["mergedIntoDraftId"]
        return aliases.get(current, current)

    for draft_id in tombstones:
        aliases[str(draft_id)] = resolve(str(draft_id))
    return aliases


def _resolve_person_id(person_id: object, aliases: dict[str, str]) -> str:
    current = str(person_id or "")
    seen: set[str] = set()
    while current in aliases and aliases[current] != current:
        if current in seen:
            raise ValueError("Person aliases contain a cycle.")
        seen.add(current)
        current = aliases[current]
    return current


def _project_scoped_links(
    links: object,
    binding: dict[str, Any],
) -> list[dict[str, Any]]:
    """Translate archived hosted ids back to stable local aliases on rebind."""

    result: list[dict[str, Any]] = []
    for raw_link in links if isinstance(links, list) else []:
        if not isinstance(raw_link, dict):
            continue
        link = deepcopy(raw_link)
        if str(link.get("state") or "confirmed") == "confirmed":
            raw_person_id = link.get("personId", link.get("person_id"))
            if raw_person_id:
                link["personId"] = (
                    BindingStore._resolve_project_scoped_person_in_binding(
                        binding, raw_person_id
                    )
                )
                link.pop("person_id", None)
        result.append(link)
    return result


def person_source_projection(
    links: object,
    cached: object,
    aliases: dict[str, str] | None = None,
    person_drafts: object | None = None,
) -> dict[str, list[str]]:
    """Project every confirmed occurrence into complete person/source membership.

    Legacy candidate-wide links without ``occurrenceIds`` expand to every
    occurrence in that candidate.  Candidates without face occurrences fall
    back to their source refs, preserving the older source-level contract.
    """

    aliases = aliases or {}
    occurrence_sources = occurrence_source_index(cached)
    candidate_occurrences, candidate_sources = _candidate_indexes(cached)
    projected: dict[str, set[str]] = {}
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict) or str(link.get("state") or "confirmed") != "confirmed":
            continue
        person_id = _resolve_person_id(
            link.get("personId", link.get("person_id")), aliases
        )
        candidate_id = str(link.get("candidateId", link.get("candidate_id")) or "")
        if not person_id or not candidate_id:
            continue
        selected = link.get("occurrenceIds", link.get("occurrence_ids"))
        occurrence_ids = (
            [str(value) for value in selected]
            if isinstance(selected, list) and selected
            else candidate_occurrences.get(candidate_id, [])
        )
        sources = {
            occurrence_sources[occurrence_id]
            for occurrence_id in occurrence_ids
            if occurrence_id in occurrence_sources
        }
        if not sources and not candidate_occurrences.get(candidate_id):
            selected_sources = link.get("sourceRefs", link.get("source_refs"))
            sources.update(
                str(value)
                for value in (
                    selected_sources
                    if isinstance(selected_sources, list) and selected_sources
                    else candidate_sources.get(candidate_id, [])
                )
                if str(value)
            )
        projected.setdefault(person_id, set()).update(sources)

    draft_items = (
        person_drafts.items()
        if isinstance(person_drafts, dict)
        else (
            (
                (str(value.get("draftId") or ""), value)
                for value in person_drafts
                if isinstance(value, dict)
            )
            if isinstance(person_drafts, list)
            else []
        )
    )
    for draft_id, draft in draft_items:
        if not isinstance(draft, dict):
            continue
        person_id = _resolve_person_id(draft_id, aliases)
        if not person_id:
            continue
        projected.setdefault(person_id, set()).update(
            str(value) for value in draft.get("sourceRefs", []) if str(value)
        )
    return {
        person_id: sorted(source_refs)
        for person_id, source_refs in sorted(projected.items())
    }


def source_person_projection(
    links: object,
    cached: object,
    aliases: dict[str, str] | None = None,
    person_drafts: object | None = None,
) -> dict[str, list[str]]:
    """Invert ``person_source_projection`` for complete hosted manifest sync."""

    sources: dict[str, set[str]] = {}
    for person_id, source_refs in person_source_projection(
        links, cached, aliases, person_drafts
    ).items():
        for source_ref in source_refs:
            sources.setdefault(source_ref, set()).add(person_id)
    return {
        source_ref: sorted(person_ids)
        for source_ref, person_ids in sorted(sources.items())
    }


def identity_review_hash(links: object) -> str:
    """Hash normalized review decisions without exposing local occurrence data."""

    normalized = []
    for link in links if isinstance(links, list) else []:
        if not isinstance(link, dict):
            continue
        normalized.append(
            {
                "candidateId": str(
                    link.get("candidateId", link.get("candidate_id")) or ""
                ),
                "personId": str(link.get("personId", link.get("person_id")) or ""),
                "state": str(link.get("state") or "confirmed"),
                "occurrenceIds": sorted(
                    {
                        str(value)
                        for value in (
                            link.get("occurrenceIds", link.get("occurrence_ids")) or []
                        )
                    }
                ),
                "sourceRefs": sorted(
                    {
                        str(value)
                        for value in (
                            link.get("sourceRefs", link.get("source_refs")) or []
                        )
                    }
                ),
            }
        )
    normalized.sort(
        key=lambda value: (
            value["candidateId"],
            value["personId"],
            value["state"],
            value["occurrenceIds"],
        )
    )
    return _stable_hash(normalized)


class IdentityDecisionService:
    """Coordinate one local producer decision and its durable sync intent."""

    def __init__(
        self,
        identity: IdentityAnalysisService,
        bindings: BindingStore,
        *,
        connection_path: str | None = None,
    ):
        self.identity = identity
        self.bindings = bindings
        self.connection_path = connection_path or ""
        self.transactions_dir = os.path.join(identity.data_dir, "decisions")
        self.outbox_path = os.path.join(identity.data_dir, "sync-outbox.json")
        self._pending_drain_task: asyncio.Task[list[dict[str, Any]]] | None = None
        self._pending_drain_loop: asyncio.AbstractEventLoop | None = None
        self._entry_drain_tasks: dict[
            str,
            tuple[asyncio.AbstractEventLoop, asyncio.Task[dict[str, Any]]],
        ] = {}
        ensure_private_dir(self.transactions_dir)
        self._poisoned_error: IdentityPersistenceError | None = None
        try:
            with self.identity._lock, self.bindings._lock:
                self._recover_prepared_transactions_locked()
        except IdentityPersistenceError as exc:
            # Keep routes available so the UI receives a fail-closed 503 with a
            # recovery message instead of silently losing the entire plugin.
            self._poisoned_error = exc

    def _assert_healthy(self) -> None:
        if self._poisoned_error is not None:
            raise self._poisoned_error

    def put_decision(self, job_id: str, body: object) -> dict[str, Any]:
        self._assert_healthy()
        request = self._normalize_request(job_id, body)
        transaction_id = _stable_hash(request)
        transaction_path = self._transaction_path(transaction_id)
        with self.identity._lock, self.bindings._lock:
            self._recover_prepared_transactions_locked()
            replay = self._committed_replay(transaction_path, request)
            if replay is not None:
                return replay
            return self._commit_locked(
                request,
                transaction_id=transaction_id,
                transaction_path=transaction_path,
            )

    def pending_sync_entries(
        self, workflow_ref: object | None = None
    ) -> list[dict[str, Any]]:
        self._assert_healthy()
        wanted = str(workflow_ref or "")
        with self.identity._lock:
            outbox = self._read_outbox()
            values = [
                deepcopy(value)
                for value in outbox["entries"].values()
                if isinstance(value, dict)
                and value.get("state") != "synced"
                and (not wanted or value.get("workflowRef") == wanted)
            ]
            return sorted(values, key=lambda value: (value.get("revision", 0), value.get("entryId", "")))

    def mark_sync_entry_synced(self, entry_id: object) -> dict[str, Any]:
        """Acknowledge a successful remote drain without deleting its receipt."""

        self._assert_healthy()
        safe_entry_id = str(entry_id or "")
        if not SAFE_ID.fullmatch(safe_entry_id):
            raise ValueError("entryId must be an opaque identifier.")
        with self.identity._lock:
            outbox = self._read_outbox()
            entry = outbox["entries"].get(safe_entry_id)
            if not isinstance(entry, dict):
                raise ValueError("Identity sync entry was not found.")
            if not self._person_phase_complete(entry):
                raise IdentityConflictError(
                    "Workspace manifest sync cannot finish before every local person "
                    "has a canonical project mapping."
                )
            entry["state"] = "synced"
            entry["syncedAt"] = int(time.time())
            write_private_json(self.outbox_path, outbox)
            return self._sync_state(entry, outbox)

    def drain_sync_outbox(
        self, drain: Callable[[dict[str, Any]], bool]
    ) -> list[dict[str, Any]]:
        """Retry pending entries through a caller-supplied remote drain.

        This module deliberately performs no network I/O.  The hosted client
        can promote ``clientPersonId`` idempotently, PUT the complete source
        projection, and return true only after both phases succeed.
        """

        states = []
        for entry in self.pending_sync_entries():
            if drain(deepcopy(entry)):
                states.append(self.mark_sync_entry_synced(entry["entryId"]))
            else:
                states.append(self._sync_state(entry, self._read_outbox()))
        return states

    def mark_workflow_revision_synced(
        self, workflow_ref: object, revision: object
    ) -> list[dict[str, Any]]:
        """Acknowledge every covered entry after a full manifest PUT.

        Browser saves coalesce by workflow.  A manifest for revision N is the
        complete projection after all decisions through N, so it safely covers
        earlier entries but must never acknowledge a future revision.
        """

        self._assert_healthy()
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            return []
        wanted = str(workflow_ref or "")
        with self.identity._lock:
            outbox = self._read_outbox()
            changed = []
            for entry in outbox["entries"].values():
                if not isinstance(entry, dict):
                    continue
                if (
                    entry.get("workflowRef") == wanted
                    and isinstance(entry.get("revision"), int)
                    and not isinstance(entry.get("revision"), bool)
                    and entry.get("revision") <= revision
                    and entry.get("state") != "synced"
                    and self._person_phase_complete(entry)
                ):
                    entry["state"] = "synced"
                    entry["syncedAt"] = int(time.time())
                    changed.append(entry)
            if changed:
                write_private_json(self.outbox_path, outbox)
            return [self._sync_state(entry, outbox) for entry in changed]

    @staticmethod
    def _person_phase_complete(entry: dict[str, Any]) -> bool:
        people = entry.get("people")
        if not isinstance(people, list) or not people:
            return entry.get("personPhaseState") in {None, "not_required", "synced"}
        for person in people:
            if not isinstance(person, dict) or person.get("state") not in {
                "synced",
                "superseded",
            }:
                return False
            canonical_person_id = str(person.get("canonicalPersonId") or "")
            if person.get("state") == "synced" and not canonical_person_id:
                return False
        projected_ids = {
            str(person_id)
            for source in entry.get("sourcePeople", [])
            if isinstance(source, dict)
            for person_id in source.get("personIds", [])
        }
        local_person_ids = {
            str(value) for value in entry.get("localPersonIds", []) if str(value)
        }
        return not (projected_ids & local_person_ids)

    def sync_status(self) -> list[dict[str, Any]]:
        """Return safe state summaries without exposing private outbox payloads."""

        self._assert_healthy()
        with self.identity._lock:
            outbox = self._read_outbox()
            return [
                self._sync_state(entry, outbox)
                for entry in sorted(
                    (
                        value
                        for value in outbox["entries"].values()
                        if isinstance(value, dict)
                    ),
                    key=lambda value: (value.get("revision", 0), value.get("entryId", "")),
                )
            ]

    def reconciliation_preview(self, job_id: str) -> dict[str, Any]:
        """Describe local identity divergence without mutating or auto-merging."""

        self._assert_healthy()
        try:
            safe_job_id = str(uuid.UUID(str(job_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid identity job identifier.") from exc
        with self.identity._lock, self.bindings._lock:
            job = self.identity._get_job_record(safe_job_id)
            workflow_ref = str(job.get("workflowRef") or "")
            if not workflow_ref:
                raise ValueError("Reconciliation requires a workflow-scoped analysis job.")
            cached = self.identity._read_cache(str(job.get("cacheKey") or "")) or {}
            current = self.identity.get_links(safe_job_id)
            bindings_data = self._read_bindings_strict()
            binding = self.bindings._find(bindings_data, workflow_ref)
            active_drafts = binding.get("person_drafts")
            if not isinstance(active_drafts, dict):
                active_drafts = {}
            tombstones = binding.get("person_draft_tombstones")
            if not isinstance(tombstones, dict):
                tombstones = {}
            aliases = _alias_map(active_drafts, tombstones)
            candidate_occurrences, _candidate_sources = _candidate_indexes(cached)
            occurrence_sources = occurrence_source_index(cached)
            projection = person_source_projection(current["links"], cached, aliases)

            owners: dict[str, set[str]] = {}
            person_occurrences: dict[str, set[str]] = {}
            person_candidates: dict[str, set[str]] = {}
            for link in current["links"]:
                if str(link.get("state") or "confirmed") != "confirmed":
                    continue
                candidate_id = str(link.get("candidateId") or "")
                person_id = _resolve_person_id(link.get("personId"), aliases)
                selected = link.get("occurrenceIds")
                occurrence_ids = (
                    [str(value) for value in selected]
                    if isinstance(selected, list) and selected
                    else candidate_occurrences.get(candidate_id, [])
                )
                for occurrence_id in occurrence_ids:
                    owners.setdefault(occurrence_id, set()).add(person_id)
                    person_occurrences.setdefault(person_id, set()).add(occurrence_id)
                person_candidates.setdefault(person_id, set()).add(candidate_id)
            conflicts = [
                {
                    "occurrenceId": occurrence_id,
                    "personIds": sorted(person_ids),
                    "sourceRef": occurrence_sources.get(occurrence_id),
                }
                for occurrence_id, person_ids in sorted(owners.items())
                if len(person_ids) > 1
            ]

            active = []
            for draft_id, value in sorted(active_drafts.items()):
                if not isinstance(value, dict):
                    raise IdentityPersistenceError(
                        "A local person draft could not be verified."
                    )
                resolved_person_id = _resolve_person_id(draft_id, aliases)
                active.append(
                    {
                        "draftId": draft_id,
                        "canonicalPersonId": value.get("canonicalPersonId"),
                        "resolvedPersonId": resolved_person_id,
                        "displayName": str(value.get("displayName") or ""),
                        "role": str(value.get("role") or ""),
                        "sourceCount": len(set(value.get("sourceRefs") or [])),
                        "projectedSourceCount": len(projection.get(resolved_person_id, [])),
                        "occurrenceCount": len(
                            person_occurrences.get(resolved_person_id, set())
                        ),
                        "candidateCount": len(
                            person_candidates.get(resolved_person_id, set())
                        ),
                    }
                )

            resolved_tombstones = []
            for value in sorted(
                tombstones.values(), key=lambda item: str(item.get("draftId") or "")
            ):
                tombstone = _normalize_person_tombstone(value)
                resolved_tombstones.append(
                    {
                        **tombstone,
                        "resolvedPersonId": _resolve_person_id(
                            tombstone["draftId"], aliases
                        ),
                    }
                )

            suspected_pairs = []
            for index, left in enumerate(active):
                left_value = active_drafts[left["draftId"]]
                for right in active[index + 1 :]:
                    right_value = active_drafts[right["draftId"]]
                    evidence = self._alias_evidence(left_value, right_value)
                    if not evidence:
                        continue
                    suspected_pairs.append(
                        {
                            "draftIds": [left["draftId"], right["draftId"]],
                            "displayNames": [left["displayName"], right["displayName"]],
                            "evidence": evidence,
                            "requiresExplicitCombine": True,
                        }
                    )

            return {
                "jobId": safe_job_id,
                "workflowRef": workflow_ref,
                "revision": current["revision"],
                "counts": {
                    "activeDrafts": len(active),
                    "tombstones": len(resolved_tombstones),
                    "confirmedOccurrences": len(owners),
                    "projectedSources": len(
                        {
                            source_ref
                            for source_refs in projection.values()
                            for source_ref in source_refs
                        }
                    ),
                    "ownershipConflicts": len(conflicts),
                    "suspectedAliasPairs": len(suspected_pairs),
                },
                "activeDrafts": active,
                "resolvedAliases": resolved_tombstones,
                "ownershipConflicts": conflicts[:100],
                "suspectedAliasPairs": suspected_pairs,
                "readOnly": True,
            }

    @staticmethod
    def _alias_evidence(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
        def normalized_text(value: object) -> str:
            return " ".join(str(value or "").casefold().split())

        def contacts(value: dict[str, Any]) -> set[str]:
            result = set()
            talent_email = normalized_text(value.get("talentEmail"))
            if talent_email:
                result.add(talent_email)
            representative = value.get("representative")
            if isinstance(representative, dict):
                representative_email = normalized_text(representative.get("email"))
                if representative_email:
                    result.add(representative_email)
            return result

        evidence = []
        left_name = normalized_text(left.get("displayName"))
        right_name = normalized_text(right.get("displayName"))
        # Shared representatives routinely reuse one contact address across
        # multiple performers. Never suggest a person-level alias from contact
        # alone; a normalized name match is the minimum preview signal and is
        # still only evidence for an explicit producer-confirmed combine.
        if not left_name or left_name != right_name:
            return []
        evidence.append("exact_normalized_name")
        left_role = normalized_text(left.get("role"))
        right_role = normalized_text(right.get("role"))
        if left_role and left_role == right_role:
            evidence.append("exact_normalized_role")
        if contacts(left) & contacts(right):
            evidence.append("exact_normalized_contact")
        return evidence

    @staticmethod
    def _workspace_alias_for_client(
        binding: dict[str, Any], client_person_id: str
    ) -> dict[str, str] | None:
        drafts = binding.get("person_drafts")
        draft = drafts.get(client_person_id) if isinstance(drafts, dict) else None
        if isinstance(draft, dict) and draft.get("workspaceAlias"):
            return _normalize_workspace_alias(
                draft.get("workspaceAlias"),
                draft_id=client_person_id,
                canonical_person_id=draft.get("canonicalPersonId"),
            )
        tombstones = binding.get("person_draft_tombstones")
        tombstone = (
            tombstones.get(client_person_id)
            if isinstance(tombstones, dict)
            else None
        )
        if isinstance(tombstone, dict) and tombstone.get("workspaceAlias"):
            normalized = _normalize_person_tombstone(tombstone)
            marker = normalized.get("workspaceAlias")
            return marker if isinstance(marker, dict) else None
        return None

    @staticmethod
    def _canonical_for_client(
        binding: dict[str, Any], client_person_id: str
    ) -> str:
        drafts = binding.get("person_drafts")
        if not isinstance(drafts, dict):
            drafts = {}
        tombstones = binding.get("person_draft_tombstones")
        if not isinstance(tombstones, dict):
            tombstones = {}
        aliases = _alias_map(drafts, tombstones)
        resolved = _resolve_person_id(client_person_id, aliases)
        local_ids = {str(value) for value in [*drafts.keys(), *tombstones.keys()]}
        return "" if not resolved or resolved in local_ids else resolved

    @staticmethod
    def _workspace_alias_covers_operation(
        marker: dict[str, str], operation: dict[str, Any]
    ) -> bool:
        if operation.get("clientPersonId") != marker["clientPersonId"]:
            return False
        request_body = operation.get("requestBody")
        if not isinstance(request_body, dict):
            return operation.get("operationKind") == "merge_alias"
        if _stable_hash(request_body) == marker["requestHash"]:
            return True
        return (
            request_body.get("mode") == "existing"
            and request_body.get("talentRecordId") == marker["canonicalPersonId"]
        )

    def _reconcile_outbox_entry_with_binding(
        self, entry: dict[str, Any], binding: dict[str, Any]
    ) -> bool:
        """Apply durable local mappings to one outbox entry without network I/O."""

        changed = False
        drafts = binding.get("person_drafts")
        if not isinstance(drafts, dict):
            drafts = {}
        tombstones = binding.get("person_draft_tombstones")
        if not isinstance(tombstones, dict):
            tombstones = {}
        aliases = _alias_map(drafts, tombstones)

        people = entry.get("people")
        if not isinstance(people, list):
            people = []
            entry["people"] = people
            changed = True
        for operation in people:
            if not isinstance(operation, dict):
                continue
            client_person_id = str(operation.get("clientPersonId") or "")
            if not client_person_id or operation.get("state") == "superseded":
                continue
            marker = self._workspace_alias_for_client(binding, client_person_id)
            if marker and self._workspace_alias_covers_operation(marker, operation):
                if (
                    operation.get("state") != "synced"
                    or operation.get("canonicalPersonId")
                    != marker["canonicalPersonId"]
                ):
                    operation["state"] = "synced"
                    operation["canonicalPersonId"] = marker["canonicalPersonId"]
                    operation["requestHash"] = marker["requestHash"]
                    operation["syncedAt"] = int(time.time())
                    changed = True
                continue
            if operation.get("operationKind") != "merge_alias":
                continue
            canonical_person_id = self._canonical_for_client(
                binding, client_person_id
            )
            if not canonical_person_id:
                if operation.get("state") != "waiting_for_survivor":
                    operation["state"] = "waiting_for_survivor"
                    changed = True
                continue
            request_body = {
                "mode": "existing",
                "clientPersonId": client_person_id,
                "talentRecordId": canonical_person_id,
            }
            existing_request = operation.get("requestBody")
            if isinstance(existing_request, dict) and existing_request != request_body:
                raise IdentityPersistenceError(
                    "A frozen merged-person alias request could not be reconciled."
                )
            if existing_request != request_body or operation.get("state") != "pending":
                operation["requestBody"] = request_body
                operation["requestHash"] = _stable_hash(request_body)
                operation["canonicalPersonId"] = canonical_person_id
                operation["state"] = "pending"
                changed = True

        for source in entry.get("sourcePeople", []):
            if not isinstance(source, dict):
                continue
            current_ids = [str(value) for value in source.get("personIds", [])]
            resolved_ids = sorted(
                {_resolve_person_id(value, aliases) for value in current_ids if value}
            )
            if current_ids != resolved_ids:
                source["personIds"] = resolved_ids
                changed = True

        next_phase = (
            "synced"
            if people and self._person_phase_complete(entry)
            else ("not_required" if not people else "pending")
        )
        if entry.get("personPhaseState") != next_phase:
            entry["personPhaseState"] = next_phase
            changed = True
        return changed

    def _supersede_tombstoned_operations(
        self,
        outbox: dict[str, Any],
        binding: dict[str, Any],
    ) -> bool:
        """Prevent pre-combine drafts from ever being created as new people."""

        tombstones = binding.get("person_draft_tombstones")
        if not isinstance(tombstones, dict) or not tombstones:
            return False
        changed = False
        workflow_ref = str(binding.get("workflow_ref") or "")
        for entry in outbox.get("entries", {}).values():
            if (
                not isinstance(entry, dict)
                or entry.get("workflowRef") != workflow_ref
            ):
                continue
            for operation in entry.get("people", []):
                if not isinstance(operation, dict):
                    continue
                client_person_id = str(operation.get("clientPersonId") or "")
                if (
                    client_person_id in tombstones
                    and operation.get("operationKind") != "merge_alias"
                    and operation.get("state") != "synced"
                ):
                    operation["state"] = "superseded"
                    operation["supersededByDraftId"] = (
                        BindingStore._resolve_person_alias_in_binding(
                            binding, client_person_id
                        )
                    )
                    operation.pop("requestBody", None)
                    changed = True
            changed = (
                self._reconcile_outbox_entry_with_binding(entry, binding)
                or changed
            )
        return changed

    async def drain_pending_async(self) -> list[dict[str, Any]]:
        """Promote/attach pending people through one in-flight drain.

        Panel load, connection polling, status reads, and explicit retries can
        all schedule this method at once.  Sharing the in-flight task keeps
        those callers from posting the same durable person operation before
        the first response has been reconciled locally.  The task is scoped to
        this service and the current event loop so a restarted service (and
        tests that use separate ``asyncio.run`` loops) never inherits a lock or
        future bound to an old loop.
        """

        loop = asyncio.get_running_loop()
        task = self._pending_drain_task
        if (
            task is None
            or task.done()
            or self._pending_drain_loop is not loop
        ):
            task = loop.create_task(self._drain_pending_once())
            self._pending_drain_task = task
            self._pending_drain_loop = loop

        # A caller going away must not cancel the shared network/reconciliation
        # work for every other trigger.  Each caller receives its own safe copy
        # of the current status snapshot.
        return deepcopy(await asyncio.shield(task))

    async def _drain_pending_once(self) -> list[dict[str, Any]]:
        """Drain one durable snapshot, re-reading each entry before posting."""

        states = []
        for entry in self.pending_sync_entries():
            states.append(await self.drain_sync_entry(entry["entryId"]))
        return states

    async def drain_sync_entry(self, entry_id: object) -> dict[str, Any]:
        """Drain one entry through the same singleflight as background sync.

        The immediate-save route calls this method directly while background
        triggers call it through ``drain_pending_async``.  Both must share the
        in-flight task for an entry or they can post the same frozen operation
        before either response has been persisted.
        """

        self._assert_healthy()
        safe_entry_id = str(entry_id or "")
        if not SAFE_ID.fullmatch(safe_entry_id):
            raise ValueError("entryId must be an opaque identifier.")
        loop = asyncio.get_running_loop()
        current = self._entry_drain_tasks.get(safe_entry_id)
        task = current[1] if current and current[0] is loop else None
        if task is None or task.done():
            task = loop.create_task(self._drain_sync_entry_once(safe_entry_id))
            self._entry_drain_tasks[safe_entry_id] = (loop, task)

            def clear_entry_task(completed: asyncio.Task[dict[str, Any]]) -> None:
                active = self._entry_drain_tasks.get(safe_entry_id)
                if active is not None and active == (loop, completed):
                    self._entry_drain_tasks.pop(safe_entry_id, None)

            task.add_done_callback(clear_entry_task)

        return deepcopy(await asyncio.shield(task))

    async def _drain_sync_entry_once(self, safe_entry_id: str) -> dict[str, Any]:
        """Drain the idempotent person phase for one pending sync entry.

        Full manifest sync still needs the browser's safe source inventory
        (source kind and operations).  The existing source-links PUT names the
        identity revision and acknowledges this entry only after that remote
        manifest commit succeeds.
        """

        while True:
            with self.identity._lock, self.bindings._lock:
                outbox = self._read_outbox()
                stored = outbox["entries"].get(safe_entry_id)
                if not isinstance(stored, dict):
                    raise ValueError("Identity sync entry was not found.")
                if stored.get("state") == "synced":
                    return self._sync_state(stored, outbox)
                workflow_ref = str(stored.get("workflowRef") or "")
                bindings_data = self._read_bindings_strict()
                binding = self.bindings._find(bindings_data, workflow_ref)
                active_project_id = str(binding.get("project_id") or "")
                outbox_changed = self._supersede_tombstoned_operations(
                    outbox, binding
                )
                stored = outbox["entries"].get(safe_entry_id)
                if not isinstance(stored, dict):
                    raise ValueError("Identity sync entry was not found.")
                bound_project_id = str(binding.get("project_id") or "")
                stored_project_id = str(stored.get("projectId") or "")
                if not stored_project_id and bound_project_id:
                    stored["projectId"] = bound_project_id
                    stored_project_id = bound_project_id
                    outbox_changed = True
                elif (
                    stored_project_id
                    and bound_project_id
                    and stored_project_id != bound_project_id
                ):
                    raise IdentityConflictError(
                        "This saved identity decision belongs to a different project."
                    )
                reconciled = self._reconcile_outbox_entry_with_binding(
                    stored, binding
                )
                if reconciled or outbox_changed:
                    write_private_json(self.outbox_path, outbox)
                initial_state = self._sync_state(stored, outbox)
                if initial_state["state"] in {"saved_local", "reconnect_required"}:
                    return initial_state
                people = stored.get("people")
                if not isinstance(people, list):
                    people = []
                pending_person = next(
                    (
                        value
                        for value in people
                        if isinstance(value, dict) and value.get("state") == "pending"
                    ),
                    None,
                )
                if pending_person is None:
                    next_phase = (
                        "synced"
                        if people and self._person_phase_complete(stored)
                        else ("not_required" if not people else "pending")
                    )
                    if stored.get("personPhaseState") != next_phase:
                        stored["personPhaseState"] = next_phase
                        write_private_json(self.outbox_path, outbox)
                    return self._sync_state(stored, outbox)
                request_body = deepcopy(pending_person.get("requestBody"))
                if not isinstance(request_body, dict):
                    raise IdentityPersistenceError(
                        "A frozen workspace person request could not be verified."
                    )
                client_person_id = str(pending_person.get("clientPersonId") or "")
                project_id = str(stored.get("projectId") or "")

            result_status, result_payload = await remote.create_project_person(
                self.connection_path,
                project_id,
                request_body,
            )
            if not 200 <= result_status < 300:
                with self.identity._lock:
                    outbox = self._read_outbox()
                    current = outbox["entries"].get(safe_entry_id)
                    if isinstance(current, dict):
                        current["attemptCount"] = int(current.get("attemptCount") or 0) + 1
                        current["lastStatus"] = int(result_status)
                        if result_status == 401:
                            current["requiresReconnect"] = True
                        write_private_json(self.outbox_path, outbox)
                        return self._sync_state(current, outbox)
                return initial_state

            if not isinstance(result_payload, dict):
                raise IdentityPersistenceError(
                    "Workspace person sync returned an invalid local reconciliation payload."
                )
            created = (
                result_payload.get("person")
                or result_payload.get("talent")
                or result_payload
            )
            if not isinstance(created, dict):
                raise IdentityPersistenceError(
                    "Workspace person sync returned an invalid local reconciliation payload."
                )
            returned_person_id = str(
                created.get("id")
                or created.get("talentRecordId")
                or created.get("talent_record_id")
                or ""
            )
            returned_person_id = _require_identifier(
                returned_person_id, "canonicalPersonId"
            )
            requested_existing_id = str(request_body.get("talentRecordId") or "")
            if requested_existing_id and returned_person_id != requested_existing_id:
                raise IdentityConflictError(
                    "Workspace person sync returned a different canonical person."
                )

            # Persist the canonical mapping and exact request receipt before any
            # manifest can acknowledge the outbox. A crash after this write is
            # reconciled locally without posting a different request body.
            with self.identity._lock, self.bindings._lock:
                bindings_data = self._read_bindings_strict()
                binding = self.bindings._find(bindings_data, workflow_ref)
                commit_project_id = str(binding.get("project_id") or "")
                if not commit_project_id or commit_project_id != active_project_id:
                    raise IdentityConflictError(
                        "The workflow project changed while its person mapping was syncing. Retry against the current project."
                    )
                drafts = binding.get("person_drafts")
                draft = drafts.get(client_person_id) if isinstance(drafts, dict) else None
                request_hash = _stable_hash(request_body)
                workspace_alias = {
                    "state": "synced",
                    "clientPersonId": client_person_id,
                    "canonicalPersonId": returned_person_id,
                    "requestMode": str(request_body.get("mode") or ""),
                    "requestHash": request_hash,
                }
                if isinstance(draft, dict):
                    existing_mapping = str(draft.get("canonicalPersonId") or "")
                    if existing_mapping and existing_mapping != returned_person_id:
                        raise IdentityConflictError(
                            "The local person is already mapped to a different project person."
                        )
                    draft["canonicalPersonId"] = returned_person_id
                    draft["workspaceAlias"] = _normalize_workspace_alias(
                        workspace_alias,
                        draft_id=client_person_id,
                        canonical_person_id=returned_person_id,
                    )
                    if commit_project_id:
                        draft.setdefault("workspaceAliases", {})[
                            commit_project_id
                        ] = deepcopy(draft["workspaceAlias"])
                else:
                    tombstones = binding.get("person_draft_tombstones")
                    tombstone = (
                        tombstones.get(client_person_id)
                        if isinstance(tombstones, dict)
                        else None
                    )
                    if not isinstance(tombstone, dict):
                        raise IdentityConflictError(
                            "The local person awaiting workspace sync no longer exists."
                        )
                    survivor_id = BindingStore._resolve_person_alias_in_binding(
                        binding, client_person_id
                    )
                    survivor = (
                        drafts.get(survivor_id)
                        if isinstance(drafts, dict)
                        else None
                    )
                    survivor_canonical_id = (
                        str(survivor.get("canonicalPersonId") or "")
                        if isinstance(survivor, dict)
                        else ""
                    )
                    if (
                        not survivor_canonical_id
                        or survivor_canonical_id != returned_person_id
                    ):
                        raise IdentityConflictError(
                            "The merged local alias no longer resolves to this project person."
                        )
                    tombstone["resolvedPersonId"] = returned_person_id
                    tombstone["workspaceAlias"] = _normalize_workspace_alias(
                        workspace_alias,
                        draft_id=client_person_id,
                        canonical_person_id=returned_person_id,
                    )
                    if commit_project_id:
                        tombstone.setdefault("workspaceAliases", {})[
                            commit_project_id
                        ] = deepcopy(tombstone["workspaceAlias"])
                self.bindings._write(bindings_data)

                outbox = self._read_outbox()
                for entry in outbox["entries"].values():
                    if (
                        not isinstance(entry, dict)
                        or entry.get("workflowRef") != workflow_ref
                    ):
                        continue
                    for person_op in entry.get("people", []):
                        if (
                            isinstance(person_op, dict)
                            and person_op.get("clientPersonId") == client_person_id
                            and person_op.get("state") != "superseded"
                            and isinstance(person_op.get("requestBody"), dict)
                            and _stable_hash(person_op["requestBody"])
                            == request_hash
                        ):
                            person_op["canonicalPersonId"] = returned_person_id
                            person_op["state"] = "synced"
                            person_op["requestHash"] = request_hash
                            person_op["syncedAt"] = int(time.time())
                    entry_person = entry.get("person")
                    if (
                        isinstance(entry_person, dict)
                        and entry_person.get("clientPersonId") == client_person_id
                    ):
                        entry_person["canonicalPersonId"] = returned_person_id
                    self._reconcile_outbox_entry_with_binding(entry, binding)
                current = outbox["entries"].get(safe_entry_id)
                if isinstance(current, dict):
                    current["attemptCount"] = int(current.get("attemptCount") or 0) + 1
                    current["personSyncedAt"] = int(time.time())
                    current.pop("lastStatus", None)
                    current.pop("requiresReconnect", None)
                write_private_json(self.outbox_path, outbox)
                # Continue until every person needed by the complete projection
                # has a durable canonical mapping.

    def _normalize_request(self, job_id: str, body: object) -> dict[str, Any]:
        try:
            safe_job_id = str(uuid.UUID(str(job_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid identity job identifier.") from exc
        if not isinstance(body, dict):
            raise ValueError("Request body must be an object.")
        base_revision = body.get("baseRevision", body.get("base_revision"))
        if (
            isinstance(base_revision, bool)
            or not isinstance(base_revision, int)
            or base_revision < 0
        ):
            raise ValueError("baseRevision must be a non-negative integer.")
        candidate_id = str(body.get("candidateId", body.get("candidate_id")) or "")
        if not SAFE_ID.fullmatch(candidate_id):
            raise ValueError("candidateId must be an opaque identifier.")
        decision = _DECISION_ALIASES.get(str(body.get("decision") or ""))
        if not decision:
            raise ValueError("decision is not supported.")
        action = str(body.get("action") or "assign")
        if action not in _ACTIONS:
            raise ValueError("action is not supported.")
        if action == "combine" and decision != "confirmed":
            raise ValueError("Only a confirmed person decision can combine identities.")

        selected = body.get("occurrenceIds", body.get("occurrence_ids", []))
        if not isinstance(selected, list):
            raise ValueError("occurrenceIds must be a list.")
        if len(selected) > MAX_OCCURRENCES:
            raise ValueError("occurrenceIds may contain at most 2000 entries.")
        occurrence_ids = sorted({str(value) for value in selected if str(value)})
        if any(not SAFE_ID.fullmatch(value) for value in occurrence_ids):
            raise ValueError("Every occurrenceId must be an opaque identifier.")

        raw_source_refs = body.get("sourceRefs", body.get("source_refs"))
        source_refs = None
        if raw_source_refs is not None:
            if not isinstance(raw_source_refs, list):
                raise ValueError("sourceRefs must be a list.")
            if len(raw_source_refs) > 500:
                raise ValueError("sourceRefs may contain at most 500 entries.")
            source_refs = sorted(
                {str(value) for value in raw_source_refs if str(value)}
            )
            if any(not SAFE_ID.fullmatch(value) for value in source_refs):
                raise ValueError("Every sourceRef must be an opaque identifier.")

        raw_target = body.get("target")
        if raw_target is None:
            raw_target = {}
        if not isinstance(raw_target, dict):
            raise ValueError("target must be an object.")
        target = {
            field: deepcopy(raw_target[field])
            for field in _TARGET_FIELDS
            if field in raw_target and raw_target[field] not in (None, "")
        }
        if decision == "confirmed" and not target:
            raise ValueError("A confirmed decision requires a target person.")
        if target.get("draftId"):
            target["draftId"] = _require_uuid(target["draftId"], "target.draftId")
        if target.get("canonicalPersonId"):
            target["canonicalPersonId"] = _require_identifier(
                target["canonicalPersonId"], "target.canonicalPersonId"
            )

        raw_merge_ids = body.get("mergeDraftIds", body.get("merge_draft_ids", []))
        if not isinstance(raw_merge_ids, list):
            raise ValueError("mergeDraftIds must be a list.")
        if len(raw_merge_ids) > MAX_MERGE_DRAFTS:
            raise ValueError("mergeDraftIds may contain at most 100 entries.")
        merge_ids = sorted(
            {_require_uuid(value, "mergeDraftId") for value in raw_merge_ids}
        )
        if action == "combine" and not merge_ids:
            raise ValueError("combine requires at least one mergeDraftId.")
        if action != "combine" and merge_ids:
            raise ValueError("mergeDraftIds require action=combine.")
        return {
            "schemaVersion": DECISION_SCHEMA_VERSION,
            "jobId": safe_job_id,
            "baseRevision": base_revision,
            "candidateId": candidate_id,
            "decision": decision,
            "occurrenceIds": occurrence_ids,
            "sourceRefs": source_refs,
            "action": action,
            "target": target,
            "mergeDraftIds": merge_ids,
        }

    def _commit_locked(
        self,
        request: dict[str, Any],
        *,
        transaction_id: str,
        transaction_path: str,
    ) -> dict[str, Any]:
        job = self.identity._get_job_record(request["jobId"])
        if job.get("state") != "completed":
            raise ValueError("Links can be saved only for a completed analysis job.")
        self.identity._require_current_workflow_job(job)
        workflow_ref = str(job.get("workflowRef") or "")
        if not workflow_ref:
            raise ValueError("Identity decisions require a workflow-scoped analysis job.")

        bindings_before = self._read_bindings_strict()
        binding_before = self.bindings._find(bindings_before, workflow_ref)
        bindings_after = deepcopy(bindings_before)
        binding_after = self.bindings._find(bindings_after, workflow_ref)
        cached = self.identity._read_cache(str(job.get("cacheKey") or "")) or {}
        candidate_occurrences, candidate_sources = _candidate_indexes(cached)
        candidate_id = request["candidateId"]
        if candidate_id not in candidate_occurrences:
            raise ValueError("candidateId was not minted for this job.")
        selected = set(request["occurrenceIds"])
        if not selected <= set(candidate_occurrences[candidate_id]):
            raise ValueError(
                "Every occurrenceId must belong to the selected candidate in this job."
            )
        requested_source_refs = request["sourceRefs"]
        if candidate_occurrences[candidate_id]:
            if requested_source_refs is not None:
                raise ValueError(
                    "sourceRefs may be selected only for a candidate without appearances."
                )
            selected_source_refs: set[str] = set()
        else:
            selected_source_refs = set(
                candidate_sources[candidate_id]
                if requested_source_refs is None
                else requested_source_refs
            )
            if not selected_source_refs <= set(candidate_sources[candidate_id]):
                raise ValueError(
                    "Every sourceRef must belong to the selected candidate in this job."
                )
            if candidate_sources[candidate_id] and not selected_source_refs:
                raise ValueError(
                    "A source-only identity decision must select at least one source."
                )
        if request["decision"] == "confirmed" and candidate_occurrences[candidate_id] and not selected:
            raise ValueError("A confirmed person decision must select at least one occurrence.")
        if request["decision"] != "confirmed" and not selected:
            selected = set(candidate_occurrences[candidate_id])

        current = self.identity.get_links(request["jobId"])
        if current["revision"] != request["baseRevision"]:
            raise IdentityConflictError(
                "Identity link revision conflict. Reload the current links before saving."
            )
        active_drafts = binding_after.get("person_drafts")
        if not isinstance(active_drafts, dict):
            active_drafts = {}
            binding_after["person_drafts"] = active_drafts
        tombstones = binding_after.get("person_draft_tombstones")
        if not isinstance(tombstones, dict):
            tombstones = {}
            binding_after["person_draft_tombstones"] = tombstones

        target_draft_id = ""
        target_person_id = ""
        target_display_name = ""
        target = request["target"]
        if request["decision"] == "confirmed":
            target_draft_id = str(target.get("draftId") or "")
            if target_draft_id:
                target_draft_id = BindingStore._resolve_person_alias_in_binding(
                    binding_after, target_draft_id
                )
            else:
                target_draft_id = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"https://trypluribus.com/local-person/{transaction_id}",
                    )
                )
            existing_target = active_drafts.get(target_draft_id)
            if not isinstance(existing_target, dict):
                existing_target = {}
            supplied_canonical = str(target.get("canonicalPersonId") or "")
            existing_canonical = str(existing_target.get("canonicalPersonId") or "")
            if supplied_canonical and existing_canonical and supplied_canonical != existing_canonical:
                raise ValueError("target.canonicalPersonId cannot replace an existing mapping.")

            merge_drafts: list[dict[str, Any]] = []
            for merge_id in request["mergeDraftIds"]:
                resolved_merge_id = BindingStore._resolve_person_alias_in_binding(
                    binding_after, merge_id
                )
                if resolved_merge_id == target_draft_id:
                    continue
                merge_value = active_drafts.get(resolved_merge_id)
                if not isinstance(merge_value, dict):
                    raise ValueError("Every mergeDraftId must name an active local draft.")
                merge_drafts.append(merge_value)
            canonical_ids = {
                str(value.get("canonicalPersonId"))
                for value in [existing_target, *merge_drafts]
                if value.get("canonicalPersonId")
            }
            if supplied_canonical:
                canonical_ids.add(supplied_canonical)
            if len(canonical_ids) > 1:
                raise ValueError(
                    "Local aliases mapped to different project people cannot be combined."
                )
            canonical_person_id = next(iter(canonical_ids), "")

            merged_target = {
                **deepcopy(existing_target),
                **deepcopy(target),
                "draftId": target_draft_id,
                "sourceRefs": list(existing_target.get("sourceRefs") or []),
            }
            if canonical_person_id:
                merged_target["canonicalPersonId"] = canonical_person_id
            if not str(merged_target.get("displayName") or "").strip():
                raise ValueError("target.displayName is required for a new person.")
            target_display_name = str(merged_target.get("displayName") or "")
            active_drafts[target_draft_id] = merged_target
            target_person_id = canonical_person_id or target_draft_id

            if request["action"] == "combine":
                for merge_id in request["mergeDraftIds"]:
                    resolved_merge_id = BindingStore._resolve_person_alias_in_binding(
                        binding_after, merge_id
                    )
                    if resolved_merge_id == target_draft_id:
                        continue
                    alias_value = active_drafts.pop(resolved_merge_id, None)
                    if not isinstance(alias_value, dict):
                        continue
                    alias_history = _normalize_workspace_alias_history(
                        alias_value.get("workspaceAliases"),
                        draft_id=resolved_merge_id,
                    )
                    tombstones[resolved_merge_id] = {
                        "draftId": resolved_merge_id,
                        "mergedIntoDraftId": target_draft_id,
                        "resolvedPersonId": target_person_id,
                        "mergedAt": int(time.time()),
                        **(
                            {"workspaceAlias": deepcopy(alias_value["workspaceAlias"])}
                            if alias_value.get("workspaceAlias")
                            else {}
                        ),
                        **(
                            {"workspaceAliases": alias_history}
                            if alias_history
                            else {}
                        ),
                    }

        aliases = _alias_map(active_drafts, tombstones)
        if target_draft_id:
            aliases[target_draft_id] = target_person_id
            for merge_id in request["mergeDraftIds"]:
                aliases[merge_id] = target_person_id
        links = self._canonicalized_expanded_links(current["links"], cached, aliases)
        links = self._apply_candidate_decision(
            links,
            candidate_id=candidate_id,
            decision=request["decision"],
            selected=selected,
            selected_source_refs=selected_source_refs,
            target_person_id=target_person_id,
            display_name=target_display_name,
            # Assign replaces this person's selection only in the edited
            # candidate. Combine has already canonicalized every merged alias,
            # so its appearances must be unioned with the checked selection
            # rather than discarded from a same-candidate survivor link.
            preserve_target=request["action"] == "combine",
        )
        links_path, links_document, link_response = self.identity._prepare_links_locked(
            request["jobId"],
            {"baseRevision": request["baseRevision"], "links": links},
        )

        projection_aliases = _alias_map(active_drafts, tombstones)
        scoped_projection_links = _project_scoped_links(
            link_response["links"], binding_after
        )
        projected = person_source_projection(
            scoped_projection_links, cached, projection_aliases
        )
        merged_manual_refs: set[str] = set()
        if target_draft_id:
            before_drafts = binding_before.get("person_drafts")
            if not isinstance(before_drafts, dict):
                before_drafts = {}
            for merge_id in [target_draft_id, *request["mergeDraftIds"]]:
                prior = before_drafts.get(merge_id)
                if isinstance(prior, dict):
                    merged_manual_refs.update(
                        set(
                            prior.get(
                                "manualSourceRefs",
                                prior.get("sourceRefs") or [],
                            )
                        )
                    )
        known_source_refs = set(binding_after.get("source_refs", {}).values())
        for draft_id, draft in list(active_drafts.items()):
            if not isinstance(draft, dict):
                raise IdentityPersistenceError("A local person draft could not be verified.")
            effective_person_id = _resolve_person_id(draft_id, projection_aliases)
            manual_refs = set(
                draft.get("manualSourceRefs", draft.get("sourceRefs") or [])
            )
            if draft_id == target_draft_id:
                manual_refs.update(merged_manual_refs)
            next_refs = sorted(manual_refs | set(projected.get(effective_person_id, [])))
            active_drafts[draft_id] = _normalize_person_draft(
                {
                    **draft,
                    "draftId": draft_id,
                    "sourceRefs": next_refs,
                    "manualSourceRefs": sorted(manual_refs),
                },
                known_source_refs,
                allow_empty_source_refs=True,
                allow_workspace_alias=True,
                allow_workspace_alias_history=True,
                allow_manual_source_refs=True,
            )
        for value in tombstones.values():
            _normalize_person_tombstone(value)

        outbox_before = self._read_outbox()
        outbox_after = deepcopy(outbox_before)
        self._supersede_tombstoned_operations(outbox_after, binding_after)
        entry_id = _stable_hash(
            {
                "workflowRef": workflow_ref,
                "revision": link_response["revision"],
                "transactionId": transaction_id,
            }
        )[:40]
        entry = self._outbox_entry(
            entry_id=entry_id,
            transaction_id=transaction_id,
            workflow_ref=workflow_ref,
            project_id=str(binding_after.get("project_id") or ""),
            job_id=request["jobId"],
            revision=link_response["revision"],
            target_draft_id=target_draft_id,
            active_drafts=active_drafts,
            tombstones=tombstones,
            links=link_response["links"],
            cached=cached,
            binding=binding_after,
        )
        outbox_after["entries"][entry_id] = entry

        links_before = _read_json_strict(
            links_path,
            {
                "schemaVersion": 3,
                "analysisJobId": request["jobId"],
                "analysisCacheKey": job.get("cacheKey"),
                "revision": 0,
                "links": [],
            },
        )
        person_drafts = self._public_drafts(binding_after)
        sync_details = self._sync_state(entry, outbox_after)
        response = {
            "jobId": request["jobId"],
            "workflowRef": workflow_ref,
            "revision": link_response["revision"],
            "identityRevision": link_response["revision"],
            "identityReviewHash": entry["identityReviewHash"],
            "links": link_response["links"],
            "personDrafts": person_drafts,
            "syncState": sync_details["state"],
            "syncDetails": sync_details,
        }
        journal = {
            "schemaVersion": DECISION_SCHEMA_VERSION,
            "transactionId": transaction_id,
            "requestHash": _stable_hash(request),
            "state": "prepared",
            "createdAt": int(time.time()),
            "paths": {
                "links": links_path,
                "bindings": self.bindings.path,
                "outbox": self.outbox_path,
            },
            "before": {
                "links": links_before,
                "bindings": bindings_before,
                "outbox": outbox_before,
            },
            "after": {
                "links": links_document,
                "bindings": bindings_after,
                "outbox": outbox_after,
            },
            "response": response,
        }
        write_private_json(transaction_path, journal)
        try:
            write_private_json(links_path, links_document)
            self.bindings._write(bindings_after)
            write_private_json(self.outbox_path, outbox_after)
            journal["state"] = "committed"
            journal["committedAt"] = int(time.time())
            write_private_json(transaction_path, journal)
        except BaseException:
            try:
                self._restore_snapshot_locked(journal["paths"], journal["before"])
            except BaseException as recovery_error:
                # Leave the prepared WAL in place; startup recovery will finish
                # the rollback before another identity decision is accepted.
                self._poisoned_error = IdentityPersistenceError(
                    "An interrupted identity decision could not be recovered. "
                    "Restart ComfyUI before saving more review work."
                )
                self._poisoned_error.__cause__ = recovery_error
            raise
        return response

    @staticmethod
    def _canonicalized_expanded_links(
        links: list[dict[str, Any]],
        cached: object,
        aliases: dict[str, str],
    ) -> list[dict[str, Any]]:
        candidate_occurrences, candidate_sources = _candidate_indexes(cached)
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for link in links:
            candidate_id = str(link.get("candidateId", link.get("candidate_id")) or "")
            person_id = _resolve_person_id(
                link.get("personId", link.get("person_id")), aliases
            )
            state = str(link.get("state") or "confirmed")
            key = (candidate_id, person_id, state)
            selected = link.get("occurrenceIds", link.get("occurrence_ids"))
            occurrences = set(
                str(value)
                for value in (
                    selected
                    if isinstance(selected, list) and selected
                    else candidate_occurrences.get(candidate_id, [])
                )
            )
            selected_sources = link.get("sourceRefs", link.get("source_refs"))
            source_refs = set(
                str(value)
                for value in (
                    selected_sources
                    if isinstance(selected_sources, list) and selected_sources
                    else (
                        candidate_sources.get(candidate_id, [])
                        if not candidate_occurrences.get(candidate_id)
                        else []
                    )
                )
            )
            value = grouped.setdefault(
                key,
                {
                    "candidateId": candidate_id,
                    **({"personId": person_id} if person_id else {}),
                    "state": state,
                    "displayName": str(link.get("displayName") or "")[:160],
                    "occurrenceIds": set(),
                    "sourceRefs": set(),
                },
            )
            value["occurrenceIds"].update(occurrences)
            value["sourceRefs"].update(source_refs)
        result = []
        for value in grouped.values():
            occurrences = sorted(value.pop("occurrenceIds"))
            source_refs = sorted(value.pop("sourceRefs"))
            if occurrences:
                value["occurrenceIds"] = occurrences
            if source_refs:
                value["sourceRefs"] = source_refs
            result.append(value)
        return result

    @staticmethod
    def _apply_candidate_decision(
        links: list[dict[str, Any]],
        *,
        candidate_id: str,
        decision: str,
        selected: set[str],
        selected_source_refs: set[str],
        target_person_id: str,
        display_name: str,
        preserve_target: bool,
    ) -> list[dict[str, Any]]:
        retained: list[dict[str, Any]] = []
        preserved_target_occurrences: set[str] = set()
        preserved_target_source_refs: set[str] = set()
        for link in links:
            if str(link.get("candidateId") or "") != candidate_id:
                retained.append(link)
                continue
            linked_person_id = str(link.get("personId") or "")
            state = str(link.get("state") or "confirmed")
            occurrences = set(str(value) for value in link.get("occurrenceIds", []))
            source_refs = set(str(value) for value in link.get("sourceRefs", []))
            if decision == "confirmed" and linked_person_id == target_person_id and state == "confirmed":
                if preserve_target:
                    preserved_target_occurrences.update(occurrences)
                    preserved_target_source_refs.update(source_refs)
                continue
            if decision == state and not linked_person_id and decision != "confirmed":
                # Re-reviewing false-positive or unsure evidence replaces that
                # decision's exact selection; unchecked evidence becomes open
                # review again instead of producing a duplicate link.
                continue
            if occurrences:
                remaining = occurrences - selected
                if remaining:
                    retained.append({**link, "occurrenceIds": sorted(remaining)})
            elif source_refs:
                remaining_sources = source_refs - selected_source_refs
                if remaining_sources:
                    retained.append({**link, "sourceRefs": sorted(remaining_sources)})
        if decision == "confirmed":
            confirmed = sorted(selected | preserved_target_occurrences)
            confirmed_sources = sorted(
                selected_source_refs | preserved_target_source_refs
            )
            retained.append(
                {
                    "candidateId": candidate_id,
                    "personId": target_person_id,
                    "state": "confirmed",
                    "displayName": display_name[:160],
                    **({"occurrenceIds": confirmed} if confirmed else {}),
                    **({"sourceRefs": confirmed_sources} if confirmed_sources else {}),
                }
            )
        elif decision == "unsure":
            retained.append(
                {
                    "candidateId": candidate_id,
                    "state": "unsure",
                    "displayName": display_name[:160],
                    **({"occurrenceIds": sorted(selected)} if selected else {}),
                    **(
                        {"sourceRefs": sorted(selected_source_refs)}
                        if selected_source_refs
                        else {}
                    ),
                }
            )
        else:
            retained.append(
                {
                    "candidateId": candidate_id,
                    "state": "rejected",
                    "displayName": "False detection",
                    **({"occurrenceIds": sorted(selected)} if selected else {}),
                    **(
                        {"sourceRefs": sorted(selected_source_refs)}
                        if selected_source_refs
                        else {}
                    ),
                }
            )
        return retained

    def _outbox_entry(
        self,
        *,
        entry_id: str,
        transaction_id: str,
        workflow_ref: str,
        project_id: str,
        job_id: str,
        revision: int,
        target_draft_id: str,
        active_drafts: dict[str, Any],
        tombstones: dict[str, Any],
        links: list[dict[str, Any]],
        cached: object,
        binding: dict[str, Any],
    ) -> dict[str, Any]:
        aliases = _alias_map(active_drafts, tombstones)
        scoped_links = _project_scoped_links(links, binding)
        source_people = source_person_projection(
            scoped_links, cached, aliases, active_drafts
        )
        projected_person_ids = {
            person_id
            for person_ids in source_people.values()
            for person_id in person_ids
        }
        local_person_ids = {
            str(value) for value in [*active_drafts.keys(), *tombstones.keys()]
        }
        target = active_drafts.get(target_draft_id) if target_draft_id else None
        person = None
        if isinstance(target, dict):
            person = {
                key: deepcopy(value)
                for key, value in target.items()
                if key
                in {
                    "draftId",
                    "canonicalPersonId",
                    "displayName",
                    "role",
                    "talentEmail",
                    "representative",
                    "notes",
                    "sourceRefs",
                }
            }
            person["clientPersonId"] = target["draftId"]
        people = []
        for draft_id, draft in sorted(active_drafts.items()):
            if not isinstance(draft, dict):
                continue
            effective_person_id = _resolve_person_id(draft_id, aliases)
            if effective_person_id not in projected_person_ids:
                continue
            canonical_person_id = str(draft.get("canonicalPersonId") or "")
            marker = (
                _normalize_workspace_alias(
                    draft.get("workspaceAlias"),
                    draft_id=draft_id,
                    canonical_person_id=canonical_person_id,
                )
                if draft.get("workspaceAlias")
                else None
            )
            operation: dict[str, Any] = {
                "operationKind": "person",
                "clientPersonId": draft_id,
                "draftId": draft_id,
                "canonicalPersonId": canonical_person_id or None,
            }
            if marker:
                operation.update(
                    {
                        "state": "synced",
                        "canonicalPersonId": marker["canonicalPersonId"],
                        "requestHash": marker["requestHash"],
                    }
                )
            else:
                request_body: dict[str, Any]
                if canonical_person_id:
                    request_body = {
                        "mode": "existing",
                        "clientPersonId": draft_id,
                        "talentRecordId": canonical_person_id,
                    }
                else:
                    request_body = {
                        "mode": "new",
                        "clientPersonId": draft_id,
                        "displayName": str(draft.get("displayName") or ""),
                    }
                    for field in ("role", "talentEmail", "representative"):
                        if draft.get(field) not in (None, ""):
                            request_body[field] = deepcopy(draft[field])
                operation.update(
                    {
                        "state": "pending",
                        # Freeze the exact hosted material. A successful mode:new
                        # response may be lost after the canonical mapping is saved;
                        # retry must remain byte/material-equivalent for idempotency.
                        "requestBody": request_body,
                        "requestHash": _stable_hash(request_body),
                    }
                )
            people.append(operation)

        for alias_id, raw_tombstone in sorted(tombstones.items()):
            tombstone = _normalize_person_tombstone(raw_tombstone)
            effective_person_id = _resolve_person_id(alias_id, aliases)
            if effective_person_id not in projected_person_ids:
                continue
            marker = tombstone.get("workspaceAlias")
            alias_operation: dict[str, Any] = {
                "operationKind": "merge_alias",
                "clientPersonId": alias_id,
                "draftId": alias_id,
                "mergedIntoDraftId": tombstone["mergedIntoDraftId"],
                "canonicalPersonId": None,
            }
            if isinstance(marker, dict):
                alias_operation.update(
                    {
                        "state": "synced",
                        "canonicalPersonId": marker["canonicalPersonId"],
                        "requestHash": marker["requestHash"],
                    }
                )
            elif effective_person_id not in local_person_ids:
                request_body = {
                    "mode": "existing",
                    "clientPersonId": alias_id,
                    "talentRecordId": effective_person_id,
                }
                alias_operation.update(
                    {
                        "state": "pending",
                        "canonicalPersonId": effective_person_id,
                        "requestBody": request_body,
                        "requestHash": _stable_hash(request_body),
                    }
                )
            else:
                alias_operation["state"] = "waiting_for_survivor"
            people.append(alias_operation)

        result = {
            "entryId": entry_id,
            "transactionId": transaction_id,
            "state": "pending",
            "createdAt": int(time.time()),
            "workflowRef": workflow_ref,
            "projectId": project_id or None,
            "jobId": job_id,
            "revision": revision,
            "clientPersonId": target_draft_id or None,
            "draftId": target_draft_id or None,
            "person": person,
            "people": people,
            "personPhaseState": (
                "synced"
                if people
                and all(value.get("state") == "synced" for value in people)
                and not (
                    projected_person_ids & local_person_ids
                )
                else ("pending" if people else "not_required")
            ),
            "localPersonIds": sorted(local_person_ids),
            "identityReviewHash": identity_review_hash(links),
            "sourcePeople": [
                {"sourceRef": source_ref, "personIds": person_ids}
                for source_ref, person_ids in source_people.items()
            ],
            "mergeAliases": [
                _normalize_person_tombstone(value)
                for value in sorted(
                    tombstones.values(), key=lambda item: str(item.get("draftId") or "")
                )
            ],
        }
        return result

    def _sync_state(
        self, entry: dict[str, Any], outbox: dict[str, Any]
    ) -> dict[str, Any]:
        if entry.get("state") == "synced":
            state = "synced"
        elif not entry.get("projectId"):
            state = "saved_local"
        elif entry.get("requiresReconnect") or not remote.read_connection(
            self.connection_path
        ):
            state = "reconnect_required"
        else:
            state = "sync_pending"
        pending_count = sum(
            1
            for value in outbox.get("entries", {}).values()
            if isinstance(value, dict) and value.get("state") != "synced"
        )
        return {
            "state": state,
            "entryId": entry.get("entryId"),
            "pendingCount": pending_count,
            "workflowRef": entry.get("workflowRef"),
            "projectId": entry.get("projectId"),
            "revision": entry.get("revision"),
            "clientPersonId": entry.get("clientPersonId"),
            "identityReviewHash": entry.get("identityReviewHash"),
            "personPhaseState": entry.get("personPhaseState"),
        }

    def _read_bindings_strict(self) -> dict[str, Any]:
        value = _read_json_strict(
            self.bindings.path,
            {"version": BINDINGS_SCHEMA_VERSION, "workflows": {}},
        )
        if (
            value.get("version") != BINDINGS_SCHEMA_VERSION
            or not isinstance(value.get("workflows"), dict)
        ):
            raise IdentityPersistenceError(
                "Private workflow bindings could not be verified. Restart ComfyUI "
                "before saving another identity decision."
            )
        return value

    def _read_outbox(self) -> dict[str, Any]:
        value = _read_json_strict(
            self.outbox_path,
            {"schemaVersion": OUTBOX_SCHEMA_VERSION, "entries": {}},
        )
        if value.get("schemaVersion") != OUTBOX_SCHEMA_VERSION:
            raise IdentityPersistenceError("The identity sync outbox version is not supported.")
        entries = value.get("entries")
        if not isinstance(entries, dict):
            raise IdentityPersistenceError("The identity sync outbox could not be verified.")
        return value

    @staticmethod
    def _public_drafts(binding: dict[str, Any]) -> list[dict[str, Any]]:
        known_source_refs = set(binding.get("source_refs", {}).values())
        drafts = binding.get("person_drafts")
        if not isinstance(drafts, dict):
            return []
        result = []
        for value in drafts.values():
            normalized = _normalize_person_draft(
                value,
                known_source_refs,
                allow_empty_source_refs=True,
                allow_workspace_alias=True,
                allow_manual_source_refs=True,
            )
            normalized.pop("manualSourceRefs", None)
            result.append(normalized)
        return sorted(result, key=lambda value: value["draftId"])

    def _transaction_path(self, transaction_id: str) -> str:
        return os.path.join(self.transactions_dir, f"{transaction_id}.json")

    def _committed_replay(
        self, path: str, request: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not os.path.isfile(path):
            return None
        journal = _read_json_strict(path, {})
        if journal.get("requestHash") != _stable_hash(request):
            raise IdentityConflictError("Identity decision receipt does not match this request.")
        if journal.get("state") != "committed":
            return None
        response = deepcopy(journal.get("response"))
        if not isinstance(response, dict):
            raise IdentityPersistenceError("Identity decision receipt could not be verified.")
        current = self.identity.get_links(request["jobId"])
        if current["revision"] != response.get("revision"):
            raise IdentityConflictError(
                "This identity decision was already saved, but newer review work exists. "
                "Reload People before continuing."
            )
        outbox = self._read_outbox()
        entry_id = response.get("syncDetails", {}).get("entryId")
        entry = outbox["entries"].get(entry_id)
        if isinstance(entry, dict):
            sync_details = self._sync_state(entry, outbox)
            response["syncState"] = sync_details["state"]
            response["syncDetails"] = sync_details
        response["personDrafts"] = self.bindings.list_person_drafts(
            response["syncDetails"]["workflowRef"]
        )
        return response

    def _recover_prepared_transactions_locked(self) -> None:
        for filename in sorted(os.listdir(self.transactions_dir)):
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self.transactions_dir, filename)
            journal = _read_json_strict(path, {})
            state = journal.get("state")
            if state == "prepared":
                paths = journal.get("paths")
                before = journal.get("before")
                if not isinstance(paths, dict) or not isinstance(before, dict):
                    raise IdentityPersistenceError(
                        "An interrupted identity decision could not be recovered."
                    )
                self._restore_snapshot_locked(paths, before)
                journal["state"] = "rolled_back"
                journal["rolledBackAt"] = int(time.time())
                write_private_json(path, journal)
            elif state not in {"committed", "rolled_back"}:
                raise IdentityPersistenceError(
                    "An identity decision journal could not be verified."
                )

    def _restore_snapshot_locked(
        self, paths: dict[str, Any], snapshot: dict[str, Any]
    ) -> None:
        expected = {
            "bindings": os.path.realpath(self.bindings.path),
            "outbox": os.path.realpath(self.outbox_path),
        }
        links_path = str(paths.get("links") or "")
        if os.path.commonpath(
            [os.path.realpath(links_path), os.path.realpath(self.identity.links_dir)]
        ) != os.path.realpath(self.identity.links_dir):
            raise IdentityPersistenceError("Identity decision journal named an unsafe link path.")
        if os.path.realpath(str(paths.get("bindings") or "")) != expected["bindings"]:
            raise IdentityPersistenceError("Identity decision journal named an unsafe binding path.")
        if os.path.realpath(str(paths.get("outbox") or "")) != expected["outbox"]:
            raise IdentityPersistenceError("Identity decision journal named an unsafe outbox path.")
        for key in ("links", "bindings", "outbox"):
            if not isinstance(snapshot.get(key), dict):
                raise IdentityPersistenceError("Identity decision snapshot could not be verified.")
        write_private_json(links_path, snapshot["links"])
        self.bindings._write(snapshot["bindings"])
        write_private_json(self.outbox_path, snapshot["outbox"])
