import asyncio
import hashlib
import io
import json
import os
import sys
import threading
import types

import pytest
from PIL import Image
import pluribus.identity_analyzers as identity_analyzers_module
import pluribus.identity_service as identity_service_module

from pluribus.identity_analyzers import (
    AnalysisCancelled,
    AnalysisOutput,
    AnalyzedOccurrence,
    AnalyzerStatus,
    OpenCVYuNetSFaceAnalyzer,
    _cooccurrence_group,
    cluster_occurrences,
    stable_occurrence_id,
)
from pluribus.identity_models_install import IdentityModelInstaller, ModelSpec
from pluribus.identity_models import SourceRecord
from pluribus.identity_service import (
    IdentityAnalysisService,
    IdentityCapacityError,
    IdentityConflictError,
    LocalMediaResolver,
    _apply_source_label_suggestions,
)


def _image_bytes(color=(180, 90, 50)):
    output = io.BytesIO()
    Image.new("RGB", (40, 40), color).save(output, format="JPEG", quality=92)
    return output.getvalue()


class FakeAnalyzer:
    analyzer_id = "fake_faces"
    model_version = "fake-model-v3"

    def __init__(self):
        self.calls = 0

    def status(self):
        return AnalyzerStatus(True, self.analyzer_id, self.model_version)

    def analyze(self, sources, cancel_event, progress):
        self.calls += 1
        first, second = sources
        progress(0, len(sources), first.source_ref)
        values = [
            AnalyzedOccurrence(
                first.source_ref,
                "image",
                0,
                0,
                (0, 0, 20, 20),
                0.96,
                (1.0, 0.0),
                _image_bytes((220, 90, 50)),
            ),
            # Same frame and nearly identical embedding: must never merge.
            AnalyzedOccurrence(
                first.source_ref,
                "image",
                0,
                0,
                (20, 0, 20, 20),
                0.94,
                (0.999, 0.001),
                _image_bytes((50, 90, 220)),
            ),
            # A later/source occurrence may join exactly one of those people.
            AnalyzedOccurrence(
                second.source_ref,
                "image",
                0,
                0,
                (5, 5, 20, 20),
                0.91,
                (1.0, 0.0),
                _image_bytes((210, 80, 45)),
            ),
        ]
        progress(len(sources), len(sources), "")
        return values


class UnavailableAnalyzer:
    analyzer_id = "missing"
    model_version = "missing-v1"

    def status(self):
        return AnalyzerStatus(
            False,
            self.analyzer_id,
            self.model_version,
            (
                {
                    "issueId": "identity_models_unavailable",
                    "severity": "warning",
                    "title": "Models unavailable",
                    "description": "Install local models.",
                    "action": {
                        "type": "install_models",
                        "endpoint": "/pluribus/identity/models/install",
                    },
                },
            ),
        )

    def analyze(self, *_args):
        raise AssertionError("Unavailable analyzer must not run")


class MutableAnalyzer(FakeAnalyzer):
    def __init__(self):
        super().__init__()
        self.available = False

    def status(self):
        if self.available:
            return super().status()
        return AnalyzerStatus(
            False,
            self.analyzer_id,
            self.model_version,
            (
                {
                    "issueId": "identity_models_unavailable",
                    "code": "models_unavailable",
                    "severity": "warning",
                    "title": "Models unavailable",
                    "description": "Install local models.",
                },
            ),
        )


class CancellableAnalyzer(FakeAnalyzer):
    def analyze(self, sources, cancel_event, progress):
        self.calls += 1
        while not cancel_event.wait(0.01):
            progress(0, len(sources), sources[0].source_ref)
        raise AnalysisCancelled()


def _source(path, source_ref):
    return {
        "sourceRef": source_ref,
        "sourceKey": os.path.basename(path),
        "displayLabel": os.path.basename(path),
    }


async def _wait_for_terminal(service, job_id):
    for _ in range(200):
        payload = service.get_job(job_id)
        if payload["state"] in {"completed", "failed", "canceled"}:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError("identity job did not finish")


def test_resolver_restricts_paths_and_hashes_media(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    image = root / "portrait.jpg"
    image.write_bytes(_image_bytes())
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(_image_bytes((1, 2, 3)))
    resolver = LocalMediaResolver([str(root)])

    records, issues = resolver.resolve_many(
        [
            _source(str(image), "a" * 64),
            {
                "sourceRef": "b" * 64,
                "sourceKey": str(outside),
            },
        ]
    )

    assert len(records) == 1
    assert records[0].source_hash == hashlib.sha256(image.read_bytes()).hexdigest()
    assert records[0].public_dict().get("localPath") is None
    assert issues[0]["code"] == "source_unavailable"


def test_resolver_supports_comfyui_annotations_and_still_blocks_escape(tmp_path):
    root = tmp_path / "input"
    nested = root / "characters"
    nested.mkdir(parents=True)
    image = nested / "layla.png"
    image.write_bytes(_image_bytes())
    outside = tmp_path / "outside.png"
    outside.write_bytes(_image_bytes())

    def annotated(name):
        clean = name.removesuffix(" [input]")
        if clean == "escape.png":
            return str(outside)
        return str(root / clean)

    resolver = LocalMediaResolver([str(root)], annotated_resolver=annotated)
    records, issues = resolver.resolve_many(
        [
            {
                "sourceRef": "a" * 64,
                "sourceKey": "characters/layla.png [input]",
            },
            {"sourceRef": "b" * 64, "sourceKey": "escape.png [input]"},
        ]
    )

    assert [record.source_ref for record in records] == ["a" * 64]
    assert records[0].local_path == str(image)
    assert issues[0]["code"] == "source_unavailable"


def test_identity_job_rejects_unminted_or_duplicate_source_refs_before_enqueue(
    tmp_path,
):
    media = tmp_path / "input"
    media.mkdir()
    image = media / "portrait.jpg"
    image.write_bytes(_image_bytes())
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )

    async def scenario():
        with pytest.raises(ValueError, match="minted"):
            await service.start_job(
                {"sources": [{"sourceRef": "not-minted", "sourceKey": image.name}]}
            )
        with pytest.raises(ValueError, match="only once"):
            await service.start_job(
                {
                    "sources": [
                        _source(str(image), "a" * 64),
                        _source(str(image), "a" * 64),
                    ]
                }
            )

    asyncio.run(scenario())
    assert list((tmp_path / "state" / "identity" / "jobs").iterdir()) == []


def test_resolver_enforces_file_total_byte_and_image_pixel_limits(tmp_path):
    root = tmp_path / "input"
    root.mkdir()
    first = root / "first.jpg"
    second = root / "second.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((1, 2, 3)))

    file_limited = LocalMediaResolver(
        [str(root)], max_source_bytes=first.stat().st_size - 1
    )
    records, issues = file_limited.resolve_many([_source(str(first), "a" * 64)])
    assert records == []
    assert issues[0]["code"] == "source_file_too_large"

    total_limited = LocalMediaResolver(
        [str(root)],
        max_source_bytes=10_000,
        max_total_bytes=first.stat().st_size + second.stat().st_size - 1,
    )
    records, issues = total_limited.resolve_many(
        [_source(str(first), "a" * 64), _source(str(second), "b" * 64)]
    )
    assert [record.source_ref for record in records] == ["a" * 64]
    assert issues[0]["code"] == "source_total_bytes_exceeded"

    pixel_limited = LocalMediaResolver(
        [str(root)], max_source_bytes=10_000, max_image_pixels=1_000
    )
    records, issues = pixel_limited.resolve_many([_source(str(first), "a" * 64)])
    assert records == []
    assert issues[0]["code"] == "source_image_pixels_exceeded"


