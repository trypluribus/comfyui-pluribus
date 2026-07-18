"""Local-only identity analysis records.

These records deliberately do not extend ``PersonInstance``.  A graph source,
an observed face, a likely identity cluster, and a user-confirmed person link
are different facts with different confidence and deletion lifecycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceRecord:
    """One resolved local media source.

    ``local_path`` is private implementation data.  It must never be included
    in an HTTP response or an evidence manifest.
    """

    source_ref: str
    media_type: str
    source_hash: str
    local_path: str = field(repr=False)
    display_label: str = ""
    byte_size: int = 0

    def public_dict(self) -> dict[str, Any]:
        return {
            "sourceRef": self.source_ref,
            "mediaType": self.media_type,
            "sourceHash": self.source_hash,
            "displayLabel": self.display_label,
            "byteSize": self.byte_size,
        }


@dataclass(frozen=True)
class FaceOccurrence:
    """A face observation with no claim about the person's legal identity."""

    occurrence_id: str
    source_ref: str
    media_type: str
    frame_index: int
    timestamp_ms: int
    bbox: tuple[int, int, int, int]
    confidence: float
    crop_artifact_id: str
    candidate_id: str = ""
    source_label: str = ""
    ambiguous: bool = False

    def public_dict(self, job_id: str) -> dict[str, Any]:
        timestamp_seconds = round(self.timestamp_ms / 1000.0, 3)
        return {
            "occurrenceId": self.occurrence_id,
            "candidateId": self.candidate_id,
            "sourceRef": self.source_ref,
            "sourceLabel": self.source_label,
            "mediaType": self.media_type,
            "frameIndex": self.frame_index,
            "timestampMs": self.timestamp_ms,
            "timestampSeconds": timestamp_seconds,
            "timecode": _timecode(self.timestamp_ms),
            "bbox": list(self.bbox),
            "confidence": round(float(self.confidence), 6),
            "cropUrl": (
                f"/pluribus/identity/jobs/{job_id}/evidence/" f"{self.crop_artifact_id}"
            ),
            "frameUrl": None,
            "sceneLabel": "",
            "ambiguous": bool(self.ambiguous),
        }


@dataclass(frozen=True)
class IdentityCandidate:
    """A conservative, project-scoped cluster awaiting producer review."""

    candidate_id: str
    occurrence_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    confidence: float
    grouping_band: str = "mixed"
    grouping_label: str = "Mixed appearance - review"
    evidence_artifact_ids: tuple[str, ...] = ()
    suggested_name: str = ""
    suggested_role: str = ""
    suggestion_source: str = ""
    needs_review: bool = True

    def public_dict(self, job_id: str) -> dict[str, Any]:
        return {
            "candidateId": self.candidate_id,
            "suggestedName": self.suggested_name,
            "suggestedRole": self.suggested_role,
            "suggestionSource": self.suggestion_source or None,
            "confidence": round(float(self.confidence), 6),
            "confidenceBand": self.grouping_band,
            "groupingBand": self.grouping_band,
            "groupingLabel": self.grouping_label,
            "confidenceMeaning": "heuristic_similarity_not_probability",
            "occurrenceCount": len(self.occurrence_ids),
            "occurrenceIds": list(self.occurrence_ids),
            "sourceRefs": list(self.source_refs),
            "evidence": [
                f"/pluribus/identity/jobs/{job_id}/evidence/{artifact_id}"
                for artifact_id in self.evidence_artifact_ids
            ],
            "evidenceArtifacts": [
                {
                    "type": "evidence_sheet",
                    "url": f"/pluribus/identity/jobs/{job_id}/evidence/{artifact_id}",
                }
                for artifact_id in self.evidence_artifact_ids
            ],
            "state": "needs_review" if self.needs_review else "confirmed",
            "needsReview": bool(self.needs_review),
        }


@dataclass(frozen=True)
class PersonLink:
    """A producer-confirmed link; identity similarity never creates one."""

    candidate_id: str
    person_id: str = ""
    state: str = "confirmed"
    display_name: str = ""
    occurrence_ids: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "candidateId": self.candidate_id,
            "personId": self.person_id,
            "state": self.state,
            "displayName": self.display_name,
        }
        if self.occurrence_ids:
            result["occurrenceIds"] = list(self.occurrence_ids)
        return result


def _timecode(timestamp_ms: int) -> str:
    total_seconds, milliseconds = divmod(max(0, int(timestamp_ms)), 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
