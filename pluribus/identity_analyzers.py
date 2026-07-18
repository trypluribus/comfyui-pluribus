"""Pluggable local media analyzers for project-scoped identity suggestions.

OpenCV is imported only when the optional backend is asked to run.  The plugin
therefore continues to load in ComfyUI installations without OpenCV or model
weights, and reports an actionable capability issue instead of crashing.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import threading
from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from .identity_models import SourceRecord


LOGGER = logging.getLogger(__name__)
MAX_CLUSTER_CANDIDATE_COMPARISONS = 512
DEFAULT_MAX_TOTAL_CROP_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_CROP_SIDE = 512


class AnalysisCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class AnalyzerStatus:
    available: bool
    analyzer_id: str
    model_version: str
    issues: tuple[dict, ...] = ()

    def public_dict(self) -> dict:
        return {
            "available": self.available,
            "analyzerId": self.analyzer_id,
            "modelVersion": self.model_version,
            "issues": [dict(issue) for issue in self.issues],
        }


@dataclass(frozen=True)
class AnalyzedOccurrence:
    """Internal analyzer result.

    Embeddings exist only in memory long enough to cluster the current job.
    They are never serialized or exposed by the service.
    """

    source_ref: str
    media_type: str
    frame_index: int
    timestamp_ms: int
    bbox: tuple[int, int, int, int]
    confidence: float
    embedding: tuple[float, ...]
    crop_bytes: bytes
    crop_extension: str = ".jpg"
    # Faces in the same real frame cannot be the same person. Contact sheets
    # are an exception: each visual tile is a pseudo-frame, so repeated faces
    # across tiles can cluster while co-performers within one tile cannot.
    cooccurrence_group: str = ""
    # The content hash is part of the observation identity.  A stable graph
    # source reference identifies a slot in a workflow, not the bytes currently
    # occupying that slot; confirmations must therefore stale when those bytes
    # change even if a detector returns the same frame and bounding box.
    source_hash: str = ""


ProgressCallback = Callable[[int, int, str], None]


class AnalysisOutput(list[AnalyzedOccurrence]):
    """List-compatible analyzer output with additive limit metadata."""

    def __init__(self):
        super().__init__()
        self.issues: list[dict] = []
        self.completed_source_refs: set[str] = set()
        self.limit_reached = False
        self.source_complete = True
        self.sampled_frames = 0
        self.crop_bytes = 0
        self.crop_bytes_limited = False

    def add_issue(self, issue: dict) -> None:
        key = (issue.get("code"), issue.get("sourceRef"))
        if any(
            (value.get("code"), value.get("sourceRef")) == key
            for value in self.issues
        ):
            return
        self.issues.append(issue)


class FrameAnalysis(list[AnalyzedOccurrence]):
    def __init__(self):
        super().__init__()
        self.faces_truncated = False
        self.pixels_limited = False
        self.crop_bytes = 0
        self.crop_bytes_limited = False


class IdentityAnalyzer(Protocol):
    analyzer_id: str
    model_version: str

    def status(self) -> AnalyzerStatus: ...

    def analyze(
        self,
        sources: Sequence[SourceRecord],
        cancel_event: threading.Event,
        progress: ProgressCallback,
    ) -> list[AnalyzedOccurrence]: ...


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    numerator = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return numerator / (left_norm * right_norm)


class OpenCVYuNetSFaceAnalyzer:
    """CPU-first YuNet detection plus SFace embedding backend.

    This class never downloads models.  The explicit model installer stores
    verified weights in the private Pluribus data directory.
    """

    analyzer_id = "opencv_yunet_sface"

    def __init__(
        self,
        yunet_path: str,
        sface_path: str,
        *,
        model_version: str = "opencv-yunet-2023mar+sface-2021dec-v1",
        similarity_threshold: float = 0.38,
        sample_interval_seconds: float = 2.0,
        max_video_frames: int = 900,
        detector_top_k: int = 128,
        max_faces_per_frame: int = 32,
        max_total_occurrences: int = 1_500,
        max_frame_pixels: int = 100_000_000,
        max_total_crop_bytes: int = DEFAULT_MAX_TOTAL_CROP_BYTES,
        max_crop_side: int = DEFAULT_MAX_CROP_SIDE,
    ):
        self.yunet_path = yunet_path
        self.sface_path = sface_path
        self.model_version = model_version
        self.similarity_threshold = similarity_threshold
        self.sample_interval_seconds = max(0.25, sample_interval_seconds)
        self.max_video_frames = max(1, max_video_frames)
        self.detector_top_k = max(1, min(512, detector_top_k))
        self.max_faces_per_frame = max(1, min(128, max_faces_per_frame))
        self.max_total_occurrences = max(1, max_total_occurrences)
        self.max_frame_pixels = max(1, max_frame_pixels)
        self.max_total_crop_bytes = max(1, max_total_crop_bytes)
        self.max_crop_side = max(64, min(2048, max_crop_side))

    def status(self) -> AnalyzerStatus:
        issues: list[dict] = []
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            LOGGER.warning(
                "Optional OpenCV/NumPy identity dependencies are unavailable",
                exc_info=True,
            )
            issues.append(
                {
                    "issueId": "identity_dependency_unavailable",
                    "code": "dependency_unavailable",
                    "severity": "error",
                    "title": "Local identity dependencies are unavailable",
                    "description": (
                        "OpenCV and NumPy must be present in the ComfyUI Python "
                        "environment before local identity analysis can run."
                    ),
                    "message": "Optional local identity dependencies are unavailable.",
                    "action": {
                        "type": "install_python_dependency",
                        "packages": [
                            "opencv-python-headless>=4.8,<5",
                            "numpy>=1.24,<3",
                        ],
                        "automatic": False,
                    },
                }
            )
        missing = [
            name
            for name, path in (("YuNet", self.yunet_path), ("SFace", self.sface_path))
            if not os.path.isfile(path)
        ]
        if missing:
            issues.append(
                {
                    "issueId": "identity_models_unavailable",
                    "code": "models_unavailable",
                    "severity": "warning",
                    "title": "Local identity models are not installed",
                    "description": (
                        f"Install the verified {' and '.join(missing)} model files "
                        "to enable local face clustering. No model is downloaded "
                        "without an explicit install request."
                    ),
                    "message": "Required model files are missing.",
                    "action": {
                        "type": "install_models",
                        "method": "POST",
                        "endpoint": "/pluribus/identity/models/install",
                        "body": {
                            "modelId": "opencv-yunet-sface-v1",
                            "confirm": True,
                        },
                    },
                }
            )
        return AnalyzerStatus(
            available=not issues,
            analyzer_id=self.analyzer_id,
            model_version=self.model_version,
            issues=tuple(issues),
        )

    def analyze(
        self,
        sources: Sequence[SourceRecord],
        cancel_event: threading.Event,
        progress: ProgressCallback,
    ) -> list[AnalyzedOccurrence]:
        status = self.status()
        if not status.available:
            raise RuntimeError("Local identity analyzer is unavailable.")

        import cv2

        detector = cv2.FaceDetectorYN.create(
            self.yunet_path,
            "",
            (320, 320),
            score_threshold=0.82,
            nms_threshold=0.3,
            top_k=self.detector_top_k,
        )
        recognizer = cv2.FaceRecognizerSF.create(self.sface_path, "")
        output = AnalysisOutput()
        total = len(sources)
        remaining_video_frames = self.max_video_frames
        for source_index, source in enumerate(sources):
            self._check_cancel(cancel_event)
            progress(source_index, total, source.source_ref)
            if source.media_type == "image":
                image = cv2.imread(source.local_path)
                if image is None:
                    output.add_issue(
                        _limit_issue(
                            "identity_image_decode_failed",
                            "image_decode_failed",
                            "Image could not be decoded",
                            (
                                "The local image decoder could not open this source. "
                                "Convert it to a supported PNG or JPEG and retry; do not "
                                "treat this source as analyzed."
                            ),
                            source.source_ref,
                        )
                    )
                    continue
                frame_result = self._analyze_frame(
                    cv2,
                    detector,
                    recognizer,
                    source,
                    image,
                    frame_index=0,
                    timestamp_ms=0,
                    cancel_event=cancel_event,
                    max_faces=min(
                        self.max_faces_per_frame,
                        self.max_total_occurrences - len(output),
                    ),
                    max_frame_pixels=self.max_frame_pixels,
                    max_crop_bytes=self.max_total_crop_bytes - output.crop_bytes,
                )
                output.extend(frame_result)
                output.crop_bytes += frame_result.crop_bytes
                self._record_frame_limits(output, frame_result, source)
                if frame_result.crop_bytes_limited:
                    output.limit_reached = True
                    output.crop_bytes_limited = True
                if not frame_result.pixels_limited and not frame_result.crop_bytes_limited:
                    output.completed_source_refs.add(source.source_ref)
            elif source.media_type == "video":
                if remaining_video_frames <= 0:
                    output.limit_reached = True
                    output.add_issue(
                        _limit_issue(
                            "identity_video_frame_limit_reached",
                            "video_frame_limit_reached",
                            "Video analysis reached its frame-sampling limit",
                            (
                                f"The job sampled the first {self.max_video_frames} video "
                                "frames allowed by the local resource limit. Review the "
                                "remaining source manually or run it in a separate job."
                            ),
                            source.source_ref,
                        )
                    )
                    continue
                video_result = self._analyze_video(
                    cv2,
                    detector,
                    recognizer,
                    source,
                    cancel_event,
                    progress,
                    source_index,
                    total,
                    self.max_total_occurrences - len(output),
                    remaining_video_frames,
                    self.max_total_crop_bytes - output.crop_bytes,
                )
                output.extend(video_result)
                output.sampled_frames += video_result.sampled_frames
                output.crop_bytes += video_result.crop_bytes
                remaining_video_frames = max(
                    0, remaining_video_frames - video_result.sampled_frames
                )
                for issue in video_result.issues:
                    output.add_issue(issue)
                if video_result.source_complete:
                    output.completed_source_refs.add(source.source_ref)
                if video_result.crop_bytes_limited:
                    output.limit_reached = True
                    output.crop_bytes_limited = True
            if output.crop_bytes_limited:
                break
            if len(output) >= self.max_total_occurrences:
                output.limit_reached = True
                output.add_issue(
                    _limit_issue(
                        "identity_occurrence_limit_reached",
                        "occurrence_limit_reached",
                        "Identity analysis reached its appearance limit",
                        (
                            f"The job retained the first {self.max_total_occurrences} "
                            "face appearances and stopped scanning additional media. "
                            "Review the partial result or analyze fewer sources."
                        ),
                        source.source_ref,
                    )
                )
                break
        progress(total, total, "")
        return output

    def _analyze_video(
        self,
        cv2,
        detector,
        recognizer,
        source,
        cancel_event,
        progress,
        source_index: int,
        source_total: int,
        remaining_occurrences: int,
        remaining_frames: int,
        remaining_crop_bytes: int,
    ) -> AnalysisOutput:
        capture = cv2.VideoCapture(source.local_path)
        results = AnalysisOutput()
        if not capture.isOpened():
            capture.release()
            results.source_complete = False
            results.add_issue(
                _limit_issue(
                    "identity_video_decode_failed",
                    "video_decode_failed",
                    "Video could not be decoded",
                    (
                        "The local video decoder could not open this source. "
                        "Convert it to a supported local MP4 or image sequence and retry."
                    ),
                    source.source_ref,
                )
            )
            return results
        try:
            fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            if fps <= 0:
                fps = 24.0
            step = max(1, int(round(fps * self.sample_interval_seconds)))
            frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            sampled_total = (
                max(1, math.ceil(frame_count / step)) if frame_count > 0 else 0
            )
            frame_index = 0
            sampled = 0
            while sampled < remaining_frames and len(results) < remaining_occurrences:
                self._check_cancel(cancel_event)
                capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame = capture.read()
                if not ok:
                    if sampled == 0 or (
                        frame_count > 0 and frame_index < frame_count
                    ):
                        results.source_complete = False
                        results.add_issue(
                            _limit_issue(
                                "identity_video_decode_failed",
                                "video_decode_failed",
                                "Video decoding stopped early",
                                (
                                    "The local decoder could not read all expected video "
                                    "frames. Review the partial result or convert the source "
                                    "to a supported local MP4 and retry."
                                ),
                                source.source_ref,
                            )
                        )
                    break
                timestamp_ms = int(round(frame_index * 1000.0 / fps))
                frame_result = self._analyze_frame(
                    cv2,
                    detector,
                    recognizer,
                    source,
                    frame,
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    cancel_event=cancel_event,
                    max_faces=min(
                        self.max_faces_per_frame,
                        remaining_occurrences - len(results),
                    ),
                    max_frame_pixels=self.max_frame_pixels,
                    max_crop_bytes=remaining_crop_bytes - results.crop_bytes,
                )
                results.extend(frame_result)
                results.crop_bytes += frame_result.crop_bytes
                self._record_frame_limits(results, frame_result, source)
                sampled += 1
                self._report_frame_progress(
                    progress,
                    source_index,
                    source_total,
                    source.source_ref,
                    sampled,
                    sampled_total,
                    frame_index,
                    timestamp_ms,
                )
                if frame_result.pixels_limited:
                    results.source_complete = False
                if frame_result.crop_bytes_limited:
                    results.limit_reached = True
                    results.source_complete = False
                    results.crop_bytes_limited = True
                    break
                frame_index += step
            if len(results) >= remaining_occurrences:
                results.limit_reached = True
                results.source_complete = False
            results.sampled_frames = sampled
            if sampled >= remaining_frames and (
                sampled_total == 0 or sampled < sampled_total
            ):
                results.limit_reached = True
                results.source_complete = False
                results.add_issue(
                    _limit_issue(
                        "identity_video_frame_limit_reached",
                        "video_frame_limit_reached",
                        "Video analysis reached its frame-sampling limit",
                        (
                            f"The job sampled the first {self.max_video_frames} video "
                            "frames allowed by the local resource limit. Review the "
                            "partial result or analyze a shorter clip."
                        ),
                        source.source_ref,
                    )
                )
        finally:
            capture.release()
        return results

    def _analyze_frame(
        self,
        cv2,
        detector,
        recognizer,
        source: SourceRecord,
        frame,
        *,
        frame_index: int,
        timestamp_ms: int,
        cancel_event: threading.Event,
        max_faces: int,
        max_frame_pixels: int,
        max_crop_bytes: int,
    ) -> FrameAnalysis:
        height, width = frame.shape[:2]
        results = FrameAnalysis()
        if height * width > max_frame_pixels:
            results.pixels_limited = True
            return results
        self._check_cancel(cancel_event)
        detector.setInputSize((width, height))
        _, faces = detector.detect(frame)
        self._check_cancel(cancel_event)
        if faces is None:
            return results
        faces = sorted(faces, key=lambda face: float(face[-1]), reverse=True)
        face_count = len(faces)
        results.faces_truncated = face_count > max_faces
        for face in faces[:max_faces]:
            self._check_cancel(cancel_event)
            x, y, box_width, box_height = [int(round(value)) for value in face[:4]]
            x = max(0, x)
            y = max(0, y)
            box_width = max(1, min(box_width, width - x))
            box_height = max(1, min(box_height, height - y))
            aligned = recognizer.alignCrop(frame, face)
            self._check_cancel(cancel_event)
            feature = recognizer.feature(aligned).flatten()
            embedding = tuple(float(value) for value in feature)
            pad_x = int(round(box_width * 0.35))
            pad_y = int(round(box_height * 0.35))
            crop = frame[
                max(0, y - pad_y) : min(height, y + box_height + pad_y),
                max(0, x - pad_x) : min(width, x + box_width + pad_x),
            ]
            crop_height, crop_width = crop.shape[:2]
            if max(crop_height, crop_width) > self.max_crop_side:
                scale = self.max_crop_side / max(crop_height, crop_width)
                resized_width = max(1, int(round(crop_width * scale)))
                resized_height = max(1, int(round(crop_height * scale)))
                crop = cv2.resize(
                    crop,
                    (resized_width, resized_height),
                    interpolation=getattr(cv2, "INTER_AREA", 3),
                )
            encoded, buffer = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
            self._check_cancel(cancel_event)
            if not encoded:
                continue
            encoded_bytes = bytes(buffer)
            if results.crop_bytes + len(encoded_bytes) > max_crop_bytes:
                results.crop_bytes_limited = True
                break
            results.append(
                AnalyzedOccurrence(
                    source_ref=source.source_ref,
                    media_type=source.media_type,
                    frame_index=frame_index,
                    timestamp_ms=timestamp_ms,
                    bbox=(x, y, box_width, box_height),
                    confidence=max(0.0, min(1.0, float(face[-1]))),
                    embedding=embedding,
                    crop_bytes=encoded_bytes,
                    cooccurrence_group=_cooccurrence_group(
                        source,
                        frame_index,
                        x + box_width / 2,
                        y + box_height / 2,
                        width,
                        height,
                    ),
                    source_hash=source.source_hash,
                )
            )
            results.crop_bytes += len(encoded_bytes)
        return results

    def _record_frame_limits(
        self,
        output: AnalysisOutput,
        frame_result: FrameAnalysis,
        source: SourceRecord,
    ) -> None:
        if frame_result.faces_truncated:
            output.add_issue(
                _limit_issue(
                    "identity_faces_per_frame_limited",
                    "faces_per_frame_limited",
                    "Some faces were skipped in dense frames",
                    (
                        f"At most {self.max_faces_per_frame} faces are retained from "
                        "one frame. Split dense contact sheets into smaller sources "
                        "if every appearance needs review."
                    ),
                    source.source_ref,
                )
            )
        if frame_result.pixels_limited:
            output.add_issue(
                _limit_issue(
                    "identity_frame_pixels_limited",
                    "frame_pixels_limited",
                    "A visual frame exceeded the analysis pixel limit",
                    (
                        f"Frames above {self.max_frame_pixels:,} pixels are skipped "
                        "to keep local analysis responsive. Resize the source and retry."
                    ),
                    source.source_ref,
                )
            )
        if frame_result.crop_bytes_limited:
            output.add_issue(
                _limit_issue(
                    "identity_crop_memory_limit_reached",
                    "crop_memory_limit_reached",
                    "Identity analysis reached its in-memory evidence limit",
                    (
                        f"The job retained up to {self.max_total_crop_bytes:,} bytes "
                        "of bounded portrait crops and stopped before accumulating "
                        "more private face data. Review the partial result or analyze "
                        "fewer sources."
                    ),
                    source.source_ref,
                )
            )

    @staticmethod
    def _report_frame_progress(
        progress,
        source_completed: int,
        source_total: int,
        source_ref: str,
        sampled_frames: int,
        sampled_frame_total: int,
        frame_index: int,
        timestamp_ms: int,
    ) -> None:
        frame_callback = getattr(progress, "frame", None)
        if callable(frame_callback):
            frame_callback(
                source_completed,
                source_total,
                source_ref,
                sampled_frames,
                sampled_frame_total,
                frame_index,
                timestamp_ms,
            )

    @staticmethod
    def _check_cancel(cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            raise AnalysisCancelled("Identity analysis was canceled.")


def _limit_issue(
    issue_id: str,
    code: str,
    title: str,
    description: str,
    source_ref: str,
) -> dict:
    return {
        "issueId": issue_id,
        "code": code,
        "severity": "warning",
        "title": title,
        "description": description,
        "message": description,
        "sourceRef": source_ref,
    }


def stable_occurrence_id(occurrence: AnalyzedOccurrence, model_version: str) -> str:
    material = "|".join(
        [
            occurrence.source_ref,
            occurrence.source_hash,
            str(occurrence.frame_index),
            str(occurrence.timestamp_ms),
            ",".join(str(value) for value in occurrence.bbox),
            model_version,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def cluster_occurrences(
    occurrences: Sequence[AnalyzedOccurrence],
    model_version: str,
    similarity_threshold: float = 0.38,
) -> tuple[list[dict], set[str]]:
    """Deterministically cluster faces without merging co-occurring people.

    Normalized-centroid assignment is deliberately project-local.  The result
    is a review suggestion, never a legal identity or clearance decision.
    """

    ordered = sorted(
        occurrences, key=lambda value: stable_occurrence_id(value, model_version)
    )
    clusters: list[list[AnalyzedOccurrence]] = []
    cluster_sums: list[list[float]] = []
    cluster_groups: list[set[str]] = []
    ambiguity: set[str] = set()
    for item in ordered:
        compatible: list[tuple[float, int]] = []
        item_group = item.cooccurrence_group or f"{item.source_ref}:{item.frame_index}"
        normalized_item = _normalized_embedding(item.embedding)
        for index in range(
            min(len(clusters), MAX_CLUSTER_CANDIDATE_COMPARISONS)
        ):
            if item_group in cluster_groups[index]:
                continue
            centroid = _normalize_vector(cluster_sums[index])
            score = cosine_similarity(item.embedding, centroid)
            if score >= similarity_threshold:
                compatible.append((score, index))
        compatible.sort(key=lambda value: (-value[0], value[1]))
        if compatible:
            best_score, best_index = compatible[0]
            clusters[best_index].append(item)
            cluster_groups[best_index].add(item_group)
            if len(cluster_sums[best_index]) == len(normalized_item):
                for dimension, value in enumerate(normalized_item):
                    cluster_sums[best_index][dimension] += value
            if len(compatible) > 1 and best_score - compatible[1][0] <= 0.04:
                ambiguity.add(stable_occurrence_id(item, model_version))
        else:
            clusters.append([item])
            cluster_sums.append(list(normalized_item))
            cluster_groups.append({item_group})

    result: list[dict] = []
    for cluster_index, cluster in enumerate(clusters):
        occurrence_ids = sorted(
            stable_occurrence_id(value, model_version) for value in cluster
        )
        candidate_id = hashlib.sha256(
            (model_version + "|" + "|".join(occurrence_ids)).encode("utf-8")
        ).hexdigest()[:20]
        if len(cluster) == 1:
            confidence = min(0.5, float(cluster[0].confidence))
            grouping_band = "single"
            grouping_label = "Single appearance"
        else:
            centroid = _normalize_vector(cluster_sums[cluster_index])
            centroid_scores = sorted(
                cosine_similarity(item.embedding, centroid) for item in cluster
            )
            detector_scores = sorted(float(item.confidence) for item in cluster)
            similarity_median = _median(centroid_scores)
            detector_median = _median(detector_scores)
            confidence = 0.75 * max(0.0, similarity_median) + 0.25 * max(
                0.0, detector_median
            )
            if similarity_median >= 0.62:
                grouping_band = "strong"
                grouping_label = "Strong visual grouping"
            elif similarity_median >= 0.45:
                grouping_band = "likely"
                grouping_label = "Likely the same person"
            else:
                grouping_band = "mixed"
                grouping_label = "Mixed appearance - review"
        result.append(
            {
                "candidateId": candidate_id,
                "confidence": max(0.0, min(1.0, confidence)),
                "groupingBand": grouping_band,
                "groupingLabel": grouping_label,
                "items": sorted(
                    cluster,
                    key=lambda value: stable_occurrence_id(value, model_version),
                ),
            }
        )
    result.sort(key=lambda value: value["candidateId"])
    return result, ambiguity


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return float(values[middle - 1] + values[middle]) / 2.0


def normalized_centroid(
    embeddings: Sequence[Sequence[float]],
) -> tuple[float, ...]:
    if not embeddings:
        return ()
    dimension = len(embeddings[0])
    summed = [0.0] * dimension
    for embedding in embeddings:
        if len(embedding) != dimension:
            return ()
        norm = sum(value * value for value in embedding) ** 0.5
        if norm == 0:
            continue
        for index, value in enumerate(embedding):
            summed[index] += value / norm
    norm = sum(value * value for value in summed) ** 0.5
    if norm == 0:
        return tuple(summed)
    return tuple(value / norm for value in summed)


def _normalized_embedding(embedding: Sequence[float]) -> tuple[float, ...]:
    return _normalize_vector(embedding)


def _normalize_vector(values: Sequence[float]) -> tuple[float, ...]:
    norm = sum(value * value for value in values) ** 0.5
    if norm == 0:
        return tuple(float(value) for value in values)
    return tuple(float(value) / norm for value in values)


def _cooccurrence_group(
    source: SourceRecord,
    frame_index: int,
    center_x: float,
    center_y: float,
    width: int,
    height: int,
) -> str:
    label = f"{source.display_label} {os.path.basename(source.local_path)}".lower()
    montage_hints = (
        "character_sheet",
        "character sheet",
        "storyboard",
        "contact_sheet",
        "contact sheet",
        "lookbook",
        "grid",
        "identity_evidence",
        "identity evidence",
        "party_visual_candidate",
        "party visual candidate",
    )
    if any(hint in label for hint in montage_hints) and width > 0 and height > 0:
        # Little Flower sheets use four columns but range from two to ten rows.
        # Infer rows from the canvas aspect ratio and a roughly 16:10 panel;
        # a fixed four-row bucket would incorrectly treat distinct panels as a
        # single cannot-link group on tall storyboards.
        tile_height_to_width = 0.63
        rows = min(
            20,
            max(1, int(round((height / width) * 4 / tile_height_to_width))),
        )
        column = min(3, max(0, int(center_x / width * 4)))
        row = min(rows - 1, max(0, int(center_y / height * rows)))
        return f"{source.source_ref}:{frame_index}:tile:{row}:{column}"
    return f"{source.source_ref}:{frame_index}"