def test_opencv_analyzer_caps_detector_and_occurrences_with_frame_progress(
    tmp_path, monkeypatch
):
    import numpy

    created = {}
    frame = numpy.zeros((40, 40, 3), dtype=numpy.uint8)
    faces = numpy.array(
        [
            [0, 0, 10, 10, 0.99],
            [10, 0, 10, 10, 0.98],
            [20, 0, 10, 10, 0.97],
            [30, 0, 10, 10, 0.96],
        ],
        dtype=float,
    )

    class Detector:
        def setInputSize(self, _size):
            pass

        def detect(self, _frame):
            return None, faces

    class Recognizer:
        def alignCrop(self, current_frame, _face):
            return current_frame

        def feature(self, _aligned):
            return numpy.array([[1.0, 0.0]])

    class Capture:
        def __init__(self, _path):
            self.position = 0

        def isOpened(self):
            return True

        def get(self, key):
            if key == fake_cv2.CAP_PROP_FPS:
                return 1
            if key == fake_cv2.CAP_PROP_FRAME_COUNT:
                return 2
            return 0

        def set(self, _key, value):
            self.position = int(value)

        def read(self):
            return (True, frame) if self.position < 2 else (False, None)

        def release(self):
            pass

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.CAP_PROP_FPS = 1
    fake_cv2.CAP_PROP_FRAME_COUNT = 2
    fake_cv2.CAP_PROP_POS_FRAMES = 3
    fake_cv2.IMWRITE_JPEG_QUALITY = 4

    def create_detector(*_args, **kwargs):
        created["top_k"] = kwargs["top_k"]
        return Detector()

    fake_cv2.FaceDetectorYN = types.SimpleNamespace(create=create_detector)
    fake_cv2.FaceRecognizerSF = types.SimpleNamespace(
        create=lambda *_args, **_kwargs: Recognizer()
    )
    fake_cv2.imread = lambda _path: frame
    fake_cv2.VideoCapture = Capture
    fake_cv2.imencode = lambda *_args, **_kwargs: (True, _image_bytes())
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"model")
    sface.write_bytes(b"model")
    analyzer = OpenCVYuNetSFaceAnalyzer(
        str(yunet),
        str(sface),
        sample_interval_seconds=1,
        detector_top_k=17,
        max_faces_per_frame=2,
        max_total_occurrences=3,
    )
    sources = [
        SourceRecord("a" * 64, "image", "1" * 64, "image.jpg"),
        SourceRecord("b" * 64, "video", "2" * 64, "video.mp4"),
    ]
    source_progress = []
    frame_progress = []

    def progress(completed, total, source_ref):
        source_progress.append((completed, total, source_ref))

    def report_frame(*values):
        frame_progress.append(values)

    progress.frame = report_frame
    result = analyzer.analyze(sources, threading.Event(), progress)

    assert isinstance(result, AnalysisOutput)
    assert created["top_k"] == 17
    assert len(result) == 3
    assert result.completed_source_refs == {"a" * 64}
    assert {issue["code"] for issue in result.issues} == {
        "faces_per_frame_limited",
        "occurrence_limit_reached",
    }
    assert frame_progress == [(1, 2, "b" * 64, 1, 2, 0, 0)]
    assert source_progress[-1] == (2, 2, "")

    memory_bounded = OpenCVYuNetSFaceAnalyzer(
        str(yunet),
        str(sface),
        max_total_crop_bytes=1,
    ).analyze(sources, threading.Event(), lambda *_args: None)
    assert memory_bounded == []
    assert memory_bounded.crop_bytes_limited is True
    assert {issue["code"] for issue in memory_bounded.issues} == {
        "crop_memory_limit_reached"
    }

    memory_video_progress = []

    def memory_video_callback(*_args):
        pass

    memory_video_callback.frame = lambda *values: memory_video_progress.append(values)
    memory_bounded_video = OpenCVYuNetSFaceAnalyzer(
        str(yunet),
        str(sface),
        max_total_crop_bytes=1,
    ).analyze([sources[1]], threading.Event(), memory_video_callback)
    assert memory_bounded_video.crop_bytes_limited is True
    assert memory_bounded_video.sampled_frames == 1
    assert len(memory_video_progress) == 1

    media = tmp_path / "input"
    media.mkdir()
    image_path = media / "image.jpg"
    video_path = media / "video.mp4"
    image_path.write_bytes(_image_bytes())
    video_path.write_bytes(b"local-video")
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )

    async def run_limited_job():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(image_path), "a" * 64),
                    _source(str(video_path), "b" * 64),
                ]
            }
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(run_limited_job())
    assert completed["state"] == "completed"
    assert completed["coverage"]["analyzedSources"] == 1
    assert completed["coverage"]["skippedSources"] == 1
    assert completed["coverage"]["detectedOccurrences"] == 3
    assert {issue["code"] for issue in completed["issues"]} >= {
        "faces_per_frame_limited",
        "occurrence_limit_reached",
    }
    assert completed["manualReviewRequired"] is True
    assert {value["sourceRef"] for value in completed["manualReviewSources"]} == {
        "a" * 64,
        "b" * 64,
    }

    canceled = threading.Event()
    canceled.set()
    with pytest.raises(AnalysisCancelled):
        analyzer.analyze(sources, canceled, lambda *_args: None)


def test_opencv_analyzer_default_occurrence_limit_is_bounded(tmp_path):
    analyzer = OpenCVYuNetSFaceAnalyzer(
        str(tmp_path / "yunet.onnx"), str(tmp_path / "sface.onnx")
    )
    assert analyzer.max_total_occurrences == 1_500


