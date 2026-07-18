"""Private local bindings between ComfyUI sources and Pluribus projects.

Raw ComfyUI workflow names, source paths, prompts, and node ids never belong in
the Pluribus API.  This module keeps the local lookup material private and
mints random public identifiers for the small rights manifest that is sent to
the connected account.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from copy import deepcopy
from typing import Any

from .storage import write_private_json

SCHEMA_VERSION = 1
WORKFLOW_KINDS = {"character_sheet", "storyboard", "production", "final", "other"}
SOURCE_KINDS = {"reference", "audio", "lora", "prompt", "unknown"}
SOURCE_DISPOSITIONS = {"linked", "not_person", "review_required"}
LOCAL_SOURCE_REVIEW_STATES = {"not_person", "review_required"}
REPRESENTATIVE_ROLES = {
    "talent",
    "manager",
    "agent",
    "attorney",
    "guardian",
    "rights_holder",
    "other",
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
CLASS_TYPE_PATTERN = re.compile(r"[^A-Za-z0-9_.:-]+")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _random_sha256() -> str:
    return hashlib.sha256(os.urandom(32)).hexdigest()


def _require_sha256(value: object, field: str) -> str:
    normalized = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be a SHA-256 hex digest.")
    return normalized


def _require_uuid(value: object, field: str) -> str:
    try:
        parsed = uuid.UUID(str(value or ""))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{field} must be a UUID.") from exc
    return str(parsed)


def normalize_class_type(value: object) -> str:
    """Return a bounded operation class label with no graph-local metadata."""
    normalized = CLASS_TYPE_PATTERN.sub("", str(value or "").strip())[:120]
    if not normalized:
        raise ValueError("Each operation must have a classType.")
    return normalized


def _require_identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be an opaque identifier.")
    return normalized


def _optional_string(
    value: object,
    field: str,
    max_length: int,
) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string.")
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters.")
    return normalized


def _optional_email(value: object, field: str) -> str | None:
    normalized = _optional_string(value, field, 320)
    if normalized and not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError(f"{field} must be a valid email address.")
    return normalized


def _normalize_person_draft(
    body: object,
    known_source_refs: set[str],
) -> dict[str, Any]:
    """Allow-list one local person draft without accepting graph metadata."""
    if not isinstance(body, dict):
        raise ValueError("Person draft must be an object.")

    result: dict[str, Any] = {}
    if body.get("draftId") not in (None, ""):
        result["draftId"] = _require_uuid(body.get("draftId"), "draftId")
    if body.get("canonicalPersonId") not in (None, ""):
        result["canonicalPersonId"] = _require_identifier(
            body.get("canonicalPersonId"), "canonicalPersonId"
        )

    for field, limit in (("displayName", 160), ("role", 120), ("notes", 3000)):
        value = _optional_string(body.get(field), field, limit)
        if value:
            result[field] = value

    talent_email = _optional_email(body.get("talentEmail"), "talentEmail")
    if talent_email:
        result["talentEmail"] = talent_email

    representative = body.get("representative")
    if representative not in (None, ""):
        if not isinstance(representative, dict):
            raise ValueError("representative must be an object.")
        representative_role = str(representative.get("role") or "manager")
        if representative_role not in REPRESENTATIVE_ROLES:
            raise ValueError("representative.role is not supported.")
        safe_representative: dict[str, str] = {"role": representative_role}
        representative_name = _optional_string(
            representative.get("name"), "representative.name", 160
        )
        representative_email = _optional_email(
            representative.get("email"), "representative.email"
        )
        if representative_name:
            safe_representative["name"] = representative_name
        if representative_email:
            safe_representative["email"] = representative_email
        result["representative"] = safe_representative

    source_refs = body.get("sourceRefs")
    if not isinstance(source_refs, list) or not source_refs:
        raise ValueError("sourceRefs must be a non-empty list.")
    if len(source_refs) > 100:
        raise ValueError("sourceRefs may contain at most 100 entries.")
    safe_source_refs = sorted(
        {_require_sha256(source_ref, "sourceRef") for source_ref in source_refs}
    )
    for source_ref in safe_source_refs:
        if source_ref not in known_source_refs:
            raise ValueError("sourceRef was not minted for this workflow.")
    result["sourceRefs"] = safe_source_refs
    return result


def normalize_source_links(
    *,
    workflow_ref: object,
    workflow_kind: object,
    graph_hash: object | None,
    sources: object,
) -> dict[str, Any]:
    """Build the exact safe, rights-relevant source-link payload.

    Input objects are reconstructed from an allow-list.  In particular, node
    ids, source paths/keys, prompts, provenance, and full graphs cannot pass
    through this function even if a caller includes them.
    """
    safe_workflow_ref = _require_uuid(workflow_ref, "workflowRef")
    safe_workflow_kind = str(workflow_kind or "other")
    if safe_workflow_kind not in WORKFLOW_KINDS:
        raise ValueError("workflowKind is not supported.")
    if not isinstance(sources, list):
        raise ValueError("sources must be a list.")
    if len(sources) > 500:
        raise ValueError("sources may contain at most 500 entries.")

    safe_sources: list[dict[str, Any]] = []
    seen_source_refs: set[str] = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each source must be an object.")
        source_kind = str(source.get("sourceKind") or source.get("source_kind") or "unknown")
        if source_kind not in SOURCE_KINDS:
            raise ValueError("sourceKind is not supported.")
        disposition = str(source.get("disposition") or "review_required")
        if disposition not in SOURCE_DISPOSITIONS:
            raise ValueError("disposition is not supported.")

        talent_ids = source.get("talentRecordIds", source.get("talent_record_ids", []))
        if not isinstance(talent_ids, list):
            raise ValueError("talentRecordIds must be a list.")
        if len(talent_ids) > 100:
            raise ValueError("talentRecordIds may contain at most 100 entries.")
        safe_talent_ids = sorted(
            {
                _require_identifier(talent_id, "talentRecordId").lower()
                for talent_id in talent_ids
            }
        )
        if disposition == "linked" and not safe_talent_ids:
            raise ValueError("A linked source must name at least one project person.")
        if disposition != "linked" and safe_talent_ids:
            raise ValueError("Only linked sources may name project people.")

        operations = source.get("operations", [])
        if not isinstance(operations, list):
            raise ValueError("operations must be a list.")
        if len(operations) > 100:
            raise ValueError("operations may contain at most 100 entries.")
        safe_operations = []
        seen_operations: set[str] = set()
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError("Each operation must be an object.")
            class_type = normalize_class_type(
                operation.get("classType", operation.get("class_type"))
            )
            source_role = str(
                operation.get("sourceRole", operation.get("source_role")) or ""
            )
            if source_role not in {
                "",
                "reference_audio",
                "reference_image",
                "reference_video",
            }:
                raise ValueError("operation.sourceRole is not supported.")
            operation_key = f"{class_type}|{source_role}"
            if operation_key in seen_operations:
                continue
            seen_operations.add(operation_key)
            safe_operation: dict[str, str] = {"classType": class_type}
            if source_role:
                safe_operation["sourceRole"] = source_role
            safe_operations.append(safe_operation)
        safe_operations.sort(
            key=lambda item: f"{item['classType']}|{item.get('sourceRole', '')}"
        )

        source_ref = _require_sha256(
            source.get("sourceRef", source.get("source_ref")), "sourceRef"
        )
        if source_ref in seen_source_refs:
            raise ValueError("Each sourceRef may appear only once.")
        seen_source_refs.add(source_ref)
        safe_source: dict[str, Any] = {
            "sourceRef": source_ref,
            "sourceKind": source_kind,
            "disposition": disposition,
            "talentRecordIds": safe_talent_ids,
            "operations": safe_operations,
        }
        # displayLabel is optional and must be supplied deliberately by the UI;
        # it is never derived here from a filename, path, or prompt.
        display_label = str(source.get("displayLabel") or "").strip()[:200]
        if display_label:
            safe_source["displayLabel"] = display_label
        safe_sources.append(safe_source)

    safe_sources.sort(key=lambda item: item["sourceRef"])
    outbound_document = {
        "workflowRef": safe_workflow_ref,
        "workflowKind": safe_workflow_kind,
        "sources": safe_sources,
    }
    # Human-readable labels and the whole-graph audit hash are deliberately
    # excluded. Renaming a card or changing an unrelated graph node must not
    # stale a person's confirmation.
    rights_document = {
        "workflowRef": safe_workflow_ref,
        "workflowKind": safe_workflow_kind,
        "sources": [
            {key: value for key, value in source.items() if key != "displayLabel"}
            for source in safe_sources
        ],
    }
    manifest_hash = hashlib.sha256(
        json.dumps(
            rights_document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    payload = {**outbound_document, "manifestHash": manifest_hash}
    if graph_hash not in (None, ""):
        payload["graphHash"] = _require_sha256(graph_hash, "graphHash")
    return payload


class BindingStore:
    """Crash-safe private mapping store for one ComfyUI installation."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()

    def _read(self) -> dict[str, Any]:
        try:
            with open(self.path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            value = {}
        if not isinstance(value, dict) or value.get("version") != SCHEMA_VERSION:
            return {"version": SCHEMA_VERSION, "workflows": {}}
        if not isinstance(value.get("workflows"), dict):
            value["workflows"] = {}
        return value

    def _write(self, value: dict[str, Any]) -> None:
        write_private_json(self.path, value)

    @staticmethod
    def _public(binding: dict[str, Any]) -> dict[str, Any]:
        result = {
            "workflowRef": binding["workflow_ref"],
            "projectId": binding.get("project_id"),
            "workflowKind": binding.get("workflow_kind", "other"),
        }
        if binding.get("graph_hash"):
            result["graphHash"] = binding["graph_hash"]
        if binding.get("manifest_hash"):
            result["manifestHash"] = binding["manifest_hash"]
        return result

    def resolve_workflow(
        self, local_workflow_key: object, graph_hash: object | None = None
    ) -> dict[str, Any]:
        """Resolve a local-only key to a stable random workflow UUID."""
        local_value = str(local_workflow_key or "").strip()
        if not local_value or len(local_value) > 1024:
            raise ValueError("localWorkflowKey is required and must be at most 1024 characters.")
        local_digest = _sha256_text(local_value)
        safe_graph_hash = None
        if graph_hash not in (None, ""):
            safe_graph_hash = _require_sha256(graph_hash, "graphHash")

        with self._lock:
            data = self._read()
            workflows = data["workflows"]
            binding = workflows.get(local_digest)
            if not isinstance(binding, dict):
                binding = {
                    "workflow_ref": str(uuid.uuid4()),
                    "project_id": None,
                    "workflow_kind": "other",
                    "source_refs": {},
                }
                workflows[local_digest] = binding
            if safe_graph_hash:
                binding["graph_hash"] = safe_graph_hash
            self._write(data)
            return self._public(binding)

    def _find(self, data: dict[str, Any], workflow_ref: str) -> dict[str, Any]:
        safe_ref = _require_uuid(workflow_ref, "workflowRef")
        for binding in data["workflows"].values():
            if isinstance(binding, dict) and binding.get("workflow_ref") == safe_ref:
                return binding
        raise ValueError("Unknown workflowRef. Resolve this workflow locally first.")

    def get(self, workflow_ref: object) -> dict[str, Any]:
        with self._lock:
            binding = self._find(self._read(), str(workflow_ref or ""))
            return self._public(deepcopy(binding))

    def associate(
        self, workflow_ref: object, project_id: object, workflow_kind: object
    ) -> dict[str, Any]:
        project = _require_identifier(project_id, "projectId")
        kind = str(workflow_kind or "other")
        if kind not in WORKFLOW_KINDS:
            raise ValueError("workflowKind is not supported.")
        with self._lock:
            data = self._read()
            binding = self._find(data, str(workflow_ref or ""))
            binding["project_id"] = project
            binding["workflow_kind"] = kind
            self._write(data)
            return self._public(binding)

    def resolve_source(
        self, workflow_ref: object, local_source_key: object, source_kind: object
    ) -> dict[str, str]:
        """Mint a stable random SHA-256 ref without persisting raw source data."""
        local_value = str(local_source_key or "")
        if not local_value or len(local_value) > 4096:
            raise ValueError("localSourceKey is required and must be at most 4096 characters.")
        kind = str(source_kind or "unknown")
        if kind not in SOURCE_KINDS:
            raise ValueError("sourceKind is not supported.")
        locator_digest = _sha256_text(f"{kind}\0{local_value}")
        with self._lock:
            data = self._read()
            binding = self._find(data, str(workflow_ref or ""))
            source_refs = binding.setdefault("source_refs", {})
            source_ref = source_refs.get(locator_digest)
            if not isinstance(source_ref, str) or not SHA256_PATTERN.fullmatch(source_ref):
                source_ref = _random_sha256()
                source_refs[locator_digest] = source_ref
            self._write(data)
            return {"sourceRef": source_ref, "sourceKind": kind}

    def source_belongs_to_workflow(self, workflow_ref: object, source_ref: object) -> bool:
        with self._lock:
            binding = self._find(self._read(), str(workflow_ref or ""))
            return str(source_ref or "") in set(binding.get("source_refs", {}).values())

    def source_matches_workflow(
        self,
        workflow_ref: object,
        source_ref: object,
        local_source_key: object,
        source_kind: object,
    ) -> bool:
        """Verify that an opaque ref was minted for this exact local source slot."""

        local_value = str(local_source_key or "")
        if not local_value or len(local_value) > 4096:
            return False
        kind = str(source_kind or "unknown")
        if kind not in SOURCE_KINDS:
            return False
        locator_digest = _sha256_text(f"{kind}\0{local_value}")
        with self._lock:
            binding = self._find(self._read(), str(workflow_ref or ""))
            return binding.get("source_refs", {}).get(locator_digest) == str(
                source_ref or ""
            )

    @staticmethod
    def _person_drafts(binding: dict[str, Any]) -> dict[str, Any]:
        drafts = binding.get("person_drafts")
        return drafts if isinstance(drafts, dict) else {}

    def list_person_drafts(
        self,
        workflow_ref: object,
        source_ref: object | None = None,
    ) -> list[dict[str, Any]]:
        """List private drafts, optionally filtering by an opaque local source ref."""
        with self._lock:
            binding = self._find(self._read(), str(workflow_ref or ""))
            known_source_refs = set(binding.get("source_refs", {}).values())
            safe_source_ref = None
            if source_ref not in (None, ""):
                safe_source_ref = _require_sha256(source_ref, "sourceRef")
                if safe_source_ref not in known_source_refs:
                    raise ValueError("sourceRef was not minted for this workflow.")

            drafts = []
            for stored in self._person_drafts(binding).values():
                normalized = _normalize_person_draft(stored, known_source_refs)
                if safe_source_ref and safe_source_ref not in normalized["sourceRefs"]:
                    continue
                drafts.append(normalized)
            return deepcopy(drafts)

    def put_person_draft(
        self,
        workflow_ref: object,
        body: object,
    ) -> dict[str, Any]:
        """Create or replace one local-only draft attached to one or more sources."""
        with self._lock:
            data = self._read()
            binding = self._find(data, str(workflow_ref or ""))
            known_source_refs = set(binding.get("source_refs", {}).values())
            normalized = _normalize_person_draft(body, known_source_refs)
            draft_id = normalized.get("draftId") or str(uuid.uuid4())
            normalized["draftId"] = draft_id

            drafts = binding.get("person_drafts")
            if not isinstance(drafts, dict):
                drafts = {}
                binding["person_drafts"] = drafts
            drafts[draft_id] = normalized
            self._write(data)
            return deepcopy(normalized)

    def delete_person_draft(
        self,
        workflow_ref: object,
        draft_id: object,
    ) -> bool:
        """Delete a local draft without changing source or project bindings."""
        safe_draft_id = _require_uuid(draft_id, "draftId")
        with self._lock:
            data = self._read()
            binding = self._find(data, str(workflow_ref or ""))
            drafts = binding.get("person_drafts")
            if not isinstance(drafts, dict) or safe_draft_id not in drafts:
                return False
            del drafts[safe_draft_id]
            self._write(data)
            return True

    def list_source_reviews(self, workflow_ref: object) -> list[dict[str, str]]:
        """List private producer outcomes for visual sources with no detected face."""
        with self._lock:
            binding = self._find(self._read(), str(workflow_ref or ""))
            known_source_refs = set(binding.get("source_refs", {}).values())
            stored = binding.get("source_reviews")
            if not isinstance(stored, dict):
                return []
            reviews: list[dict[str, str]] = []
            for source_ref, value in sorted(stored.items()):
                if source_ref not in known_source_refs:
                    continue
                # Scalar values came from the short-lived pre-hash prototype.
                state = (
                    value
                    if isinstance(value, str)
                    else value.get("state")
                    if isinstance(value, dict)
                    else None
                )
                source_hash = (
                    value.get("source_hash") if isinstance(value, dict) else None
                )
                if state not in LOCAL_SOURCE_REVIEW_STATES:
                    continue
                review = {"sourceRef": source_ref, "state": state}
                if isinstance(source_hash, str) and SHA256_PATTERN.fullmatch(source_hash):
                    review["sourceHash"] = source_hash
                reviews.append(review)
            return reviews

    def put_source_review(
        self,
        workflow_ref: object,
        source_ref: object,
        body: object,
    ) -> dict[str, str]:
        """Persist a local, reversible no-face review without requiring a connection."""
        if not isinstance(body, dict):
            raise ValueError("Source review must be an object.")
        safe_source_ref = _require_sha256(source_ref, "sourceRef")
        state = str(body.get("state") or "")
        if state not in LOCAL_SOURCE_REVIEW_STATES:
            raise ValueError("Source review state is not supported.")
        source_hash = _require_sha256(body.get("sourceHash"), "sourceHash")
        with self._lock:
            data = self._read()
            binding = self._find(data, str(workflow_ref or ""))
            if safe_source_ref not in set(binding.get("source_refs", {}).values()):
                raise ValueError("sourceRef was not minted for this workflow.")
            reviews = binding.get("source_reviews")
            if not isinstance(reviews, dict):
                reviews = {}
                binding["source_reviews"] = reviews
            reviews[safe_source_ref] = {
                "state": state,
                "source_hash": source_hash,
            }
            self._write(data)
            return {
                "sourceRef": safe_source_ref,
                "state": state,
                "sourceHash": source_hash,
            }

    def source_links_payload(
        self, workflow_ref: object, project_id: object, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Build a safe outbound source manifest without marking it synced."""
        with self._lock:
            data = self._read()
            binding = self._find(data, str(workflow_ref or ""))
            expected_project = str(binding.get("project_id") or "")
            project = _require_identifier(project_id, "projectId")
            if expected_project and expected_project != project:
                raise ValueError("workflowRef is associated with a different project.")
            kind = str(body.get("workflowKind") or binding.get("workflow_kind") or "other")
            graph_hash = body.get("graphHash", binding.get("graph_hash"))
            payload = normalize_source_links(
                workflow_ref=binding["workflow_ref"],
                workflow_kind=kind,
                graph_hash=graph_hash,
                sources=body.get("sources"),
            )
            known_source_refs = set(binding.get("source_refs", {}).values())
            for source in payload["sources"]:
                if source["sourceRef"] not in known_source_refs:
                    raise ValueError("sourceRef was not minted for this workflow.")
            return payload

    def record_source_links(
        self, workflow_ref: object, project_id: object, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Record a manifest only after the upstream sync returned success."""
        project = _require_identifier(project_id, "projectId")
        normalized = normalize_source_links(
            workflow_ref=workflow_ref,
            workflow_kind=payload.get("workflowKind"),
            graph_hash=payload.get("graphHash"),
            sources=payload.get("sources"),
        )
        if normalized["manifestHash"] != payload.get("manifestHash"):
            raise ValueError("manifestHash does not match the normalized rights manifest.")
        with self._lock:
            data = self._read()
            binding = self._find(data, str(workflow_ref or ""))
            expected_project = str(binding.get("project_id") or "")
            if expected_project and expected_project != project:
                raise ValueError("workflowRef is associated with a different project.")
            binding["project_id"] = project
            binding["workflow_kind"] = normalized["workflowKind"]
            if normalized.get("graphHash"):
                binding["graph_hash"] = normalized["graphHash"]
            binding["manifest_hash"] = normalized["manifestHash"]
            self._write(data)
            return self._public(binding)
