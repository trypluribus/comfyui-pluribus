#!/usr/bin/env python3
"""Reverse-engineer local production assets from the finished Little Flower cut.

This does not claim that the film was generated with AI. It creates a local,
clearly-labelled reconstruction that can stand in for the pre-production
inputs an advanced ComfyUI graph might have used: shot manifests, storyboard
keyframes, identity-evidence sheets, location/prop boards, scene motion proxies,
and temp audio mixes.

The generated media lives under the repo's ignored ``outputs/`` directory so
film frames are not accidentally added to the public source tree.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from pluribus.identity_analyzers import (  # noqa: E402
    AnalyzedOccurrence,
    IdentityAnalyzer,
    OpenCVYuNetSFaceAnalyzer,
    cluster_occurrences,
    cosine_similarity,
    stable_occurrence_id,
)
from pluribus.identity_models import SourceRecord  # noqa: E402
from pluribus.identity_models_install import (  # noqa: E402
    MODEL_BUNDLE_ID,
    MODEL_SPECS,
    IdentityModelInstaller,
)


DEFAULT_SOURCE = Path.home() / "Downloads" / "Little Flower V04 Final Delivery 0213.mp4"
DEFAULT_OUTPUT = Path("outputs/little-flower-reconstruction")
SCENE_THRESHOLD = 0.20
FRAME_SIZE = (640, 360)
FONT_REGULAR = Path("/System/Library/Fonts/Supplemental/Arial.ttf")
FONT_BOLD = Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
COMFYUI_INPUT_PREFIX = "little_flower_reverse__"
BUILDER_VERSION = 3
MANIFEST_SCHEMA_VERSION = 2
FACE_MATCH_MARGIN = 0.04
PARTY_MAX_CANDIDATES = 16
BUILDER_FINGERPRINT_FILES = (
    ("tools/build_little_flower_reverse_assets.py", Path(__file__).resolve()),
    ("pluribus/identity_analyzers.py", PLUGIN_ROOT / "pluribus" / "identity_analyzers.py"),
    ("pluribus/identity_models.py", PLUGIN_ROOT / "pluribus" / "identity_models.py"),
    (
        "pluribus/identity_models_install.py",
        PLUGIN_ROOT / "pluribus" / "identity_models_install.py",
    ),
)

# The global detector misses intentional near-black cuts. These overrides come
# from the already hand-sliced opening nightmare and a dedicated low-threshold
# pass over the second nightmare. Scene boundaries are included explicitly so
# a long low-contrast dissolve cannot merge two production sequences.
MANUAL_CUT_OVERRIDES = (
    8.500,
    11.100,
    13.800,
    16.800,
    19.100,
    22.000,
    26.200,
    26.400,
    27.500,
    28.200,
    29.100,
    33.100,
    36.000,
    176.843,
    347.681,
    413.955,
    414.705958,
    417.500417,
    425.174750,
    427.760667,
    429.679250,
    433.099333,
    433.891792,
    435.852083,
    435.935500,
    437.812375,
    439.272167,
    439.856083,
    439.939500,
    443.443000,
    446.154042,
    456.581125,
    463.838,
    640.890,
    780.863,
    908.366,
)


@dataclass(frozen=True)
class Scene:
    scene_id: str
    slug: str
    title: str
    start: float
    end: float
    roles: tuple[str, ...]
    prompt: str


@dataclass(frozen=True)
class PrincipalSpec:
    slug: str
    role: str
    credited_performer: str
    anchor_time: float
    evidence_times: tuple[float, ...]
    minimum_scenes: int


SCENES = (
    Scene(
        "SC01",
        "nightmare_and_wake",
        "Nightmare and wake-up",
        0.0,
        36.0,
        ("Layla", "Nightmare Shadow"),
        "Cinematic nocturnal psychological drama. Layla sits at her vanity in a red shirt during a storm; a shadow presence approaches through macro inserts of toys, scissors and the mirror before she wakes abruptly. Deep blue night, warm practical lamp, slow dolly and sudden insert cuts.",
    ),
    Scene(
        "SC02",
        "morning_with_mama",
        "Morning conversation with Mama",
        36.0,
        176.843,
        ("Layla", "Mama"),
        "Naturalistic family drama. Layla in a red T-shirt recounts the dream to Mama in a beige hijab while they move between the kitchen and sitting area. Morning window light, intimate shot-reverse-shot, gentle handheld camera and restrained expressions.",
    ),
    Scene(
        "SC03",
        "dream_book_and_dalia",
        "Dream book and Dalia's visit",
        176.843,
        347.681,
        ("Layla", "Dalia"),
        "Warm domestic two-hander. Layla studies a dream-interpretation page before Dalia arrives; they drink tea at a kitchen table with oranges and discuss the dream. Maintain both faces, wardrobe continuity and eyelines across wides, singles and over-shoulders.",
    ),
    Scene(
        "SC04",
        "market_with_amo_hassan",
        "Market visit with Amo Hassan",
        347.681,
        413.955,
        ("Layla", "Amo Hassan"),
        "Observational market sequence. Layla wears a dark teal outer layer and moves through fluorescent grocery aisles while Amo Hassan works nearby. Slow tracking, shelf inserts, practical store light and grounded documentary motion.",
    ),
    Scene(
        "SC05",
        "second_nightmare",
        "Second nightmare",
        413.955,
        463.838,
        ("Layla", "Nightmare Shadow"),
        "A second nightmare in Layla's bedroom. Red and blue practical lighting, fragmented objects, oppressive shadow movement, close detail shots and an abrupt frightened wake-up. Preserve the same bedroom geography and character identity from the opening.",
    ),
    Scene(
        "SC06",
        "neighborhood_walk",
        "Neighborhood walk with Dalia",
        463.838,
        640.890,
        ("Layla", "Dalia"),
        "Day exterior dialogue scene. Layla in a red sweater walks through a suburban neighborhood with Dalia in a gray striped sweater; the conversation turns tense. Reactive handheld tracking, alternating profiles and close singles, overcast daylight.",
    ),
    Scene(
        "SC07",
        "mama_counsel",
        "Mama's counsel",
        640.890,
        780.863,
        ("Layla", "Mama"),
        "Intimate living-room conversation. Mama now wears a blue hijab and counsels Layla about dreams and faith. Calm locked compositions, soft window light, close shot-reverse-shot and gentle physical reassurance.",
    ),
    Scene(
        "SC08",
        "garden_party_fight",
        "Garden party and bounce-house fight",
        780.863,
        908.366,
        ("Layla", "Amo Hassan", "Featured Extras"),
        "Lively garden birthday party with food, balloons and a bounce house. Layla changes to a black top; Amo Hassan and Layla wear oversized inflatable boxing gloves while a large family crowd reacts. Escalating handheld coverage, crowd inserts, comedic action and continuity across intercut angles.",
    ),
)

PRINCIPALS = (
    PrincipalSpec(
        "layla",
        "Layla",
        "Nisreen Salem",
        203.5,
        (33.0, 49.0, 203.5, 356.0, 458.0, 545.0, 675.0, 850.0),
        4,
    ),
    PrincipalSpec(
        "dalia",
        "Dalia",
        "Newsha Sadri",
        213.5,
        (213.5, 218.5, 303.5, 338.5, 495.0, 565.0, 575.0, 595.0),
        2,
    ),
    PrincipalSpec(
        "amo_hassan",
        "Amo Hassan",
        "Salim Kassam",
        370.0,
        (370.0, 371.0, 372.0, 846.0, 854.0, 878.0, 890.0),
        2,
    ),
    PrincipalSpec(
        "mama",
        "Mama",
        "Sawsan Mustafa",
        53.5,
        (53.5, 73.5, 88.5, 128.5, 650.0, 700.0, 735.0, 760.0),
        2,
    ),
)

NIGHTMARE_SHADOW = {
    "slug": "nightmare_shadow",
    "role": "Nightmare Shadow",
    "credited_performer": "Anthony Egwu",
    "times": (16.8, 22.0, 427.8, 430.0, 433.4, 439.5),
}

# Sample broadly across the garden party. These frames are clustered by visual
# similarity only. Credited extra names are never assigned to the clusters.
PARTY_SAMPLE_TIMES = tuple(float(value) for value in range(784, 907, 4))

FEATURED_EXTRAS = (
    "Hanan Salem",
    "Haneen Salem",
    "Amani Salem",
    "Yasmine Hamdan",
    "Yousuf Hamdan",
    "Musa Hamdan",
    "Emad Salem",
    "Miki Das",
    "Shovna Tripathy",
    "Niranjan Tripathy",
    "Sara Ibrahim",
    "Hiba Chisti",
    "Jazmin Guevara",
    "Ashlerina Ortiz",
    "Emmanuel Hernandez",
)

LOCATION_TIMES = (
    ("Nightmare bedroom", 13.0),
    ("Morning home", 64.0),
    ("Dalia kitchen", 260.0),
    ("International food store", 369.0),
    ("Red-lit bedroom", 440.0),
    ("Neighborhood exterior", 520.0),
    ("Mama living room", 700.0),
    ("Garden party / bounce house", 850.0),
)

PROP_TIMES = (
    ("Vanity and storm window", 13.8),
    ("Scissors nightmare insert", 26.3),
    ("Dream-book research", 190.5),
    ("Tea and oranges", 260.0),
    ("Market shelves", 380.0),
    ("Red practical nightmare light", 438.0),
    ("Party food and balloons", 795.0),
    ("Inflatable boxing gloves", 875.0),
)


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def probe_duration(source: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def builder_fingerprint(
    files: Sequence[tuple[str, Path]] | None = None,
) -> str:
    """Hash every implementation file that can change generated evidence."""

    digest = hashlib.sha256()
    fingerprint_files = BUILDER_FINGERPRINT_FILES if files is None else files
    for logical_path, path in fingerprint_files:
        digest.update(logical_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def detect_cuts(source: Path, metadata_path: Path) -> list[tuple[float, float]]:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-an",
            "-vf",
            f"scale=320:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print:file={metadata_path}",
            "-fps_mode",
            "vfr",
            "-f",
            "null",
            "-",
        ]
    )
    return parse_cut_metadata(metadata_path)


def parse_cut_metadata(metadata_path: Path) -> list[tuple[float, float]]:
    lines = metadata_path.read_text().splitlines()
    cuts: list[tuple[float, float]] = []
    for index, line in enumerate(lines):
        timestamp_match = re.search(r"pts_time:([0-9.]+)", line)
        if not timestamp_match:
            continue
        score = 0.0
        if index + 1 < len(lines):
            score_match = re.search(r"lavfi.scene_score=([0-9.]+)", lines[index + 1])
            if score_match:
                score = float(score_match.group(1))
        cuts.append((float(timestamp_match.group(1)), score))
    return cuts


def add_manual_cut_overrides(cuts: list[tuple[float, float]], duration: float) -> list[tuple[float, float]]:
    merged = list(cuts)
    for timestamp in MANUAL_CUT_OVERRIDES:
        if not 0 < timestamp < duration:
            continue
        if any(abs(existing - timestamp) < 0.01 for existing, _score in merged):
            continue
        merged.append((timestamp, -1.0))
    return sorted(merged, key=lambda item: item[0])


def format_time(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{milliseconds:03d}"


def scene_for_time(timestamp: float) -> str:
    for scene in SCENES:
        if scene.start <= timestamp < scene.end:
            return scene.scene_id
    return "SC09_CREDITS"


def build_shots(cuts: list[tuple[float, float]], duration: float) -> list[dict[str, object]]:
    starts = [(0.0, None), *cuts]
    shots: list[dict[str, object]] = []
    for index, (start, score) in enumerate(starts, start=1):
        end = starts[index][0] if index < len(starts) else duration
        midpoint = start + ((end - start) / 2)
        shots.append(
            {
                "shot_id": f"SH{index:03d}",
                "scene_id": scene_for_time(midpoint),
                "start_seconds": round(start, 6),
                "end_seconds": round(end, 6),
                "duration_seconds": round(end - start, 6),
                "midpoint_seconds": round(midpoint, 6),
                "start_timecode": format_time(start),
                "end_timecode": format_time(end),
                "scene_score": None if score is None or score < 0 else round(score, 6),
                "cut_origin": (
                    "master_start"
                    if score is None
                    else "manual_dark_or_boundary_override"
                    if score < 0
                    else "automatic_scene_score"
                ),
            }
        )
    return shots


def extract_frame(source: Path, timestamp: float, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2:black",
            "-q:v",
            "2",
            "-y",
            str(target),
        ]
    )


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    path = FONT_BOLD if bold else FONT_REGULAR
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError:
        return ImageFont.load_default(size=max(10, size))


def make_sheet(
    title: str,
    entries: list[tuple[Path, str]],
    target: Path,
    columns: int = 4,
    subtitle: str | None = None,
) -> None:
    tile_width, tile_height = FRAME_SIZE
    label_height = 42
    header_height = 116 if subtitle else 78
    rows = math.ceil(len(entries) / columns)
    sheet = Image.new(
        "RGB",
        (columns * tile_width, header_height + rows * (tile_height + label_height)),
        "#111316",
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 16), title, fill="#f4efe5", font=font(34, bold=True))
    if subtitle:
        draw.text((24, 62), subtitle, fill="#c5c8cc", font=font(22))
    for index, (image_path, label) in enumerate(entries):
        row, column = divmod(index, columns)
        x = column * tile_width
        y = header_height + row * (tile_height + label_height)
        with Image.open(image_path) as frame:
            fitted = ImageOps.fit(frame.convert("RGB"), FRAME_SIZE, method=Image.Resampling.LANCZOS)
        sheet.paste(fitted, (x, y))
        draw.rectangle((x, y + tile_height, x + tile_width, y + tile_height + label_height), fill="#20242a")
        draw.text((x + 12, y + tile_height + 9), label, fill="#f4efe5", font=font(19, bold=True))
        draw.rectangle((x, y, x + tile_width - 1, y + tile_height + label_height - 1), outline="#484f59", width=2)
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def extract_identity_frame(source: Path, timestamp: float, target: Path) -> None:
    """Extract a higher-resolution frame for local face analysis."""
    target.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp:.6f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-vf",
            "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2:black",
            "-q:v",
            "2",
            "-y",
            str(target),
        ]
    )


def frame_source_ref(source_hash: str, timestamp: float) -> str:
    return hashlib.sha256(
        f"little-flower-frame:{source_hash}:{timestamp:.3f}".encode("utf-8")
    ).hexdigest()


def make_identity_sheet(
    title: str,
    subtitle: str,
    entries: Sequence[tuple[bytes, str]],
    target: Path,
) -> None:
    columns = 4
    tile_size = 320
    label_height = 70
    header_height = 124
    rows = max(1, math.ceil(len(entries) / columns))
    sheet = Image.new(
        "RGB",
        (columns * tile_size, header_height + rows * (tile_size + label_height)),
        "#111316",
    )
    draw = ImageDraw.Draw(sheet)
    draw.text((24, 14), title, fill="#f4efe5", font=font(30, bold=True))
    draw.text((24, 58), subtitle, fill="#e28a2b", font=font(18, bold=True))
    draw.text(
        (24, 88),
        "Finished-film evidence · identity and rights require producer confirmation",
        fill="#c5c8cc",
        font=font(17),
    )
    for index, (crop_bytes, label) in enumerate(entries):
        row, column = divmod(index, columns)
        x = column * tile_size
        y = header_height + row * (tile_size + label_height)
        with Image.open(io.BytesIO(crop_bytes)) as crop:
            fitted = ImageOps.fit(
                crop.convert("RGB"),
                (tile_size, tile_size),
                method=Image.Resampling.LANCZOS,
            )
        sheet.paste(fitted, (x, y))
        draw.rectangle(
            (x, y + tile_size, x + tile_size, y + tile_size + label_height),
            fill="#20242a",
        )
        draw.multiline_text(
            (x + 10, y + tile_size + 8),
            label,
            fill="#f4efe5",
            font=font(16, bold=True),
            spacing=3,
        )
        draw.rectangle(
            (x, y, x + tile_size - 1, y + tile_size + label_height - 1),
            outline="#484f59",
            width=2,
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target, optimize=True)


def _largest_face(values: Sequence[AnalyzedOccurrence]) -> AnalyzedOccurrence:
    if not values:
        raise ValueError("No face was detected in a curated identity anchor frame.")
    return max(
        values,
        key=lambda value: (
            value.bbox[2] * value.bbox[3],
            value.confidence,
            value.bbox,
        ),
    )


def _default_clusterer(
    values: Sequence[AnalyzedOccurrence], model_version: str, threshold: float
) -> tuple[list[dict], set[str]]:
    # Use the same conservative clustering implementation as the in-plugin
    # identity review job. It deliberately never merges simultaneous faces.
    return cluster_occurrences(values, model_version, threshold)


def _occurrence_record(
    occurrence: AnalyzedOccurrence,
    timestamp: float,
    *,
    similarity: float | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "scene_id": scene_for_time(timestamp),
        "timestamp_seconds": round(timestamp, 3),
        "timecode": format_time(timestamp),
        "bbox": list(occurrence.bbox),
        "detector_confidence": round(float(occurrence.confidence), 6),
        "source_frame_ref": occurrence.source_ref,
    }
    if similarity is not None:
        record["anchor_similarity"] = round(float(similarity), 6)
    return record


def _write_crop(path: Path, crop_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(crop_bytes)


def filename_slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _principal_evidence(
    detections_by_time: dict[float, list[AnalyzedOccurrence]],
    specs: Sequence[PrincipalSpec],
    threshold: float,
) -> tuple[
    dict[str, list[tuple[AnalyzedOccurrence, float, float]]],
    dict[str, AnalyzedOccurrence],
]:
    seeds: dict[str, AnalyzedOccurrence] = {}
    for spec in specs:
        try:
            seeds[spec.slug] = _largest_face(detections_by_time.get(spec.anchor_time, ()))
        except ValueError as error:
            raise SystemExit(
                f"Could not build {spec.role} evidence: {error} "
                f"Anchor time {format_time(spec.anchor_time)} needs a clear face."
            ) from error

    resolved: dict[str, list[tuple[AnalyzedOccurrence, float, float]]] = {
        spec.slug: [] for spec in specs
    }
    for spec in specs:
        for timestamp in spec.evidence_times:
            detections = detections_by_time.get(timestamp, ())
            if not detections:
                continue
            ranked: list[tuple[float, AnalyzedOccurrence]] = sorted(
                (
                    (cosine_similarity(value.embedding, seeds[spec.slug].embedding), value)
                    for value in detections
                ),
                key=lambda item: (-item[0], item[1].bbox),
            )
            score, occurrence = ranked[0]
            other_scores = sorted(
                (
                    cosine_similarity(occurrence.embedding, seed.embedding)
                    for slug, seed in seeds.items()
                    if slug != spec.slug
                ),
                reverse=True,
            )
            margin = score - (other_scores[0] if other_scores else -1.0)
            is_anchor = timestamp == spec.anchor_time and occurrence is seeds[spec.slug]
            if is_anchor or (score >= threshold and margin >= FACE_MATCH_MARGIN):
                resolved[spec.slug].append((occurrence, timestamp, score))

        scene_count = len(
            {scene_for_time(timestamp) for _occurrence, timestamp, _score in resolved[spec.slug]}
        )
        if scene_count < spec.minimum_scenes:
            raise SystemExit(
                f"{spec.role} evidence resolved across only {scene_count} scene(s); "
                f"at least {spec.minimum_scenes} are required. Review anchor/evidence times "
                "instead of emitting a potentially mixed character sheet."
            )
    return resolved, seeds


def _is_principal_face(
    occurrence: AnalyzedOccurrence,
    seeds: dict[str, AnalyzedOccurrence],
    threshold: float,
) -> bool:
    return any(
        cosine_similarity(occurrence.embedding, seed.embedding) >= threshold
        for seed in seeds.values()
    )


def build_character_sheets(
    source: Path,
    output: Path,
    analyzer: IdentityAnalyzer,
    source_hash: str,
    *,
    specs: Sequence[PrincipalSpec] = PRINCIPALS,
    party_times: Sequence[float] = PARTY_SAMPLE_TIMES,
    shadow_spec: dict[str, object] = NIGHTMARE_SHADOW,
    clusterer: Callable[
        [Sequence[AnalyzedOccurrence], str, float], tuple[list[dict], set[str]]
    ] = _default_clusterer,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Build real-face evidence sheets without asserting legal identity.

    Curated single-person frames seed four role suggestions. Other occurrences
    must clear the same local SFace threshold and a cross-principal margin.
    Party faces are clustered anonymously and are never mapped to credit names.
    """
    status = analyzer.status()
    if not status.available:
        details = "; ".join(str(issue.get("title") or issue) for issue in status.issues)
        raise SystemExit(
            "Local identity analysis is unavailable. Install the verified YuNet/SFace "
            f"models and OpenCV before rebuilding evidence. {details}"
        )

    all_times = sorted(
        {
            *(timestamp for spec in specs for timestamp in spec.evidence_times),
            *party_times,
        }
    )
    frame_dir = output / "analysis" / "identity_frames"
    source_by_ref: dict[str, float] = {}
    sources: list[SourceRecord] = []
    for timestamp in all_times:
        source_ref = frame_source_ref(source_hash, timestamp)
        path = frame_dir / f"frame_{int(round(timestamp * 1000)):09d}.jpg"
        extract_identity_frame(source, timestamp, path)
        source_by_ref[source_ref] = timestamp
        sources.append(
            SourceRecord(
                source_ref=source_ref,
                media_type="image",
                source_hash=sha256_file(path),
                local_path=str(path),
                display_label=f"{scene_for_time(timestamp)} @ {format_time(timestamp)}",
                byte_size=path.stat().st_size,
            )
        )

    analyzed = analyzer.analyze(
        sources,
        threading.Event(),
        lambda _completed, _total, _source_ref: None,
    )
    detections_by_time: dict[float, list[AnalyzedOccurrence]] = {}
    for occurrence in analyzed:
        timestamp = source_by_ref.get(occurrence.source_ref)
        if timestamp is not None:
            detections_by_time.setdefault(timestamp, []).append(occurrence)

    threshold = float(getattr(analyzer, "similarity_threshold", 0.38))
    resolved, seeds = _principal_evidence(detections_by_time, specs, threshold)
    records: list[dict[str, object]] = []
    crop_dir = output / "analysis" / "identity_crops"
    for spec in specs:
        occurrences = sorted(resolved[spec.slug], key=lambda item: item[1])
        sheet_entries: list[tuple[bytes, str]] = []
        occurrence_records: list[dict[str, object]] = []
        for index, (occurrence, timestamp, score) in enumerate(occurrences, start=1):
            crop_path = crop_dir / spec.slug / f"occurrence_{index:02d}.jpg"
            _write_crop(crop_path, occurrence.crop_bytes)
            sheet_entries.append(
                (
                    occurrence.crop_bytes,
                    f"{scene_for_time(timestamp)} · {format_time(timestamp)}\nmatch {score:.2f} · detect {occurrence.confidence:.2f}",
                )
            )
            occurrence_records.append(
                _occurrence_record(occurrence, timestamp, similarity=score)
            )
        performer_slug = filename_slug(spec.credited_performer)
        sheet_path = (
            output
            / "characters"
            / f"{performer_slug}_as_{spec.slug}_identity_evidence.png"
        )
        make_identity_sheet(
            f"IDENTITY EVIDENCE · {spec.role}",
            f"Suggested role label from curated anchor · credited performer: {spec.credited_performer}",
            sheet_entries,
            sheet_path,
        )
        records.append(
            {
                "role": spec.role,
                "credited_performer": spec.credited_performer,
                "asset": str(sheet_path.relative_to(output)),
                "asset_sha256": sha256_file(sheet_path),
                "evidence_type": "face_crop_occurrences",
                "identity_state": "suggested_role_mapping_needs_producer_confirmation",
                "mapping_basis": "curated role anchor plus within-film SFace similarity",
                "anchor_timestamp_seconds": spec.anchor_time,
                "occurrences": occurrence_records,
                "rights_or_clearance_asserted": False,
            }
        )

    shadow_entries: list[tuple[Path, str]] = []
    shadow_occurrences: list[dict[str, object]] = []
    for index, timestamp in enumerate(shadow_spec["times"], start=1):
        frame_path = frame_dir / "nightmare_shadow" / f"body_{index:02d}.jpg"
        extract_identity_frame(source, float(timestamp), frame_path)
        label = f"BODY {index:02d} · {scene_for_time(float(timestamp))} · {format_time(float(timestamp))}"
        shadow_entries.append((frame_path, label))
        shadow_occurrences.append(
            {
                "scene_id": scene_for_time(float(timestamp)),
                "timestamp_seconds": float(timestamp),
                "timecode": format_time(float(timestamp)),
                "evidence_scope": "full_body_or_silhouette",
            }
        )
    shadow_path = output / "characters" / "nightmare_shadow_body_evidence.png"
    make_sheet(
        "FULL-BODY EVIDENCE · NIGHTMARE SHADOW",
        shadow_entries,
        shadow_path,
        columns=3,
        subtitle="Face recognition not used · role-context suggestion requires producer confirmation",
    )
    records.append(
        {
            "role": shadow_spec["role"],
            "credited_performer": shadow_spec["credited_performer"],
            "asset": str(shadow_path.relative_to(output)),
            "asset_sha256": sha256_file(shadow_path),
            "evidence_type": "full_body_role_context",
            "identity_state": "manual_role_context_needs_producer_confirmation",
            "mapping_basis": "curated silhouette and full-body scene frames; no face match",
            "occurrences": shadow_occurrences,
            "rights_or_clearance_asserted": False,
        }
    )

    party_values = [
        occurrence
        for timestamp in party_times
        for occurrence in detections_by_time.get(float(timestamp), ())
        if not _is_principal_face(occurrence, seeds, threshold)
    ]
    clusters, ambiguity = clusterer(party_values, status.model_version, threshold)
    ranked_clusters = sorted(
        clusters,
        key=lambda cluster: (
            -len(cluster["items"]),
            -float(cluster["confidence"]),
            cluster["candidateId"],
        ),
    )[:PARTY_MAX_CANDIDATES]
    if not ranked_clusters:
        raise SystemExit(
            "No anonymous party face candidates survived principal exclusion. "
            "Review party sampling before emitting an ensemble placeholder."
        )
    for index, cluster in enumerate(ranked_clusters, start=1):
        candidate_id = str(cluster["candidateId"])
        items = list(cluster["items"])
        items.sort(key=lambda item: source_by_ref[item.source_ref])
        selected_items = items[:8]
        sheet_entries = []
        occurrence_records = []
        for item_index, occurrence in enumerate(selected_items, start=1):
            timestamp = source_by_ref[occurrence.source_ref]
            crop_path = (
                crop_dir
                / "party_candidates"
                / candidate_id
                / f"occurrence_{item_index:02d}.jpg"
            )
            _write_crop(crop_path, occurrence.crop_bytes)
            sheet_entries.append(
                (
                    occurrence.crop_bytes,
                    f"SC08 · {format_time(timestamp)}\ndetect {occurrence.confidence:.2f}",
                )
            )
            record = _occurrence_record(occurrence, timestamp)
            record["ambiguous_cluster_assignment"] = (
                stable_occurrence_id(occurrence, status.model_version) in ambiguity
            )
            occurrence_records.append(record)
        sheet_path = (
            output
            / "characters"
            / f"party_visual_candidate_{index:02d}_{candidate_id[:8]}.png"
        )
        make_identity_sheet(
            f"PARTY VISUAL CANDIDATE {index:02d}",
            "Anonymous within-film cluster · NOT mapped to any credited extra name",
            sheet_entries,
            sheet_path,
        )
        records.append(
            {
                "role": f"Party visual candidate {index:02d}",
                "credited_performer": None,
                "asset": str(sheet_path.relative_to(output)),
                "asset_sha256": sha256_file(sheet_path),
                "evidence_type": "anonymous_face_cluster",
                "identity_state": "anonymous_visual_candidate_needs_producer_confirmation",
                "candidate_id": candidate_id,
                "cluster_confidence": round(float(cluster["confidence"]), 6),
                "cluster_has_ambiguous_assignments": any(
                    stable_occurrence_id(item, status.model_version) in ambiguity
                    for item in items
                ),
                "mapping_basis": "within-SC08 SFace similarity after principal exclusion",
                "credited_name_mapping": None,
                "occurrences": occurrence_records,
                "rights_or_clearance_asserted": False,
            }
        )

    identity_method = {
        "analyzer_id": status.analyzer_id,
        "model_version": status.model_version,
        "model_bundle_id": MODEL_BUNDLE_ID,
        "similarity_threshold": threshold,
        "cross_principal_margin": FACE_MATCH_MARGIN,
        "detector_and_embedding_method": "OpenCV YuNet face detection plus SFace embeddings",
        "party_clustering_method": "project-scoped centroid clustering with simultaneous-face cannot-link",
        "face_embeddings_persisted": False,
        "open_world_identity_recognition": False,
        "producer_confirmation_required": True,
        "rights_or_clearance_inferred": False,
        "model_files": [
            {"filename": spec.filename, "sha256": spec.sha256}
            for spec in MODEL_SPECS
        ],
    }
    return records, identity_method