def test_video_frame_limit_reports_partial_coverage_and_truthful_progress(
    tmp_path, monkeypatch
):
    import numpy

    frame = numpy.zeros((20, 20, 3), dtype=numpy.uint8)

    class Detector:
        def setInputSize(self, _size):
            pass

        def detect(self, _frame):
            return None, None

    class Capture:
        def __init__(self, _path):
            self.position = 0

        def isOpened(self):
            return True

        def get(self, key):
            if key == fake_cv2.CAP_PROP_FPS:
                return 1
            if key == fake_cv2.CAP_PROP_FRAME_COUNT:
                return 5
            return 0

        def set(self, _key, value):
            self.position = int(value)

        def read(self):
            return (True, frame) if self.position < 5 else (False, None)

        def release(self):
            pass

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.CAP_PROP_FPS = 1
    fake_cv2.CAP_PROP_FRAME_COUNT = 2
    fake_cv2.CAP_PROP_POS_FRAMES = 3
    fake_cv2.IMWRITE_JPEG_QUALITY = 4
    fake_cv2.FaceDetectorYN = types.SimpleNamespace(
        create=lambda *_args, **_kwargs: Detector()
    )
    fake_cv2.FaceRecognizerSF = types.SimpleNamespace(
        create=lambda *_args, **_kwargs: object()
    )
    fake_cv2.VideoCapture = Capture
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"model")
    sface.write_bytes(b"model")
    analyzer = OpenCVYuNetSFaceAnalyzer(
        str(yunet),
        str(sface),
        sample_interval_seconds=1,
        max_video_frames=2,
    )
    media = tmp_path / "input"
    media.mkdir()
    video = media / "long.mp4"
    video.write_bytes(b"local-video")
    source_record = SourceRecord(
        "a" * 64, "video", "1" * 64, str(video), "long.mp4"
    )
    frame_updates = []

    def progress(*_args):
        pass

    progress.frame = lambda *values: frame_updates.append(values)
    direct = analyzer.analyze([source_record], threading.Event(), progress)
    assert direct.completed_source_refs == set()
    assert frame_updates[-1][3:5] == (2, 5)

    frame_updates.clear()
    second_source = SourceRecord(
        "b" * 64, "video", "2" * 64, str(video), "second-long.mp4"
    )
    combined = analyzer.analyze(
        [source_record, second_source], threading.Event(), progress
    )
    assert combined.sampled_frames == 2
    assert len(frame_updates) == 2
    assert {
        issue["sourceRef"]
        for issue in combined.issues
        if issue["code"] == "video_frame_limit_reached"
    } == {"a" * 64, "b" * 64}

    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )

    async def scenario():
        started = await service.start_job(
            {"sources": [_source(str(video), "a" * 64)]}
        )
        saw_frame_progress = None
        for _ in range(200):
            current = service.get_job(started["jobId"])
            if current["progress"].get("sampledFrames"):
                saw_frame_progress = current["progress"]
            if current["state"] in {"completed", "failed", "canceled"}:
                return current, saw_frame_progress
            await asyncio.sleep(0.01)
        raise AssertionError("identity job did not finish")

    completed, frame_progress = asyncio.run(scenario())
    assert completed["state"] == "completed"
    assert completed["coverage"]["analyzedSources"] == 0
    assert completed["coverage"]["skippedSources"] == 1
    assert {issue["code"] for issue in completed["issues"]} >= {
        "video_frame_limit_reached"
    }
    assert completed["manualReviewSources"] == [
        {
            "sourceRef": "a" * 64,
            "sourceHash": hashlib.sha256(video.read_bytes()).hexdigest(),
            "issueCodes": ["video_frame_limit_reached"],
        }
    ]
    if frame_progress is not None:
        assert frame_progress["sampledFrames"] <= 2
        assert frame_progress["sampledFrameTotal"] == 5


def test_video_open_failure_reports_decode_issue_and_is_not_analyzed(
    tmp_path, monkeypatch
):
    class Capture:
        def __init__(self, _path):
            self.released = False

        def isOpened(self):
            return False

        def release(self):
            self.released = True

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.FaceDetectorYN = types.SimpleNamespace(
        create=lambda *_args, **_kwargs: object()
    )
    fake_cv2.FaceRecognizerSF = types.SimpleNamespace(
        create=lambda *_args, **_kwargs: object()
    )
    fake_cv2.VideoCapture = Capture
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"model")
    sface.write_bytes(b"model")
    analyzer = OpenCVYuNetSFaceAnalyzer(str(yunet), str(sface))
    media = tmp_path / "input"
    media.mkdir()
    video = media / "broken.mp4"
    video.write_bytes(b"not-a-video")
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )

    async def scenario():
        started = await service.start_job(
            {"sources": [_source(str(video), "b" * 64)]}
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())
    assert completed["state"] == "completed"
    assert completed["coverage"]["analyzedSources"] == 0
    assert completed["coverage"]["skippedSources"] == 1
    assert {issue["code"] for issue in completed["issues"]} >= {
        "video_decode_failed"
    }
    assert completed["manualReviewRequired"] is True
    assert completed["manualReviewSources"][0]["sourceRef"] == "b" * 64
    assert completed["manualReviewSources"][0]["sourceHash"] == hashlib.sha256(
        video.read_bytes()
    ).hexdigest()
    assert all(issue["code"] != "no_face_detected" for issue in completed["issues"])


def test_image_decode_failure_is_not_reported_as_analyzed(tmp_path, monkeypatch):
    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.FaceDetectorYN = types.SimpleNamespace(
        create=lambda *_args, **_kwargs: object()
    )
    fake_cv2.FaceRecognizerSF = types.SimpleNamespace(
        create=lambda *_args, **_kwargs: object()
    )
    fake_cv2.imread = lambda _path: None
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)

    yunet = tmp_path / "yunet.onnx"
    sface = tmp_path / "sface.onnx"
    yunet.write_bytes(b"model")
    sface.write_bytes(b"model")
    analyzer = OpenCVYuNetSFaceAnalyzer(str(yunet), str(sface))
    media = tmp_path / "input"
    media.mkdir()
    image = media / "broken.jpg"
    image.write_bytes(_image_bytes())
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )

    async def scenario():
        started = await service.start_job(
            {"sources": [_source(str(image), "a" * 64)]}
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())

    assert completed["state"] == "completed"
    assert completed["coverage"]["analyzedSources"] == 0
    assert completed["coverage"]["manualReviewSources"] == 1
    assert any(
        issue["code"] == "image_decode_failed" for issue in completed["issues"]
    )
    manual_issue = next(
        issue for issue in completed["issues"]
        if issue["code"] == "image_decode_failed"
    )
    assert manual_issue["manualReviewRequired"] is True
    assert manual_issue["sourceHash"] == hashlib.sha256(image.read_bytes()).hexdigest()


def test_manual_review_contract_covers_resolver_and_unexplained_partial_sources(
    tmp_path,
):
    class PartialAnalyzer(FakeAnalyzer):
        def analyze(self, sources, cancel_event, progress):
            self.calls += 1
            output = AnalysisOutput()
            output.completed_source_refs.add(sources[0].source_ref)
            output.add_issue(
                {
                    "issueId": "dense-frame",
                    "code": "faces_per_frame_limited",
                    "severity": "warning",
                    "title": "Dense frame",
                    "description": "Some faces were not retained.",
                    "message": "Some faces were not retained.",
                    "sourceRef": sources[0].source_ref,
                }
            )
            progress(len(sources), len(sources), "")
            return output

    media = tmp_path / "input"
    media.mkdir()
    first = media / "first.jpg"
    second = media / "second.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((8, 9, 10)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=PartialAnalyzer(),
        media_roots=[str(media)],
    )

    async def scenario():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                    _source(str(media / "missing.jpg"), "c" * 64),
                ]
            }
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())
    by_source = {
        value["sourceRef"]: value for value in completed["manualReviewSources"]
    }
    assert completed["manualReviewRequired"] is True
    assert set(by_source) == {"a" * 64, "b" * 64, "c" * 64}
    assert set(by_source["a" * 64]["issueCodes"]) == {
        "faces_per_frame_limited",
        "no_face_detected",
    }
    assert by_source["b" * 64]["issueCodes"] == ["analysis_incomplete"]
    assert by_source["c" * 64]["issueCodes"] == ["source_unavailable"]
    assert by_source["a" * 64]["sourceHash"] == hashlib.sha256(
        first.read_bytes()
    ).hexdigest()
    assert by_source["b" * 64]["sourceHash"] == hashlib.sha256(
        second.read_bytes()
    ).hexdigest()
    assert by_source["c" * 64]["sourceHash"] is None
    scoped_issues = [issue for issue in completed["issues"] if issue.get("sourceRef")]
    assert scoped_issues
    assert all(issue["manualReviewRequired"] is True for issue in scoped_issues)
    assert all("sourceHash" in issue for issue in scoped_issues)


