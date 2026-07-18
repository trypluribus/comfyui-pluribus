import hashlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


builder = _load("little_flower_builder", "build_little_flower_reverse_assets.py")
generator = _load("little_flower_generator", "gen_little_flower_reverse_workflow.py")
from pluribus.identity_analyzers import AnalyzerStatus  # noqa: E402


def _crop_bytes(color):
    output = io.BytesIO()
    Image.new("RGB", (80, 80), color).save(output, format="JPEG", quality=90)
    return output.getvalue()


class FakeEvidenceAnalyzer:
    analyzer_id = "fake_yunet_sface"
    model_version = "fake-model-v1"
    similarity_threshold = 0.38

    def status(self):
        return AnalyzerStatus(True, self.analyzer_id, self.model_version)

    def analyze(self, sources, _cancel_event, progress):
        values = []
        for index, source in enumerate(sources):
            progress(index, len(sources), source.source_ref)
            timestamp = int(Path(source.local_path).stem.split("_")[-1]) / 1000
            if timestamp in {1.0, 40.0}:
                faces = [((1.0, 0.0, 0.0), (0, 0, 50, 50), (210, 80, 60))]
            elif timestamp in {50.0, 180.0}:
                faces = [((0.0, 1.0, 0.0), (0, 0, 50, 50), (70, 180, 210))]
            elif timestamp in {790.0, 794.0}:
                faces = [
                    ((0.0, 0.0, 1.0), (0, 0, 40, 40), (80, 210, 80)),
                    ((-1.0, 0.0, 0.0), (45, 0, 35, 35), (210, 180, 70)),
                ]
            else:
                faces = []
            for embedding, bbox, color in faces:
                values.append(
                    builder.AnalyzedOccurrence(
                        source.source_ref,
                        "image",
                        0,
                        0,
                        bbox,
                        0.95,
                        embedding,
                        _crop_bytes(color),
                    )
                )
        progress(len(sources), len(sources), "")
        return values


def test_identity_evidence_uses_crops_and_keeps_party_candidates_anonymous(
    tmp_path, monkeypatch
):
    source = tmp_path / "master.mp4"
    source.write_bytes(b"owned-test-master")
    output = tmp_path / "output"

    def fake_extract(_source, _timestamp, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (128, 72), (35, 40, 45)).save(target, format="JPEG")

    monkeypatch.setattr(builder, "extract_identity_frame", fake_extract)
    specs = (
        builder.PrincipalSpec("a", "Principal A", "Credit A", 1.0, (1.0, 40.0), 2),
        builder.PrincipalSpec("b", "Principal B", "Credit B", 50.0, (50.0, 180.0), 2),
    )
    shadow = {
        "slug": "shadow",
        "role": "Shadow",
        "credited_performer": "Credit Shadow",
        "times": (2.0, 420.0),
    }

    records, method = builder.build_character_sheets(
        source,
        output,
        FakeEvidenceAnalyzer(),
        hashlib.sha256(source.read_bytes()).hexdigest(),
        specs=specs,
        party_times=(790.0, 794.0),
        shadow_spec=shadow,
    )

    principals = [record for record in records if record["evidence_type"] == "face_crop_occurrences"]
    candidates = [record for record in records if record["evidence_type"] == "anonymous_face_cluster"]
    assert len(principals) == 2
    assert {record["asset"] for record in principals} == {
        "characters/credit_a_as_a_identity_evidence.png",
        "characters/credit_b_as_b_identity_evidence.png",
    }
    assert all(len({item["scene_id"] for item in record["occurrences"]}) == 2 for record in principals)
    assert all((output / record["asset"]).is_file() for record in records)
    assert len(candidates) == 2
    assert all(record["credited_performer"] is None for record in candidates)
    assert all(record["credited_name_mapping"] is None for record in candidates)
    assert method["face_embeddings_persisted"] is False
    assert method["rights_or_clearance_inferred"] is False


def test_package_only_validates_dynamic_inventory_hashes_and_cleans_stale_files(tmp_path):
    output = tmp_path / "output"
    source = tmp_path / "master.mp4"
    source.write_bytes(b"master")
    first = output / "characters" / "person.png"
    second = output / "audio" / "scene.m4a"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"person")
    second.write_bytes(b"audio")
    inventory = [
        {"asset": "characters/person.png", "sha256": builder.sha256_file(first)},
        {"asset": "audio/scene.m4a", "sha256": builder.sha256_file(second)},
    ]
    manifest = {
        "schema_version": builder.MANIFEST_SCHEMA_VERSION,
        "artifact_type": "reverse_engineered_preproduction_reconstruction",
        "builder_version": builder.BUILDER_VERSION,
        "builder_sha256": builder.builder_fingerprint(),
        "source_master": str(source),
        "source_master_sha256": builder.sha256_file(source),
        "workflow_input_count": len(inventory),
        "workflow_inputs": inventory,
    }
    output.mkdir(exist_ok=True)
    (output / "reconstruction_manifest.json").write_text(json.dumps(manifest))
    package_dir = output / "comfyui_input"
    package_dir.mkdir()
    stale = package_dir / f"{builder.COMFYUI_INPUT_PREFIX}stale.png"
    stale.write_bytes(b"stale")

    packaged = builder.package_comfyui_inputs(output)

    assert len(packaged) == 2
    assert not stale.exists()
    assert json.loads((package_dir / "package_manifest.json").read_text())["input_count"] == 2

    first.write_bytes(b"mutated")
    with pytest.raises(SystemExit, match="changed after manifest"):
        builder.package_comfyui_inputs(output)