def build_reference_sheet(
    source: Path,
    output: Path,
    slug: str,
    title: str,
    timed_entries: tuple[tuple[str, float], ...],
) -> str:
    frame_dir = output / "analysis" / f"{slug}_frames"
    entries: list[tuple[Path, str]] = []
    for index, (label, timestamp) in enumerate(timed_entries, start=1):
        frame_path = frame_dir / f"reference_{index:02d}.jpg"
        extract_frame(source, timestamp, frame_path)
        entries.append((frame_path, f"{label}  |  {format_time(timestamp)}"))
    target = output / "references" / f"{slug}.png"
    make_sheet(title, entries, target, columns=4, subtitle="Reconstructed visual-development reference")
    return str(target.relative_to(output))


def build_storyboards(source: Path, output: Path, shots: list[dict[str, object]]) -> list[dict[str, object]]:
    storyboard_records: list[dict[str, object]] = []
    for scene in SCENES:
        scene_shots = [shot for shot in shots if shot["scene_id"] == scene.scene_id]
        entries: list[tuple[Path, str]] = []
        for shot in scene_shots:
            frame_path = output / "storyboards" / "keyframes" / scene.scene_id / f"{shot['shot_id']}.jpg"
            extract_frame(source, float(shot["midpoint_seconds"]), frame_path)
            entries.append(
                (
                    frame_path,
                    f"{shot['shot_id']}  |  {shot['start_timecode']}  |  {float(shot['duration_seconds']):.2f}s",
                )
            )
        sheet_path = output / "storyboards" / f"{scene.scene_id}_{scene.slug}_storyboard.png"
        make_sheet(
            f"{scene.scene_id}  ·  {scene.title.upper()}",
            entries,
            sheet_path,
            columns=4,
            subtitle=f"{len(scene_shots)} detected shots  |  roles: {', '.join(scene.roles)}",
        )
        storyboard_records.append(
            {
                "scene_id": scene.scene_id,
                "title": scene.title,
                "asset": str(sheet_path.relative_to(output)),
                "shot_count": len(scene_shots),
            }
        )
    return storyboard_records