def test_centroid_clustering_reduces_pose_fragmentation_but_never_same_frame():
    def occurrence(source_ref, frame_index, embedding, x=0):
        return AnalyzedOccurrence(
            source_ref,
            "video",
            frame_index,
            frame_index * 1000,
            (x, 0, 10, 10),
            0.9,
            embedding,
            _image_bytes(),
        )

    # A->B and the normalized A/B centroid->C clear .38, although A->C does
    # not. This is the pose/lighting variation complete-link over-fragmented.
    prototypes = [
        occurrence("a" * 64, 0, (0.0, 0.0)),
        occurrence("b" * 64, 1, (0.0, 0.0)),
        occurrence("c" * 64, 2, (0.0, 0.0)),
    ]
    prototypes.sort(key=lambda item: stable_occurrence_id(item, "test-model"))
    embeddings = [(0.5, 0.866), (1.0, 0.0), (0.0, 1.0)]
    ordered_input = [
        AnalyzedOccurrence(
            item.source_ref,
            item.media_type,
            item.frame_index,
            item.timestamp_ms,
            item.bbox,
            item.confidence,
            embedding,
            item.crop_bytes,
        )
        for item, embedding in zip(prototypes, embeddings)
    ]
    clustered, _ = cluster_occurrences(
        ordered_input,
        "test-model",
        0.38,
    )
    assert len(clustered) == 1

    separated, _ = cluster_occurrences(
        [
            occurrence("a" * 64, 0, (1.0, 0.0), 0),
            occurrence("a" * 64, 0, (1.0, 0.0), 20),
        ],
        "test-model",
        0.38,
    )
    assert len(separated) == 2


def test_clustering_bounds_candidate_comparisons(monkeypatch):
    occurrences = [
        AnalyzedOccurrence(
            f"{index:064x}",
            "image",
            index,
            0,
            (0, 0, 10, 10),
            0.9,
            (1.0, 0.0),
            b"crop",
            cooccurrence_group=f"group-{index}",
        )
        for index in range(520)
    ]
    comparisons = 0
    original = identity_analyzers_module.cosine_similarity

    def counted(left, right):
        nonlocal comparisons
        comparisons += 1
        return original(left, right)

    monkeypatch.setattr(identity_analyzers_module, "cosine_similarity", counted)
    clusters, _ambiguity = cluster_occurrences(
        occurrences, "bounded-model", similarity_threshold=1.1
    )

    assert len(clusters) == len(occurrences)
    assert comparisons == sum(
        min(index, identity_analyzers_module.MAX_CLUSTER_CANDIDATE_COMPARISONS)
        for index in range(len(occurrences))
    )


def test_contact_sheet_tiles_allow_repeated_person_but_block_tile_coperformer():
    source_ref = "d" * 64

    def montage_face(x, embedding, tile):
        return AnalyzedOccurrence(
            source_ref,
            "image",
            0,
            0,
            (x, 0, 10, 10),
            0.94,
            embedding,
            _image_bytes(),
            cooccurrence_group=f"{source_ref}:0:tile:{tile}",
        )

    candidates, _ = cluster_occurrences(
        [
            montage_face(0, (1.0, 0.0), "0:0"),
            montage_face(20, (0.0, 1.0), "0:0"),
            montage_face(40, (0.999, 0.001), "0:1"),
        ],
        "test-model",
        0.38,
    )

    assert sorted(len(candidate["items"]) for candidate in candidates) == [1, 2]
    repeated = next(
        candidate for candidate in candidates if len(candidate["items"]) == 2
    )
    assert {item.cooccurrence_group for item in repeated["items"]} == {
        f"{source_ref}:0:tile:0:0",
        f"{source_ref}:0:tile:0:1",
    }


def test_tall_storyboard_infers_more_than_four_pseudo_frame_rows(tmp_path):
    path = tmp_path / "little_flower_SC01_storyboard.png"
    path.write_bytes(b"not-read-by-this-test")
    source = SourceRecord(
        source_ref="e" * 64,
        media_type="image",
        source_hash="f" * 64,
        local_path=str(path),
        display_label="Little Flower SC01 Storyboard",
    )
    top_group = _cooccurrence_group(source, 0, 100, 300, 2560, 4100)
    bottom_group = _cooccurrence_group(source, 0, 100, 3500, 2560, 4100)

    assert top_group.endswith(":tile:0:0")
    assert bottom_group.endswith(":tile:8:0")
    assert top_group != bottom_group


def test_identity_evidence_sheets_treat_tiles_as_separate_observations(tmp_path):
    path = tmp_path / "little_flower_reverse__layla_identity_evidence.png"
    path.write_bytes(b"not-read-by-this-test")
    source = SourceRecord(
        source_ref="e" * 64,
        media_type="image",
        source_hash="f" * 64,
        local_path=str(path),
        display_label=path.name,
    )

    first = _cooccurrence_group(source, 0, 120, 260, 1280, 904)
    second = _cooccurrence_group(source, 0, 450, 260, 1280, 904)

    assert ":tile:" in first
    assert ":tile:" in second
    assert first != second


def test_explicit_asset_labels_suggest_only_the_dominant_person():
    candidates = [
        {"candidateId": "lead", "suggestedName": "", "suggestedRole": ""},
        {"candidateId": "extra", "suggestedName": "", "suggestedRole": ""},
        {"candidateId": "generic", "suggestedName": "", "suggestedRole": ""},
        {"candidateId": "headshot", "suggestedName": "", "suggestedRole": ""},
    ]
    occurrences = [
        {"candidateId": "lead", "sourceRef": "sheet"},
        {"candidateId": "lead", "sourceRef": "sheet"},
        {"candidateId": "extra", "sourceRef": "sheet"},
        {"candidateId": "generic", "sourceRef": "scene"},
        {"candidateId": "headshot", "sourceRef": "headshot-source"},
    ]
    labels = {
        "sheet": "little_flower_reverse__nisreen_salem_as_layla_identity_evidence.png",
        "scene": "SC03_morning_storyboard.png",
        "headshot-source": "Nisreen_Salem_headshot.jpg",
    }

    _apply_source_label_suggestions(candidates, occurrences, labels)

    assert candidates[0]["suggestedName"] == "Nisreen Salem"
    assert candidates[0]["suggestedRole"] == "Layla"
    assert candidates[0]["suggestionSource"] == "source_label"
    assert candidates[1]["suggestedName"] == ""
    assert candidates[2]["suggestedName"] == ""
    assert candidates[3]["suggestedName"] == "Nisreen Salem"
    assert candidates[3]["suggestedRole"] == "Performer"