def test_builder_fingerprint_tracks_analyzer_and_model_implementations(tmp_path):
    files = []
    for logical_path in (
        "tools/build_little_flower_reverse_assets.py",
        "pluribus/identity_analyzers.py",
        "pluribus/identity_models.py",
        "pluribus/identity_models_install.py",
    ):
        path = tmp_path / Path(logical_path).name
        path.write_text(f"implementation for {logical_path}\n")
        files.append((logical_path, path))

    baseline = builder.builder_fingerprint(files)
    files[1][1].write_text("changed analyzer implementation\n")

    assert builder.builder_fingerprint(files) != baseline


def test_committed_manifest_reproduces_fixtures_without_private_media():
    manifest = generator.load_manifest()
    assert generator.DEFAULT_MANIFEST.parent == generator.FIXTURES
    assert {
        "source_master",
        "source_master_sha256",
        "builder_sha256",
        "ownership_context",
        "identity_evidence_method",
        "workflow_inputs",
    }.isdisjoint(manifest)
    assert len(generator.manifest_input_assets(manifest)) == 43

    ui, api = generator.build_fixture_documents(manifest)

    assert generator.fixture_text(ui) == (
        generator.FIXTURES / generator.UI_FIXTURE_NAME
    ).read_text()
    assert generator.fixture_text(api) == (
        generator.FIXTURES / generator.API_FIXTURE_NAME
    ).read_text()


def test_fixture_check_reports_stale_files_without_mutating_them(tmp_path):
    manifest = generator.load_manifest()
    generator.sync_fixture_documents(manifest, tmp_path, check=False)
    ui_path = tmp_path / generator.UI_FIXTURE_NAME
    ui_path.write_text("stale fixture\n")

    with pytest.raises(SystemExit, match=generator.UI_FIXTURE_NAME):
        generator.sync_fixture_documents(manifest, tmp_path, check=True)

    assert ui_path.read_text() == "stale fixture\n"


def test_generator_wires_every_anonymous_party_candidate_through_crowd_batches():
    cast = [
        {"role": "Layla", "asset": "characters/layla.png", "evidence_type": "face_crop_occurrences"},
        {"role": "Amo Hassan", "asset": "characters/amo.png", "evidence_type": "face_crop_occurrences"},
        {"role": "Nightmare Shadow", "asset": "characters/shadow.png", "evidence_type": "full_body_role_context"},
    ]
    cast.extend(
        {
            "role": f"Party visual candidate {index:02d}",
            "asset": f"characters/candidate_{index:02d}.png",
            "evidence_type": "anonymous_face_cluster",
            "identity_state": "anonymous_visual_candidate_needs_producer_confirmation",
        }
        for index in range(1, 6)
    )
    scenes = []
    storyboards = []
    scene_media = []
    for index in range(1, 9):
        scene_id = f"SC{index:02d}"
        roles = ["Layla"]
        if scene_id in {"SC01", "SC05"}:
            roles.append("Nightmare Shadow")
        if scene_id in {"SC04", "SC08"}:
            roles.append("Amo Hassan")
        if scene_id == "SC08":
            roles.append("Featured Extras")
        scenes.append(
            {
                "scene_id": scene_id,
                "slug": scene_id.lower(),
                "title": scene_id,
                "roles": roles,
                "start_seconds": float(index),
                "end_seconds": float(index + 1),
                "generation_prompt": "Scene prompt.",
            }
        )
        storyboards.append({"scene_id": scene_id, "asset": f"story/{scene_id}.png", "shot_count": 1})
        scene_media.append(
            {
                "scene_id": scene_id,
                "motion_proxy": f"motion/{scene_id}.mp4",
                "temp_audio": f"audio/{scene_id}.m4a",
            }
        )
    manifest = {
        "cast": cast,
        "location_reference": "refs/location.png",
        "prop_reference": "refs/props.png",
        "scenes": scenes,
        "storyboards": storyboards,
        "scene_media": scene_media,
    }

    nodes, _notes, _groups = generator.build_specs(manifest)

    candidate_loaders = [node for node in nodes if node.title.startswith("IDENTITY EVIDENCE · Party visual candidate")]
    crowd_batches = [node for node in nodes if "SC08 CROWD BATCH" in node.title]
    crowd_master = [node for node in nodes if "SC08 CROWD MASTER" in node.title]
    assert len(candidate_loaders) == 5
    assert len(crowd_batches) == 2
    assert len(crowd_master) == 1
    used_candidate_ids = {
        source_id
        for node in crowd_batches
        for source_id, _slot in node.connections.values()
    }
    assert used_candidate_ids == {node.node_id for node in candidate_loaders}