def extract_scene_media(source: Path, output: Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for scene in SCENES:
        duration = scene.end - scene.start
        audio_path = output / "audio" / f"{scene.scene_id}_{scene.slug}_temp_mix.m4a"
        video_path = output / "motion" / f"{scene.scene_id}_{scene.slug}_motion_proxy.mp4"
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.parent.mkdir(parents=True, exist_ok=True)
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{scene.start:.6f}",
                "-t",
                f"{duration:.6f}",
                "-i",
                str(source),
                "-vn",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-y",
                str(audio_path),
            ]
        )
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{scene.start:.6f}",
                "-t",
                f"{duration:.6f}",
                "-i",
                str(source),
                "-vf",
                "scale=960:-2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "25",
                "-c:a",
                "aac",
                "-b:a",
                "96k",
                "-movflags",
                "+faststart",
                "-y",
                str(video_path),
            ]
        )
        records.append(
            {
                "scene_id": scene.scene_id,
                "temp_audio": str(audio_path.relative_to(output)),
                "motion_proxy": str(video_path.relative_to(output)),
            }
        )
    return records


def write_manifests(
    source: Path,
    source_hash: str,
    output: Path,
    duration: float,
    shots: list[dict[str, object]],
    character_records: list[dict[str, object]],
    storyboard_records: list[dict[str, object]],
    scene_media: list[dict[str, object]],
    location_asset: str,
    prop_asset: str,
    identity_method: dict[str, object],
) -> None:
    analysis_dir = output / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    csv_path = analysis_dir / "shot_manifest.csv"
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(shots[0].keys()))
        writer.writeheader()
        writer.writerows(shots)
    (analysis_dir / "shot_manifest.json").write_text(json.dumps(shots, indent=2) + "\n")
    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "artifact_type": "reverse_engineered_preproduction_reconstruction",
        "source_master": str(source),
        "source_master_sha256": source_hash,
        "source_duration_seconds": duration,
        "builder_version": BUILDER_VERSION,
        "builder_sha256": builder_fingerprint(),
        "scene_detection_threshold": SCENE_THRESHOLD,
        "candidate_shot_count": len(shots),
        "ownership_context": "User states they executive-produced and own the finished short film. This local manifest does not independently verify chain of title or performer agreements.",
        "method_note": "All visual and motion assets below were reconstructed from the finished delivery for a Pluribus workflow test; they are not asserted to be the film's original pre-production materials.",
        "rights_note": "Identity suggestions and evidence assets do not establish performer identity, consent, ownership, contract scope, or clearance. Producer review is required before any use decision.",
        "identity_evidence_method": identity_method,
        "cast": character_records,
        "featured_extras": list(FEATURED_EXTRAS),
        "featured_extra_credits": {
            "names": list(FEATURED_EXTRAS),
            "mapped_to_visual_candidates": False,
            "note": "Names are transcribed from film credits and are intentionally not assigned to anonymous SC08 face clusters.",
        },
        "scenes": [
            {
                "scene_id": scene.scene_id,
                "slug": scene.slug,
                "title": scene.title,
                "start_seconds": scene.start,
                "end_seconds": scene.end,
                "roles": list(scene.roles),
                "generation_prompt": scene.prompt,
            }
            for scene in SCENES
        ],
        "storyboards": storyboard_records,
        "scene_media": scene_media,
        "location_reference": location_asset,
        "prop_reference": prop_asset,
    }
    relative_paths = workflow_input_paths(manifest)
    manifest["workflow_input_count"] = len(relative_paths)
    manifest["workflow_inputs"] = [
        {
            "asset": relative_path,
            "sha256": sha256_file(output / relative_path),
        }
        for relative_path in relative_paths
    ]
    (output / "reconstruction_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def workflow_input_paths(manifest: dict[str, object]) -> list[str]:
    relative_paths = [str(record["asset"]) for record in manifest["cast"]]
    relative_paths.extend(
        (str(manifest["location_reference"]), str(manifest["prop_reference"]))
    )
    relative_paths.extend(str(record["asset"]) for record in manifest["storyboards"])
    relative_paths.extend(
        str(record["motion_proxy"]) for record in manifest["scene_media"]
    )
    relative_paths.extend(
        str(record["temp_audio"]) for record in manifest["scene_media"]
    )
    return relative_paths


def comfyui_input_name(relative_path: str) -> str:
    """Return a collision-resistant filename that ComfyUI can list at input root."""
    return f"{COMFYUI_INPUT_PREFIX}{Path(relative_path).name}"


def package_comfyui_inputs(output: Path) -> list[Path]:
    """Validate and stage the manifest's exact graph input inventory."""
    manifest_path = output / "reconstruction_manifest.json"
    if not manifest_path.is_file():
        raise SystemExit(f"reconstruction manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("artifact_type") != "reverse_engineered_preproduction_reconstruction":
        raise SystemExit("reconstruction manifest has an unexpected artifact type")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise SystemExit(
            "reconstruction manifest schema is stale; rebuild media before packaging "
            f"(manifest={manifest.get('schema_version') or 'missing'}, current={MANIFEST_SCHEMA_VERSION})"
        )
    if manifest.get("builder_version") != BUILDER_VERSION:
        raise SystemExit(
            "reconstruction builder version is stale; rebuild media before packaging"
        )
    expected_builder = manifest.get("builder_sha256")
    current_builder = builder_fingerprint()
    if expected_builder != current_builder:
        raise SystemExit(
            "reconstruction assets are stale for this builder; rebuild media before packaging "
            f"(manifest={expected_builder or 'missing'}, current={current_builder})"
        )
    source_master = Path(str(manifest.get("source_master") or "")).expanduser()
    expected_source = str(manifest.get("source_master_sha256") or "")
    if not source_master.is_file() or not expected_source:
        raise SystemExit("reconstruction manifest is missing a verifiable source master")
    current_source = sha256_file(source_master)
    if current_source != expected_source:
        raise SystemExit(
            "reconstruction source master has changed; rebuild media before packaging"
        )
    inventory = manifest.get("workflow_inputs")
    if not isinstance(inventory, list) or not inventory:
        raise SystemExit("reconstruction manifest is missing its workflow input inventory")
    relative_paths = [str(record.get("asset") or "") for record in inventory if isinstance(record, dict)]
    if len(relative_paths) != len(inventory) or len(relative_paths) != len(set(relative_paths)):
        raise SystemExit(
            "reconstruction workflow input inventory contains malformed or duplicate entries"
        )
    if manifest.get("workflow_input_count") != len(relative_paths):
        raise SystemExit("reconstruction workflow input count does not match its inventory")

    package_dir = output / "comfyui_input"
    package_dir.mkdir(parents=True, exist_ok=True)
    packaged: list[Path] = []
    expected_packaged_names = {comfyui_input_name(path) for path in relative_paths}
    if len(expected_packaged_names) != len(relative_paths):
        raise SystemExit(
            "workflow inputs collide after flattening; rename assets before packaging"
        )
    for record, relative_path in zip(inventory, relative_paths):
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or not relative_path:
            raise SystemExit(f"workflow input has an unsafe relative path: {relative_path!r}")
        source = output / relative
        if not source.is_file():
            raise SystemExit(f"workflow input not found: {source}")
        expected_hash = str(record.get("sha256") or "")
        if not expected_hash or sha256_file(source) != expected_hash:
            raise SystemExit(
                f"workflow input changed after manifest creation: {relative_path}; rebuild media"
            )
        destination = package_dir / comfyui_input_name(relative_path)
        shutil.copy2(source, destination)
        packaged.append(destination)
    for stale in package_dir.iterdir():
        if (
            stale.is_file()
            and stale.name.startswith(COMFYUI_INPUT_PREFIX)
            and stale.name not in expected_packaged_names
        ):
            stale.unlink()
    package_manifest = {
        "schema_version": 1,
        "reconstruction_manifest_sha256": sha256_file(manifest_path),
        "input_count": len(packaged),
        "files": [path.name for path in packaged],
    }
    (package_dir / "package_manifest.json").write_text(
        json.dumps(package_manifest, indent=2) + "\n", encoding="utf-8"
    )
    return packaged


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=(
            Path(os.environ["PLURIBUS_IDENTITY_MODEL_DIR"])
            if os.environ.get("PLURIBUS_IDENTITY_MODEL_DIR")
            else None
        ),
        help=(
            "Directory containing the checksum-verified YuNet and SFace ONNX files. "
            "May also be set with PLURIBUS_IDENTITY_MODEL_DIR."
        ),
    )
    parser.add_argument("--reuse-scene-metadata", action="store_true")
    parser.add_argument(
        "--package-only",
        action="store_true",
        help="Validate and stage existing manifest assets as flat ComfyUI input files.",
    )
    args = parser.parse_args()

    output = args.output.expanduser().resolve()
    if args.package_only:
        packaged = package_comfyui_inputs(output)
        print(f"packaged {len(packaged)} flat ComfyUI inputs in {output / 'comfyui_input'}")
        return

    source = args.source.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"source film not found: {source}")
    if args.model_dir is None:
        raise SystemExit(
            "--model-dir is required for an evidence rebuild (or set "
            "PLURIBUS_IDENTITY_MODEL_DIR). The builder never downloads models implicitly."
        )
    model_dir = args.model_dir.expanduser().resolve()
    installer = IdentityModelInstaller(str(model_dir))
    model_status = installer.status()
    if not model_status["installed"]:
        missing = ", ".join(
            record["filename"]
            for record in model_status["files"]
            if not record["installed"]
        )
        raise SystemExit(
            f"Verified identity models are not installed in {model_dir}: {missing}. "
            "Install them explicitly through the Pluribus model installer first."
        )
    model_paths = installer.paths()
    analyzer = OpenCVYuNetSFaceAnalyzer(model_paths["yunet"], model_paths["sface"])
    output.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(source)
    metadata_path = output / "analysis" / "scene-metadata.txt"
    if args.reuse_scene_metadata and metadata_path.is_file():
        cuts = parse_cut_metadata(metadata_path)
    else:
        cuts = detect_cuts(source, metadata_path)
    cuts = add_manual_cut_overrides(cuts, duration)
    shots = build_shots(cuts, duration)
    source_hash = sha256_file(source)
    character_records, identity_method = build_character_sheets(
        source, output, analyzer, source_hash
    )
    location_asset = build_reference_sheet(source, output, "location_bible", "LOCATION BIBLE", LOCATION_TIMES)
    prop_asset = build_reference_sheet(source, output, "prop_and_wardrobe_bible", "PROP + WARDROBE BIBLE", PROP_TIMES)
    storyboard_records = build_storyboards(source, output, shots)
    scene_media = extract_scene_media(source, output)
    write_manifests(
        source,
        source_hash,
        output,
        duration,
        shots,
        character_records,
        storyboard_records,
        scene_media,
        location_asset,
        prop_asset,
        identity_method,
    )
    packaged = package_comfyui_inputs(output)
    print(
        f"built {len(shots)} candidate shots, {len(character_records)} identity evidence sheets, "
        f"{len(storyboard_records)} storyboards, {len(scene_media)} scene media bundles, "
        f"and {len(packaged)} flat ComfyUI inputs in {output}"
    )


if __name__ == "__main__":
    main()