def test_identity_job_clusters_conservatively_serves_evidence_and_caches(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    analyzer = FakeAnalyzer()
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )
    body = {
        "workflowRef": "workflow-local",
        "workflowName": "Little Flower",
        "workflowFingerprint": "f" * 64,
        "sources": [
            _source(str(first), "a" * 64),
            _source(str(second), "b" * 64),
        ],
    }

    async def scenario():
        started = await service.start_job(body)
        completed = await _wait_for_terminal(service, started["jobId"])
        first_candidate = completed["candidates"][0]
        second_candidate = completed["candidates"][1]
        selected_occurrence = first_candidate["occurrenceIds"][0]
        service.put_links(
            completed["jobId"],
            {
                "baseRevision": 0,
                "links": [
                    {
                        "candidateId": first_candidate["candidateId"],
                        "personId": "person_layla",
                        "state": "confirmed",
                        "displayName": "Layla",
                        "occurrenceIds": [selected_occurrence],
                    }
                ]
            },
        )
        with pytest.raises(ValueError, match="belong"):
            service.put_links(
                completed["jobId"],
                {
                    "baseRevision": 1,
                    "links": [
                        {
                            "candidateId": first_candidate["candidateId"],
                            "personId": "person_layla",
                            "occurrenceIds": [second_candidate["occurrenceIds"][0]],
                        }
                    ]
                },
            )
        with pytest.raises(ValueError, match="belong"):
            service.put_links(
                completed["jobId"],
                {
                    "baseRevision": 1,
                    "links": [
                        {
                            "candidateId": first_candidate["candidateId"],
                            "personId": "person_layla",
                            "occurrenceIds": ["unknown-occurrence"],
                        }
                    ]
                },
            )
        cached_started = await service.start_job(body)
        cached = await _wait_for_terminal(service, cached_started["jobId"])
        return started, completed, cached

    started, completed, cached = asyncio.run(scenario())

    assert started["state"] in {"queued", "running"}
    assert completed["state"] == "completed"
    assert completed["coverage"] == {
        "totalSources": 2,
        "analyzedSources": 2,
        "skippedSources": 0,
        "imageCount": 2,
        "videoCount": 0,
        "audioCount": 0,
        "detectedOccurrences": 3,
    }
    assert len(completed["candidates"]) == 2
    assert [candidate["occurrenceCount"] for candidate in completed["candidates"]] == [
        2,
        1,
    ]
    assert all(
        candidate["groupingBand"] in {"strong", "likely", "mixed", "single"}
        for candidate in completed["candidates"]
    )
    assert all(
        candidate["confidenceMeaning"] == "heuristic_similarity_not_probability"
        for candidate in completed["candidates"]
    )
    assert all(
        all("sheet_" in evidence for evidence in candidate["evidence"])
        for candidate in completed["candidates"]
    )
    assert len(completed["occurrences"]) == 3
    assert analyzer.calls == 1
    assert cached["state"] == "completed"
    assert cached["cacheHit"] is True
    assert cached["jobId"] != completed["jobId"]
    assert cached["occurrences"][0]["cropUrl"].startswith(
        f"/pluribus/identity/jobs/{cached['jobId']}/"
    )
    assert cached["links"][0]["displayName"] == "Layla"
    assert len(cached["links"][0]["occurrenceIds"]) == 1
    linked_candidate = next(
        candidate
        for candidate in cached["candidates"]
        if candidate["candidateId"] == cached["links"][0]["candidateId"]
    )
    assert linked_candidate["state"] == "needs_review"
    assert linked_candidate["needsReview"] is True
    assert linked_candidate["partiallyConfirmed"] is True

    by_candidate = {}
    for occurrence in completed["occurrences"]:
        by_candidate.setdefault(occurrence["candidateId"], []).append(occurrence)
    for occurrences in by_candidate.values():
        frame_keys = {(item["sourceRef"], item["frameIndex"]) for item in occurrences}
        assert len(frame_keys) == len(occurrences)

    serialized = json.dumps(completed).lower()
    assert '"embedding":' not in serialized
    assert '"embeddings":' not in serialized
    assert str(media).lower() not in serialized
    manifest = service.evidence_manifest(completed["jobId"])
    assert "not proof" in manifest["notice"].lower()
    crop_id = completed["occurrences"][0]["cropUrl"].rsplit("/", 1)[-1]
    crop_path = service.artifact_path(completed["jobId"], crop_id)
    assert os.path.commonpath([crop_path, service.cache_dir]) == service.cache_dir
    with pytest.raises(ValueError):
        service.artifact_path(completed["jobId"], "../bindings.json")

    async def cleanup():
        first_delete = await service.delete_job(completed["jobId"])
        assert first_delete["deleted"] is True
        assert os.path.isfile(service.artifact_path(cached["jobId"], crop_id))
        assert (
            service.get_links(cached["jobId"])["links"][0]["personId"]
            == "person_layla"
        )
        assert service.delete_links(
            cached["jobId"], {"baseRevision": 1}
        )["deleted"] is True
        assert service.get_links(cached["jobId"])["links"] == []
        second_delete = await service.delete_job(cached["jobId"])
        assert second_delete["deleted"] is True

    asyncio.run(cleanup())
    assert not os.path.exists(crop_path)


def test_one_visual_candidate_can_be_split_across_multiple_people(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=FakeAnalyzer(),
        media_roots=[str(media)],
    )

    async def scenario():
        started = await service.start_job(
            {
                "workflowRef": "workflow-split",
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ],
            }
        )
        completed = await _wait_for_terminal(service, started["jobId"])
        candidate = completed["candidates"][0]
        first_occurrence, second_occurrence = candidate["occurrenceIds"]
        links = [
            {
                "candidateId": candidate["candidateId"],
                "personId": "person_layla",
                "state": "confirmed",
                "displayName": "Layla",
                "occurrenceIds": [first_occurrence],
            },
            {
                "candidateId": candidate["candidateId"],
                "personId": "person_extra",
                "state": "confirmed",
                "displayName": "Party guest",
                "occurrenceIds": [second_occurrence],
            },
        ]
        service.put_links(
            completed["jobId"], {"baseRevision": 0, "links": links}
        )
        with pytest.raises(ValueError, match="two different people"):
            service.put_links(
                completed["jobId"],
                {
                    "baseRevision": 1,
                    "links": [
                        *links,
                        {
                            "candidateId": candidate["candidateId"],
                            "personId": "person_other",
                            "state": "confirmed",
                            "occurrenceIds": [first_occurrence],
                        },
                    ]
                },
            )
        return service.get_job(completed["jobId"]), candidate["candidateId"]

    current, candidate_id = asyncio.run(scenario())
    candidate = next(
        value for value in current["candidates"] if value["candidateId"] == candidate_id
    )

    assert len(current["links"]) == 2
    assert candidate["state"] == "confirmed"
    assert candidate["needsReview"] is False
    assert candidate["partiallyConfirmed"] is False
    assert {person["displayName"] for person in candidate["confirmedPeople"]} == {
        "Layla",
        "Party guest",
    }


def test_analyzer_failure_never_exposes_private_media_paths(tmp_path):
    class FailingAnalyzer(FakeAnalyzer):
        def analyze(self, sources, cancel_event, progress):
            raise RuntimeError(f"decoder failed for {sources[0].local_path}")

    media = tmp_path / "private-input"
    media.mkdir()
    source = media / "private-person.jpg"
    source.write_bytes(_image_bytes())
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=FailingAnalyzer(),
        media_roots=[str(media)],
    )

    async def scenario():
        started = await service.start_job(
            {"sources": [_source(str(source), "a" * 64)]}
        )
        return await _wait_for_terminal(service, started["jobId"])

    failed = asyncio.run(scenario())
    public_json = json.dumps(failed)

    assert failed["state"] == "failed"
    assert str(source) not in public_json
    assert str(tmp_path) not in public_json
    assert failed["issues"][0]["code"] == "analysis_failed"

def test_cache_changes_when_source_bytes_change(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    analyzer = FakeAnalyzer()
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )
    body = {
        "sources": [
            _source(str(first), "a" * 64),
            _source(str(second), "b" * 64),
        ]
    }

    async def scenario():
        first_job = await service.start_job(body)
        await _wait_for_terminal(service, first_job["jobId"])
        second.write_bytes(_image_bytes((9, 8, 7)))
        second_job = await service.start_job(body)
        await _wait_for_terminal(service, second_job["jobId"])
        return second_job

    second_job = asyncio.run(scenario())
    assert second_job["cacheHit"] is False
    assert analyzer.calls == 2


def test_source_byte_change_stales_prior_identity_confirmation(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )
    body = {
        "workflowRef": "workflow-source-content-change",
        "sources": [
            _source(str(first), "a" * 64),
            _source(str(second), "b" * 64),
        ],
    }

    async def scenario():
        first_job = await service.start_job(body)
        first_result = await _wait_for_terminal(service, first_job["jobId"])
        candidate = first_result["candidates"][0]
        service.put_links(
            first_result["jobId"],
            {
                "baseRevision": 0,
                "links": [
                    {
                        "candidateId": candidate["candidateId"],
                        "personId": "person_from_old_bytes",
                        "state": "confirmed",
                        "occurrenceIds": candidate["occurrenceIds"],
                    }
                ]
            },
        )
        old_hash = next(
            value["sourceHash"]
            for value in first_result["sourceHashes"]
            if value["sourceRef"] == "a" * 64
        )
        first.write_bytes(_image_bytes((9, 8, 7)))
        second_job = await service.start_job(body)
        second_result = await _wait_for_terminal(service, second_job["jobId"])
        new_hash = next(
            value["sourceHash"]
            for value in second_result["sourceHashes"]
            if value["sourceRef"] == "a" * 64
        )
        return first_result, second_result, old_hash, new_hash

    first_result, second_result, old_hash, new_hash = asyncio.run(scenario())

    assert old_hash != new_hash
    assert {
        candidate["candidateId"] for candidate in first_result["candidates"]
    }.isdisjoint(
        candidate["candidateId"] for candidate in second_result["candidates"]
    )
    assert second_result["links"] == []


def test_stale_workflow_job_cannot_replace_newer_identity_links(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )
    body = {
        "workflowRef": "workflow-stale-link-write",
        "sources": [
            _source(str(first), "a" * 64),
            _source(str(second), "b" * 64),
        ],
    }

    async def scenario():
        old_job = await service.start_job(body)
        old_result = await _wait_for_terminal(service, old_job["jobId"])
        new_job = await service.start_job(body)
        new_result = await _wait_for_terminal(service, new_job["jobId"])
        candidate = new_result["candidates"][0]
        current_links = {
            "baseRevision": 0,
            "links": [
                {
                    "candidateId": candidate["candidateId"],
                    "personId": "current_person",
                    "state": "confirmed",
                    "occurrenceIds": candidate["occurrenceIds"],
                }
            ]
        }
        service.put_links(new_result["jobId"], current_links)
        with pytest.raises(ValueError, match="stale"):
            service.put_links(
                old_result["jobId"], {"baseRevision": 1, "links": []}
            )
        return service.get_links(new_result["jobId"])

    links = asyncio.run(scenario())["links"]
    assert len(links) == 1
    assert links[0]["personId"] == "current_person"


def test_concurrent_identity_link_writers_use_revision_compare_and_swap(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )

    async def scenario():
        started = await service.start_job(
            {
                "workflowRef": "workflow-link-revision",
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ],
            }
        )
        completed = await _wait_for_terminal(service, started["jobId"])
        candidate = completed["candidates"][0]
        assert service.get_links(completed["jobId"])["revision"] == 0

        def write(person_id):
            return service.put_links(
                completed["jobId"],
                {
                    "baseRevision": 0,
                    "links": [
                        {
                            "candidateId": candidate["candidateId"],
                            "personId": person_id,
                            "state": "confirmed",
                            "occurrenceIds": candidate["occurrenceIds"],
                        }
                    ],
                },
            )

        results = await asyncio.gather(
            asyncio.to_thread(write, "person_a"),
            asyncio.to_thread(write, "person_b"),
            return_exceptions=True,
        )
        return completed, results

    completed, results = asyncio.run(scenario())
    successes = [result for result in results if isinstance(result, dict)]
    conflicts = [result for result in results if isinstance(result, ValueError)]
    assert len(successes) == 1
    assert successes[0]["revision"] == 1
    assert len(conflicts) == 1
    assert "revision conflict" in str(conflicts[0]).lower()

    current = service.get_links(completed["jobId"])
    assert current["revision"] == 1
    assert current["links"] == successes[0]["links"]
    with pytest.raises(ValueError, match="baseRevision"):
        service.put_links(completed["jobId"], {"links": []})


def test_concurrent_identity_link_save_and_delete_share_revision_guard(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )

    async def scenario():
        started = await service.start_job(
            {
                "workflowRef": "workflow-save-delete-revision",
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ],
            }
        )
        completed = await _wait_for_terminal(service, started["jobId"])
        candidate = completed["candidates"][0]
        initial = service.put_links(
            completed["jobId"],
            {
                "baseRevision": 0,
                "links": [
                    {
                        "candidateId": candidate["candidateId"],
                        "personId": "initial_person",
                        "state": "confirmed",
                        "occurrenceIds": candidate["occurrenceIds"],
                    }
                ],
            },
        )
        assert initial["revision"] == 1

        def save():
            return service.put_links(
                completed["jobId"],
                {
                    "baseRevision": 1,
                    "links": [
                        {
                            "candidateId": candidate["candidateId"],
                            "personId": "replacement_person",
                            "state": "confirmed",
                            "occurrenceIds": candidate["occurrenceIds"],
                        }
                    ],
                },
            )

        def clear():
            return service.delete_links(
                completed["jobId"], {"baseRevision": 1}
            )

        results = await asyncio.gather(
            asyncio.to_thread(save),
            asyncio.to_thread(clear),
            return_exceptions=True,
        )
        return completed, results

    completed, results = asyncio.run(scenario())
    successes = [result for result in results if isinstance(result, dict)]
    conflicts = [
        result for result in results if isinstance(result, IdentityConflictError)
    ]
    assert len(successes) == 1
    assert successes[0]["revision"] == 2
    assert len(conflicts) == 1

    current = service.get_links(completed["jobId"])
    assert current["revision"] == 2
    assert current["links"] == successes[0]["links"]
    with pytest.raises(IdentityConflictError, match="revision conflict"):
        service.delete_links(completed["jobId"], {"baseRevision": 1})
    with pytest.raises(ValueError, match="baseRevision"):
        service.delete_links(completed["jobId"], {})


def test_person_delete_guard_revision_fences_concurrent_identity_save(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )
    workflow_ref = "workflow-person-delete-fence"
    guard_entered = threading.Event()
    release_guard = threading.Event()

    async def scenario():
        started = await service.start_job(
            {
                "workflowRef": workflow_ref,
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ],
            }
        )
        completed = await _wait_for_terminal(service, started["jobId"])
        candidate = completed["candidates"][0]

        def delete_person():
            with service.guard_unlinked_person_ids(
                workflow_ref, {"person_being_deleted"}
            ):
                guard_entered.set()
                assert release_guard.wait(2)
            return "deleted"

        def stale_save():
            return service.put_links(
                completed["jobId"],
                {
                    "baseRevision": 0,
                    "links": [
                        {
                            "candidateId": candidate["candidateId"],
                            "personId": "person_being_deleted",
                            "state": "confirmed",
                            "occurrenceIds": candidate["occurrenceIds"],
                        }
                    ],
                },
            )

        delete_task = asyncio.create_task(asyncio.to_thread(delete_person))
        assert await asyncio.to_thread(guard_entered.wait, 2)
        save_task = asyncio.create_task(asyncio.to_thread(stale_save))
        await asyncio.sleep(0.02)
        assert not save_task.done()
        release_guard.set()
        return completed, await asyncio.gather(
            delete_task, save_task, return_exceptions=True
        )

    completed, results = asyncio.run(scenario())
    assert results[0] == "deleted"
    assert isinstance(results[1], IdentityConflictError)
    assert service.get_links(completed["jobId"]) == {
        "jobId": completed["jobId"],
        "links": [],
        "revision": 1,
    }


def test_generic_result_failure_removes_partial_private_cache(tmp_path, monkeypatch):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )

    def fail_after_crops(*_args, **_kwargs):
        raise RuntimeError("evidence sheet failed")

    monkeypatch.setattr(
        identity_service_module, "_build_evidence_sheet", fail_after_crops
    )

    async def scenario():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ]
            }
        )
        return started["jobId"], await _wait_for_terminal(service, started["jobId"])

    job_id, failed = asyncio.run(scenario())
    private_job = service._get_job_record(job_id)
    cache_path = service._cache_path(private_job["cacheKey"])

    assert failed["state"] == "failed"
    assert not os.path.exists(cache_path)


def test_identity_artifact_byte_budget_returns_truthful_partial_result(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=FakeAnalyzer(),
        media_roots=[str(media)],
        max_evidence_artifact_bytes=1,
    )

    async def scenario():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ]
            }
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())
    assert completed["state"] == "completed"
    assert completed["coverage"]["analyzedSources"] == 0
    assert completed["coverage"]["detectedOccurrences"] == 0
    assert completed["coverage"]["manualReviewSources"] == 2
    assert completed["candidates"] == []
    assert {issue["code"] for issue in completed["issues"]} >= {
        "evidence_artifact_limit_reached",
        "evidence_omitted_source",
    }
    assert [
        issue["sourceRef"]
        for issue in completed["issues"]
        if issue["code"] == "evidence_omitted_source"
    ] == ["a" * 64, "b" * 64]
    assert completed["manualReviewRequired"] is True
    assert {
        value["sourceRef"]: value["issueCodes"]
        for value in completed["manualReviewSources"]
    } == {
        "a" * 64: ["evidence_omitted_source"],
        "b" * 64: ["evidence_omitted_source"],
    }
    assert all(issue["code"] != "no_face_detected" for issue in completed["issues"])
    assert service.capabilities()["resourceLimits"]["maxEvidenceArtifactBytes"] == 1


def test_unavailable_analyzer_completes_with_install_action(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    image = media / "portrait.jpg"
    image.write_bytes(_image_bytes())
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=UnavailableAnalyzer(),
        media_roots=[str(media)],
    )

    async def scenario():
        started = await service.start_job(
            {"sources": [_source(str(image), "a" * 64)]}
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())

    assert completed["state"] == "completed"
    assert completed["coverage"]["analyzedSources"] == 0
    assert completed["coverage"]["imageCount"] == 1
    assert completed["issues"][0]["action"]["type"] == "install_models"
    assert completed["manualReviewRequired"] is True
    assert completed["manualReviewSources"][0]["issueCodes"] == [
        "analysis_incomplete"
    ]
    assert service.capabilities()["privacy"]["persistsEmbeddings"] is False


def test_unavailable_result_does_not_mask_later_ready_analysis(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes((140, 80, 50)))
    analyzer = MutableAnalyzer()
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )
    body = {
        "sources": [
            _source(str(first), "a" * 64),
            _source(str(second), "b" * 64),
        ]
    }

    async def scenario():
        unavailable_started = await service.start_job(body)
        unavailable = await _wait_for_terminal(
            service, unavailable_started["jobId"]
        )
        analyzer.available = True
        ready = await service.start_job(body)
        completed = await _wait_for_terminal(service, ready["jobId"])
        return unavailable, ready, completed

    unavailable, ready, completed = asyncio.run(scenario())

    assert unavailable["state"] == "completed"
    assert unavailable["coverage"]["analyzedSources"] == 0
    assert ready["cacheHit"] is False
    assert completed["coverage"]["analyzedSources"] == 2
    assert completed["coverage"]["detectedOccurrences"] == 3
    assert analyzer.calls == 1


def test_ready_no_face_result_counts_opened_sources_as_analyzed(tmp_path):
    class NoFaceAnalyzer(FakeAnalyzer):
        def analyze(self, sources, cancel_event, progress):
            self.calls += 1
            progress(len(sources), len(sources), "")
            return []

    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes())
    analyzer = NoFaceAnalyzer()
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )

    async def scenario():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ]
            }
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())
    assert completed["coverage"]["analyzedSources"] == 2
    assert completed["coverage"]["skippedSources"] == 0
    assert completed["coverage"]["detectedOccurrences"] == 0
    assert completed["coverage"]["manualReviewSources"] == 2
    assert {value["sourceRef"] for value in completed["sourceHashes"]} == {
        "a" * 64,
        "b" * 64,
    }
    assert all(len(value["sourceHash"]) == 64 for value in completed["sourceHashes"])
    assert [issue["code"] for issue in completed["issues"]].count(
        "no_face_detected"
    ) == 2
    assert completed["manualReviewRequired"] is True
    assert all(
        value["sourceHash"] for value in completed["manualReviewSources"]
    )


def test_no_face_review_never_trusts_non_person_filename_cues(tmp_path):
    class NoFaceAnalyzer(FakeAnalyzer):
        def analyze(self, sources, cancel_event, progress):
            progress(len(sources), len(sources), "")
            return []

    media = tmp_path / "input"
    media.mkdir()
    paths = [
        media / "little_flower_reverse__location_bible.png",
        media / "little_flower_reverse__prop_and_wardrobe_bible.png",
        media / "little_flower_reverse__nightmare_shadow_body_evidence.png",
    ]
    for path in paths:
        path.write_bytes(_image_bytes())
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=NoFaceAnalyzer(),
        media_roots=[str(media)],
    )

    async def scenario():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(path), f"{index + 1}" * 64)
                    for index, path in enumerate(paths)
                ]
            }
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())
    manual = [
        issue for issue in completed["issues"]
        if issue.get("code") == "no_face_detected"
    ]
    assert completed["coverage"]["manualReviewSources"] == 3
    assert {issue["sourceRef"] for issue in manual} == {
        "1" * 64,
        "2" * 64,
        "3" * 64,
    }


def test_coverage_counts_resolved_audio_even_when_identity_skips_it(tmp_path):
    class NoFaceAnalyzer(FakeAnalyzer):
        def analyze(self, sources, cancel_event, progress):
            self.calls += 1
            assert [source.media_type for source in sources] == ["image"]
            progress(len(sources), len(sources), "")
            return []

    media = tmp_path / "input"
    media.mkdir()
    image = media / "scene.jpg"
    audio = media / "dialogue.wav"
    image.write_bytes(_image_bytes())
    audio.write_bytes(b"RIFF-local-audio")
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=NoFaceAnalyzer(),
        media_roots=[str(media)],
    )

    async def scenario():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(image), "a" * 64),
                    _source(str(audio), "b" * 64),
                ]
            }
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())
    assert completed["coverage"] == {
        "totalSources": 2,
        "analyzedSources": 1,
        "skippedSources": 1,
        "imageCount": 1,
        "videoCount": 0,
        "audioCount": 1,
        "detectedOccurrences": 0,
        "manualReviewSources": 2,
    }
    assert any(
        issue.get("code") == "source_unsupported" for issue in completed["issues"]
    )


def test_cancellable_background_job_retains_no_result(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes())
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=CancellableAnalyzer(),
        media_roots=[str(media)],
    )
    body = {
        "sources": [
            _source(str(first), "a" * 64),
            _source(str(second), "b" * 64),
        ]
    }

    async def scenario():
        started = await service.start_job(body)
        await asyncio.sleep(0.03)
        await service.cancel_job(started["jobId"])
        return await _wait_for_terminal(service, started["jobId"])

    canceled = asyncio.run(scenario())
    assert canceled["state"] == "canceled"
    assert canceled["issues"][0]["code"] == "canceled"


def test_identity_job_queue_has_backpressure(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes())
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        analyzer=CancellableAnalyzer(),
        media_roots=[str(media)],
        max_pending_jobs=1,
    )
    body = {
        "sources": [
            _source(str(first), "a" * 64),
            _source(str(second), "b" * 64),
        ]
    }

    async def scenario():
        started = await service.start_job(body)
        with pytest.raises(IdentityCapacityError, match="already queued"):
            await service.start_job(body)
        await service.cancel_job(started["jobId"])
        return await _wait_for_terminal(service, started["jobId"])

    canceled = asyncio.run(scenario())
    assert canceled["state"] == "canceled"
    assert service.capabilities()["resourceLimits"]["maxPendingJobs"] == 1


def test_start_job_returns_id_before_hashing_and_hashing_is_cancellable(
    tmp_path, monkeypatch
):
    media = tmp_path / "private-input"
    media.mkdir()
    source = media / "large-private-source.jpg"
    source.write_bytes(_image_bytes())
    analyzer = FakeAnalyzer()
    service = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=analyzer, media_roots=[str(media)]
    )
    hashing_started = threading.Event()

    def cancellable_hash(_path, cancel_event=None):
        hashing_started.set()
        assert cancel_event is not None
        while not cancel_event.wait(0.01):
            pass
        raise AnalysisCancelled("canceled while hashing")

    monkeypatch.setattr(identity_service_module, "_sha256_file", cancellable_hash)

    async def wait_for_hashing():
        for _ in range(200):
            if hashing_started.is_set():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("source hashing did not start")

    async def scenario():
        started = await asyncio.wait_for(
            service.start_job(
                {"sources": [_source(str(source), "a" * 64)]}
            ),
            timeout=0.25,
        )
        assert started["jobId"]
        assert started["state"] in {"queued", "running"}
        await wait_for_hashing()
        requested = await service.cancel_job(started["jobId"])
        assert requested["state"] == "cancel_requested"
        return started, await _wait_for_terminal(service, started["jobId"])

    started, canceled = asyncio.run(scenario())
    assert canceled["state"] == "canceled"
    assert canceled["issues"][0]["code"] == "canceled"
    assert analyzer.calls == 0
    public_json = json.dumps({"started": started, "canceled": canceled})
    assert str(source) not in public_json
    assert str(tmp_path) not in public_json


def test_job_progress_reports_grouping_and_evidence_phases(tmp_path):
    class PhaseService(IdentityAnalysisService):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.grouping_entered = threading.Event()
            self.grouping_release = threading.Event()
            self.evidence_entered = threading.Event()
            self.evidence_release = threading.Event()

        def _build_result(
            self,
            cache_key,
            records,
            inventory_records,
            analyzed,
            initial_issues,
            requested_count,
            building_evidence_callback=None,
            cancel_event=None,
        ):
            self.grouping_entered.set()
            assert self.grouping_release.wait(2)
            if building_evidence_callback:
                building_evidence_callback()
            self.evidence_entered.set()
            assert self.evidence_release.wait(2)
            return super()._build_result(
                cache_key,
                records,
                inventory_records,
                analyzed,
                initial_issues,
                requested_count,
                None,
                cancel_event,
            )

    media = tmp_path / "input"
    media.mkdir()
    first = media / "scene-a.jpg"
    second = media / "scene-b.jpg"
    first.write_bytes(_image_bytes())
    second.write_bytes(_image_bytes())
    service = PhaseService(
        str(tmp_path / "state"), analyzer=FakeAnalyzer(), media_roots=[str(media)]
    )

    async def wait_event(event):
        for _ in range(200):
            if event.is_set():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("phase was not reached")

    async def scenario():
        started = await service.start_job(
            {
                "sources": [
                    _source(str(first), "a" * 64),
                    _source(str(second), "b" * 64),
                ]
            }
        )
        await wait_event(service.grouping_entered)
        assert (
            service.get_job(started["jobId"])["progress"]["phase"] == "grouping_people"
        )
        service.grouping_release.set()
        await wait_event(service.evidence_entered)
        assert (
            service.get_job(started["jobId"])["progress"]["phase"]
            == "building_evidence"
        )
        service.evidence_release.set()
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())
    assert completed["progress"]["phase"] == "complete"


def test_model_installer_requires_confirmation_and_verifies_hash(tmp_path, monkeypatch):
    import pluribus.identity_models_install as module

    content = b"verified local model"
    spec = ModelSpec(
        filename="model.onnx",
        url="https://media.githubusercontent.com/model.onnx",
        sha256=hashlib.sha256(content).hexdigest(),
        byte_limit=100,
    )
    monkeypatch.setattr(module, "MODEL_SPECS", (spec,))
    calls = []

    def downloader(url, destination, byte_limit):
        calls.append((url, byte_limit))
        with open(destination, "wb") as handle:
            handle.write(content)

    installer = IdentityModelInstaller(str(tmp_path / "models"), downloader)
    with pytest.raises(ValueError, match="confirm"):
        installer.install("opencv-yunet-sface-v1", False)
    assert calls == []

    result = installer.install("opencv-yunet-sface-v1", True)

    assert result["state"] == "installed"
    assert calls == [(spec.url, 100)]
    assert oct(os.stat(tmp_path / "models" / "model.onnx").st_mode & 0o777) == "0o600"


def test_model_installer_discards_checksum_mismatch(tmp_path, monkeypatch):
    import pluribus.identity_models_install as module

    spec = ModelSpec(
        filename="model.onnx",
        url="https://media.githubusercontent.com/model.onnx",
        sha256=hashlib.sha256(b"expected").hexdigest(),
        byte_limit=100,
    )
    monkeypatch.setattr(module, "MODEL_SPECS", (spec,))

    def downloader(_url, destination, _byte_limit):
        with open(destination, "wb") as handle:
            handle.write(b"tampered")

    installer = IdentityModelInstaller(str(tmp_path / "models"), downloader)
    with pytest.raises(ValueError, match="Checksum"):
        installer.install("opencv-yunet-sface-v1", True)
    assert not (tmp_path / "models" / "model.onnx").exists()


def test_corrupt_model_files_never_report_ready_or_start_analysis(tmp_path):
    media = tmp_path / "input"
    media.mkdir()
    source = media / "lead.jpg"
    source.write_bytes(_image_bytes())
    installer = IdentityModelInstaller(str(tmp_path / "models"))
    os.makedirs(installer.model_dir)
    for path in installer.paths().values():
        with open(path, "wb") as handle:
            handle.write(b"not-a-verified-model")
    service = IdentityAnalysisService(
        str(tmp_path / "state"),
        media_roots=[str(media)],
        model_installer=installer,
    )

    assert service.capabilities()["state"] == "unavailable"
    assert service.capabilities()["modelBundle"]["installed"] is False

    async def scenario():
        started = await service.start_job(
            {"sources": [_source(str(source), "a" * 64)]}
        )
        return await _wait_for_terminal(service, started["jobId"])

    completed = asyncio.run(scenario())

    assert completed["state"] == "completed"
    assert {issue["code"] for issue in completed["issues"]} >= {
        "models_unverified"
    }
    assert completed["manualReviewRequired"] is True
    assert completed["manualReviewSources"][0]["sourceRef"] == "a" * 64
