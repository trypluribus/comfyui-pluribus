from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest
from PIL import Image

import pluribus.project_portraits as portraits_module
from pluribus.bindings import BindingStore
from pluribus.identity_service import IdentityAnalysisService
from pluribus.project_portraits import ProjectPortraitService, rank_confirmed_portraits
from pluribus.storage import write_private_json


PERSON_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
STORAGE_GENERATION = "99999999-9999-4999-8999-999999999999"


def _upload_receipt(generation=STORAGE_GENERATION):
    return {
        "success": True,
        "portrait": {"storageGeneration": generation},
    }


def _image(path, *, color):
    Image.new("RGB", (80, 100), color=color).save(path, format="JPEG")


def _service(tmp_path, artifact_paths):
    service = object.__new__(ProjectPortraitService)
    service.identity = SimpleNamespace(
        artifact_path=lambda _job_id, artifact_id: artifact_paths[artifact_id]
    )
    service.staging_dir = str(tmp_path / "staged")
    os.makedirs(service.staging_dir, exist_ok=True)
    return service


def _cached():
    return {
        "candidates": [
            {
                "candidateId": "candidate-a",
                "occurrenceIds": ["appearance-a", "appearance-b"],
            }
        ],
        "occurrences": [
            {
                "occurrenceId": "appearance-a",
                "candidateId": "candidate-a",
                "cropArtifactId": "crop-a",
                "bbox": [0, 0, 80, 100],
                "confidence": 0.99,
            },
            {
                "occurrenceId": "appearance-b",
                "candidateId": "candidate-a",
                "cropArtifactId": "crop-b",
                "bbox": [0, 0, 70, 90],
                "confidence": 0.98,
            },
        ],
    }


def _desired(service, tmp_path, links):
    return service._desired_portraits(
        job_id="job-a",
        workflow_ref="workflow-a",
        project_id=PROJECT_ID,
        binding={"person_drafts": {}, "person_draft_tombstones": {}},
        cached=_cached(),
        links=links,
    )


def test_explicit_empty_selection_never_falls_back_to_every_candidate_occurrence(tmp_path):
    crop_a = tmp_path / "a.jpg"
    crop_b = tmp_path / "b.jpg"
    _image(crop_a, color=(220, 100, 80))
    _image(crop_b, color=(80, 100, 220))
    service = _service(tmp_path, {"crop-a": str(crop_a), "crop-b": str(crop_b)})

    desired = _desired(
        service,
        tmp_path,
        [{
            "candidateId": "candidate-a",
            "personId": PERSON_ID,
            "state": "confirmed",
            "occurrenceIds": [],
        }],
    )

    assert desired == []
    assert list((tmp_path / "staged").iterdir()) == []


def test_partial_selection_uploads_only_the_confirmed_checked_appearance(tmp_path):
    crop_a = tmp_path / "a.jpg"
    crop_b = tmp_path / "b.jpg"
    _image(crop_a, color=(220, 100, 80))
    _image(crop_b, color=(80, 100, 220))
    service = _service(tmp_path, {"crop-a": str(crop_a), "crop-b": str(crop_b)})

    desired = _desired(
        service,
        tmp_path,
        [{
            "candidateId": "candidate-a",
            "personId": PERSON_ID,
            "state": "confirmed",
            "occurrenceIds": ["appearance-b"],
        }],
    )

    assert len(desired) == 1
    assert desired[0]["displayOrder"] == 0
    assert desired[0]["makePrimary"] is True
    assert set(desired[0]) == {
        "workflowRef",
        "workflowRefs",
        "jobIds",
        "projectId",
        "personKey",
        "canonicalPersonId",
        "clientPortraitId",
        "contentSha256",
        "mimeType",
        "sizeBytes",
        "displayOrder",
        "makePrimary",
        "stagedFile",
        "state",
        "attemptCount",
    }
    staged = (tmp_path / "staged" / desired[0]["stagedFile"]).read_bytes()
    assert hashlib.sha256(staged).hexdigest() == desired[0]["contentSha256"]
    with Image.open(tmp_path / "staged" / desired[0]["stagedFile"]) as image:
        assert image.format == "JPEG"
        assert image.size[0] == image.size[1]
        assert image.getexif() == {}


def test_missing_legacy_selection_field_is_the_only_case_that_uses_candidate_membership(tmp_path):
    crop_a = tmp_path / "a.jpg"
    crop_b = tmp_path / "b.jpg"
    _image(crop_a, color=(220, 100, 80))
    _image(crop_b, color=(80, 100, 220))
    service = _service(tmp_path, {"crop-a": str(crop_a), "crop-b": str(crop_b)})

    desired = _desired(
        service,
        tmp_path,
        [{
            "candidateId": "candidate-a",
            "personId": PERSON_ID,
            "state": "confirmed",
        }],
    )

    assert len(desired) == 2


def test_rank_prefers_unambiguous_large_confident_crops_and_is_stable():
    candidates = [
        {"occurrenceId": "c", "cropArtifactId": "c", "bboxArea": 9000, "confidence": 1, "ambiguous": True},
        {"occurrenceId": "b", "cropArtifactId": "b", "bboxArea": 7000, "confidence": .95},
        {"occurrenceId": "a", "cropArtifactId": "a", "bboxArea": 7000, "confidence": .99},
    ]

    assert [value["occurrenceId"] for value in rank_confirmed_portraits(candidates)] == [
        "a",
        "b",
        "c",
    ]


def _workflow_state(tmp_path):
    bindings = BindingStore(str(tmp_path / "bindings.json"))
    workflow = bindings.resolve_workflow("portrait-recovery-workflow")
    bindings.associate(workflow["workflowRef"], PROJECT_ID, "production")
    identity = IdentityAnalysisService(str(tmp_path / "identity"), analyzer=object())
    service = ProjectPortraitService(
        identity,
        bindings,
        connection_path=str(tmp_path / "connection.json"),
    )
    return identity, bindings, workflow, service


def _write_empty_completed_job(identity, workflow_ref, suffix):
    job_id = f"{suffix:08d}-0000-4000-8000-000000000000"
    cache_key = f"{suffix % 16:x}" * 64
    identity._write_cache(
        cache_key,
        {"schemaVersion": 3, "candidates": [], "occurrences": [], "artifacts": []},
    )
    identity._write_job({
        "jobId": job_id,
        "state": "completed",
        "workflowRef": workflow_ref,
        "cacheKey": cache_key,
        "createdOrder": suffix,
    })
    identity.put_links(job_id, {"baseRevision": 0, "links": []})


def _write_portrait_job(
    identity,
    *,
    job_id,
    workflow_ref,
    cache_key,
    created_order,
    state="completed",
    color=(160, 90, 60),
):
    artifact_id = f"{cache_key[:8]}.jpg"
    cached = {
        "schemaVersion": 3,
        "candidates": [{
            "candidateId": f"candidate-{cache_key[:4]}",
            "occurrenceIds": [f"appearance-{cache_key[:4]}"],
        }],
        "occurrences": [{
            "occurrenceId": f"appearance-{cache_key[:4]}",
            "candidateId": f"candidate-{cache_key[:4]}",
            "sourceRef": "a" * 64,
            "cropArtifactId": artifact_id,
            "bbox": [0, 0, 80, 100],
            "confidence": 0.99,
        }],
        "artifacts": [artifact_id],
    }
    identity._write_cache(cache_key, cached)
    _image(os.path.join(identity._cache_path(cache_key), artifact_id), color=color)
    job = {
        "jobId": job_id,
        "state": state,
        "workflowRef": workflow_ref,
        "cacheKey": cache_key,
        "createdOrder": created_order,
    }
    identity._write_job(job)
    return job, cached


def _write_multi_portrait_job(
    identity,
    *,
    job_id,
    workflow_ref,
    cache_key,
    created_order,
    person_id,
    appearances,
    state="completed",
):
    candidate_id = f"candidate-{job_id[:8]}"
    occurrence_ids = []
    occurrences = []
    artifacts = []
    for index, appearance in enumerate(appearances):
        occurrence_id = f"appearance-{job_id[:8]}-{index}"
        artifact_id = f"crop-{job_id[:8]}-{index}.jpg"
        occurrence_ids.append(occurrence_id)
        artifacts.append(artifact_id)
        width, height = appearance.get("bbox", (80, 100))
        occurrences.append({
            "occurrenceId": occurrence_id,
            "candidateId": candidate_id,
            "sourceRef": "a" * 64,
            "cropArtifactId": artifact_id,
            "bbox": [0, 0, width, height],
            "confidence": appearance.get("confidence", 0.99),
        })
    cached = {
        "schemaVersion": 3,
        "candidates": [{
            "candidateId": candidate_id,
            "occurrenceIds": occurrence_ids,
        }],
        "occurrences": occurrences,
        "artifacts": artifacts,
    }
    identity._write_cache(cache_key, cached)
    for artifact_id, appearance in zip(artifacts, appearances):
        _image(
            os.path.join(identity._cache_path(cache_key), artifact_id),
            color=appearance["color"],
        )
    job = {
        "jobId": job_id,
        "state": state,
        "workflowRef": workflow_ref,
        "cacheKey": cache_key,
        "createdOrder": created_order,
    }
    identity._write_job(job)
    if state == "completed":
        identity.put_links(
            job_id,
            {
                "baseRevision": 0,
                "links": [{
                    "candidateId": candidate_id,
                    "personId": person_id,
                    "state": "confirmed",
                    "occurrenceIds": occurrence_ids,
                }],
            },
        )
    return job


def _set_hosted_alias(bindings, workflow_ref, local_person_id, canonical_person_id):
    with bindings._lock:
        data = bindings._read()
        binding = bindings._find(data, workflow_ref)
        project_id = str(binding.get("project_id") or "")
        marker = (
            {
                "state": "synced",
                "clientPersonId": local_person_id,
                "canonicalPersonId": canonical_person_id,
                "requestMode": "new",
                "requestHash": "f" * 64,
            }
            if canonical_person_id
            else None
        )
        binding.setdefault("person_drafts", {})[local_person_id] = {
            "draftId": local_person_id,
            "canonicalPersonId": canonical_person_id,
            "sourceRefs": [],
            **({"workspaceAlias": marker} if marker else {}),
            **(
                {"workspaceAliases": {project_id: marker}}
                if marker and project_id
                else {}
            ),
        }
        bindings._write(data)


def test_startup_recovery_projects_only_the_authoritative_latest_workflow_job(tmp_path):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    # UUID filename order deliberately disagrees with createdOrder.
    older_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    newest_id = "00000000-0000-4000-8000-000000000001"
    _write_portrait_job(
        identity,
        job_id=older_id,
        workflow_ref=workflow["workflowRef"],
        cache_key="1" * 64,
        created_order=1,
        color=(60, 60, 60),
    )
    _newest_job, newest_cache = _write_portrait_job(
        identity,
        job_id=newest_id,
        workflow_ref=workflow["workflowRef"],
        cache_key="2" * 64,
        created_order=2,
        color=(220, 120, 80),
    )
    newest_candidate = newest_cache["candidates"][0]
    identity.put_links(
        newest_id,
        {
            "baseRevision": 0,
            "links": [{
                "candidateId": newest_candidate["candidateId"],
                "personId": PERSON_ID,
                "state": "confirmed",
                "occurrenceIds": newest_candidate["occurrenceIds"],
            }],
        },
    )

    results = service.reconcile_completed_jobs()

    assert [value["jobId"] for value in results] == [newest_id]
    status = service.sync_status()
    assert len(status) == 1
    assert status[0]["state"] == "pending"


def test_newest_noncompleted_job_preserves_existing_portrait_outbox(
    tmp_path,
    monkeypatch,
):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    older_id = str(uuid.uuid4())
    newer_id = str(uuid.uuid4())
    _older, cached = _write_portrait_job(
        identity,
        job_id=older_id,
        workflow_ref=workflow["workflowRef"],
        cache_key="3" * 64,
        created_order=1,
    )
    identity.put_links(
        older_id,
        {
            "baseRevision": 0,
            "links": [{
                "candidateId": cached["candidates"][0]["candidateId"],
                "personId": PERSON_ID,
                "state": "confirmed",
                "occurrenceIds": cached["candidates"][0]["occurrenceIds"],
            }],
        },
    )
    service.reconcile_completed_jobs()
    before = portraits_module._read_json(service.outbox_path)["operations"]

    newer, _ = _write_portrait_job(
        identity,
        job_id=newer_id,
        workflow_ref=workflow["workflowRef"],
        cache_key="4" * 64,
        created_order=2,
        state="queued",
    )
    identity._jobs[newer_id] = newer

    assert service.reconcile_completed_jobs() == []
    assert portraits_module._read_json(service.outbox_path)["operations"] == before
    assert any(
        value.get("code") == "identity_analysis_in_progress"
        for value in service.sync_status()
    )
    calls = []

    async def should_not_upload(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {"success": True}

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        should_not_upload,
    )
    asyncio.run(service.drain_pending_async())
    assert calls == []


def test_project_arbiter_globally_ranks_caps_and_deduplicates_two_workflows(tmp_path):
    identity, bindings, first_workflow, service = _workflow_state(tmp_path)
    second_workflow = bindings.resolve_workflow("portrait-second-workflow")
    bindings.associate(second_workflow["workflowRef"], PROJECT_ID, "production")
    first_local_person = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    second_local_person = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    _set_hosted_alias(bindings, first_workflow["workflowRef"], first_local_person, PERSON_ID)
    _set_hosted_alias(bindings, second_workflow["workflowRef"], second_local_person, PERSON_ID)

    first_job_id = "10000000-0000-4000-8000-000000000001"
    second_job_id = "20000000-0000-4000-8000-000000000002"
    duplicate_color = (210, 80, 60)
    _write_multi_portrait_job(
        identity,
        job_id=first_job_id,
        workflow_ref=first_workflow["workflowRef"],
        cache_key="7" * 64,
        created_order=1,
        person_id=first_local_person,
        appearances=[
            {"color": duplicate_color, "bbox": (100, 100)},
            {"color": (20, 40, 60), "bbox": (90, 100)},
            {"color": (40, 60, 80), "bbox": (80, 100)},
            {"color": (60, 80, 100), "bbox": (70, 100)},
        ],
    )
    _write_multi_portrait_job(
        identity,
        job_id=second_job_id,
        workflow_ref=second_workflow["workflowRef"],
        cache_key="8" * 64,
        created_order=1,
        person_id=second_local_person,
        appearances=[
            # Exact sanitized-content duplicate of the first workflow's top
            # crop. The larger box makes this workflow its deterministic owner.
            {"color": duplicate_color, "bbox": (110, 100)},
            {"color": (220, 180, 40), "bbox": (105, 100)},
            {"color": (180, 140, 30), "bbox": (65, 100)},
            {"color": (140, 100, 20), "bbox": (60, 100)},
        ],
    )

    result = service.reconcile_job(first_job_id)
    outbox = portraits_module._read_json(service.outbox_path)
    operations = list(outbox["operations"].values())

    assert result["queued"] == 5
    assert len(operations) == 5
    assert {value["canonicalPersonId"] for value in operations} == {PERSON_ID}
    assert {value["displayOrder"] for value in operations} == set(range(5))
    assert sum(value["makePrimary"] is True for value in operations) == 1
    assert len({value["contentSha256"] for value in operations}) == 5
    primary = next(value for value in operations if value["makePrimary"])
    assert primary["workflowRef"] == second_workflow["workflowRef"]


def test_project_arbiter_never_merges_unpromoted_people_across_workflows(tmp_path):
    identity, bindings, first_workflow, service = _workflow_state(tmp_path)
    second_workflow = bindings.resolve_workflow("portrait-unpromoted-workflow")
    bindings.associate(second_workflow["workflowRef"], PROJECT_ID, "production")
    first_local_person = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    second_local_person = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    with bindings._lock:
        data = bindings._read()
        for workflow_ref, person_id in (
            (first_workflow["workflowRef"], first_local_person),
            (second_workflow["workflowRef"], second_local_person),
        ):
            binding = bindings._find(data, workflow_ref)
            binding.setdefault("person_drafts", {})[person_id] = {
                "draftId": person_id,
                "sourceRefs": [],
            }
        bindings._write(data)

    shared_color = (100, 120, 140)
    first_job_id = "30000000-0000-4000-8000-000000000003"
    second_job_id = "40000000-0000-4000-8000-000000000004"
    for job_id, workflow_ref, cache_key, person_id in (
        (first_job_id, first_workflow["workflowRef"], "9" * 64, first_local_person),
        (second_job_id, second_workflow["workflowRef"], "a" * 64, second_local_person),
    ):
        _write_multi_portrait_job(
            identity,
            job_id=job_id,
            workflow_ref=workflow_ref,
            cache_key=cache_key,
            created_order=1,
            person_id=person_id,
            appearances=[{"color": shared_color, "bbox": (80, 100)}],
        )

    service.reconcile_job(first_job_id)
    operations = list(
        portraits_module._read_json(service.outbox_path)["operations"].values()
    )

    assert len(operations) == 2
    assert {value["state"] for value in operations} == {"waiting_for_person"}
    assert {value["personKey"] for value in operations} == {
        first_local_person,
        second_local_person,
    }
    assert len({value["clientPortraitId"] for value in operations}) == 2


def test_two_workflow_startup_retry_is_stable_and_freezes_on_unfinished_newest_job(
    tmp_path,
    monkeypatch,
):
    identity, bindings, first_workflow, service = _workflow_state(tmp_path)
    second_workflow = bindings.resolve_workflow("portrait-retry-workflow")
    bindings.associate(second_workflow["workflowRef"], PROJECT_ID, "production")
    first_local_person = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    second_local_person = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    _set_hosted_alias(bindings, first_workflow["workflowRef"], first_local_person, PERSON_ID)
    _set_hosted_alias(bindings, second_workflow["workflowRef"], second_local_person, PERSON_ID)
    first_job_id = "50000000-0000-4000-8000-000000000005"
    second_job_id = "60000000-0000-4000-8000-000000000006"
    _write_multi_portrait_job(
        identity,
        job_id=first_job_id,
        workflow_ref=first_workflow["workflowRef"],
        cache_key="b" * 64,
        created_order=1,
        person_id=first_local_person,
        appearances=[{"color": (30, 60, 90), "bbox": (90, 100)}],
    )
    _write_multi_portrait_job(
        identity,
        job_id=second_job_id,
        workflow_ref=second_workflow["workflowRef"],
        cache_key="c" * 64,
        created_order=1,
        person_id=second_local_person,
        appearances=[{"color": (90, 60, 30), "bbox": (80, 100)}],
    )

    assert len(service.reconcile_completed_jobs()) == 1
    initial_ids = {
        value["clientPortraitId"]
        for value in portraits_module._read_json(service.outbox_path)["operations"].values()
    }
    attempts = 0

    async def flaky_upload(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return 503, {"state": "temporarily_unavailable"}
        return 200, _upload_receipt()

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        flaky_upload,
    )
    first_drain = asyncio.run(service.drain_pending_async())
    assert first_drain[-1]["lastStatus"] == 503

    restarted = ProjectPortraitService(
        identity,
        bindings,
        connection_path=str(tmp_path / "connection.json"),
    )
    assert len(restarted.reconcile_completed_jobs()) == 1
    assert {
        value["clientPortraitId"]
        for value in portraits_module._read_json(restarted.outbox_path)["operations"].values()
    } == initial_ids
    asyncio.run(restarted.drain_pending_async())
    assert {value["state"] for value in restarted.sync_status()} == {"synced"}

    _write_multi_portrait_job(
        identity,
        job_id="70000000-0000-4000-8000-000000000007",
        workflow_ref=second_workflow["workflowRef"],
        cache_key="d" * 64,
        created_order=2,
        person_id=second_local_person,
        appearances=[{"color": (200, 200, 40), "bbox": (120, 100)}],
        state="queued",
    )
    before_freeze = portraits_module._read_json(restarted.outbox_path)
    assert restarted.reconcile_completed_jobs() == []
    after_freeze = portraits_module._read_json(restarted.outbox_path)
    assert after_freeze["operations"] == before_freeze["operations"]
    assert after_freeze["projectErrors"][PROJECT_ID]["code"] == (
        "identity_analysis_in_progress"
    )


@pytest.mark.parametrize(
    "damage",
    [
        "cache",
        "candidate",
        "occurrence",
        "duplicate",
        "membership",
        "links",
        "numeric",
        "artifact",
    ],
)
def test_missing_confirmed_evidence_blocks_projection_without_retirement(
    tmp_path,
    monkeypatch,
    damage,
):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    job_id = "71000000-0000-4000-8000-000000000007"
    cache_key = "e" * 64
    _job, cached = _write_portrait_job(
        identity,
        job_id=job_id,
        workflow_ref=workflow["workflowRef"],
        cache_key=cache_key,
        created_order=1,
    )
    identity.put_links(
        job_id,
        {
            "baseRevision": 0,
            "links": [{
                "candidateId": cached["candidates"][0]["candidateId"],
                "personId": PERSON_ID,
                "state": "confirmed",
                "occurrenceIds": cached["candidates"][0]["occurrenceIds"],
            }],
        },
    )
    service.reconcile_job(job_id)
    before = portraits_module._read_json(service.outbox_path)["operations"]
    if damage == "cache":
        os.remove(identity._cache_json_path(cache_key))
    elif damage == "candidate":
        identity._write_cache(cache_key, {**cached, "candidates": []})
    elif damage == "occurrence":
        identity._write_cache(cache_key, {**cached, "occurrences": []})
    elif damage == "duplicate":
        identity._write_cache(
            cache_key,
            {**cached, "occurrences": [*cached["occurrences"], cached["occurrences"][0]]},
        )
    elif damage == "membership":
        identity._write_cache(
            cache_key,
            {
                **cached,
                "occurrences": [
                    {**cached["occurrences"][0], "candidateId": "wrong-candidate"}
                ],
            },
        )
    elif damage == "links":
        with open(identity._links_path_for_job(_job), "w", encoding="utf-8") as handle:
            handle.write("{")
    elif damage == "numeric":
        identity._write_cache(
            cache_key,
            {
                **cached,
                "occurrences": [
                    {**cached["occurrences"][0], "confidence": "not-a-number"}
                ],
            },
        )
    else:
        os.remove(
            os.path.join(
                identity._cache_path(cache_key),
                cached["occurrences"][0]["cropArtifactId"],
            )
        )

    result = service.reconcile_job(job_id)

    assert result["state"] == "projection_blocked"
    assert result["code"] == "identity_evidence_unavailable"
    after = portraits_module._read_json(service.outbox_path)
    assert after["operations"] == before
    assert all(
        value["state"] != "retire_pending"
        for value in after["operations"].values()
    )
    calls = []

    async def should_not_sync(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {"success": True}

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        should_not_sync,
    )
    monkeypatch.setattr(
        portraits_module.remote,
        "retire_project_person_portrait",
        should_not_sync,
    )
    drained = asyncio.run(service.drain_pending_async())
    assert calls == []
    assert drained[-1]["code"] == "identity_evidence_unavailable"


def test_overlapping_confirmed_aliases_are_accepted_only_for_same_survivor(
    tmp_path,
    monkeypatch,
):
    identity, bindings, workflow, service = _workflow_state(tmp_path)
    job_id = "72000000-0000-4000-8000-000000000007"
    cache_key = "f" * 64
    _job, cached = _write_portrait_job(
        identity,
        job_id=job_id,
        workflow_ref=workflow["workflowRef"],
        cache_key=cache_key,
        created_order=1,
    )
    alias_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    alias_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    survivor = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    _set_hosted_alias(bindings, workflow["workflowRef"], alias_a, survivor)
    _set_hosted_alias(bindings, workflow["workflowRef"], alias_b, survivor)
    candidate_id = cached["candidates"][0]["candidateId"]
    occurrence_ids = cached["candidates"][0]["occurrenceIds"]
    identity.put_links(
        job_id,
        {
            "baseRevision": 0,
            "links": [{
                "candidateId": candidate_id,
                "personId": alias_a,
                "state": "confirmed",
                "occurrenceIds": occurrence_ids,
            }],
        },
    )
    links_path = identity._links_path_for_job(_job)
    with open(links_path, encoding="utf-8") as handle:
        document = json.load(handle)
    document["links"].append({
        **document["links"][0],
        "personId": alias_b,
    })
    write_private_json(links_path, document)

    result = service.reconcile_job(job_id)
    assert result["queued"] == 1
    before = portraits_module._read_json(service.outbox_path)["operations"]

    different_survivor = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    _set_hosted_alias(
        bindings,
        workflow["workflowRef"],
        alias_b,
        different_survivor,
    )
    blocked = service.reconcile_job(job_id)
    assert blocked["state"] == "projection_blocked"
    assert blocked["code"] == "identity_evidence_unavailable"
    assert portraits_module._read_json(service.outbox_path)["operations"] == before

    calls = []

    async def should_not_sync(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, {"success": True}

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        should_not_sync,
    )
    monkeypatch.setattr(
        portraits_module.remote,
        "retire_project_person_portrait",
        should_not_sync,
    )
    asyncio.run(service.drain_pending_async())
    assert calls == []


def test_unmatched_pending_reservation_is_retired_after_deselection(tmp_path, monkeypatch):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    job_id = str(uuid.uuid4())
    cache_key = "5" * 64
    identity._write_cache(cache_key, {"candidates": [], "occurrences": [], "artifacts": []})
    identity._write_job({
        "jobId": job_id,
        "state": "completed",
        "workflowRef": workflow["workflowRef"],
        "cacheKey": cache_key,
        "createdOrder": 1,
    })
    identity.put_links(job_id, {"baseRevision": 0, "links": []})
    staged_file = "reserved.jpg"
    staged_path = os.path.join(service.staging_dir, staged_file)
    _write = b"already-sanitized"
    with open(staged_path, "wb") as handle:
        handle.write(_write)
    operation = {
        "operationId": "pending-operation",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": "33333333-3333-4333-8333-333333333333",
        "contentSha256": hashlib.sha256(_write).hexdigest(),
        "mimeType": "image/jpeg",
        "sizeBytes": len(_write),
        "displayOrder": 0,
        "makePrimary": True,
        "stagedFile": staged_file,
        "state": "pending",
        "attemptCount": 0,
    }
    write_private_json(
        service.outbox_path,
        {"schemaVersion": 1, "operations": {"pending-operation": operation}},
    )

    service.reconcile_job(job_id)
    queued = service.sync_status()
    assert queued[0]["state"] == "retire_pending"
    assert os.path.exists(staged_path)

    calls = []

    async def resolve(*args, **kwargs):
        calls.append(("resolve", args, kwargs))
        return 200, {
            "proof": {
                "found": True,
                "materialMatches": True,
                "storageGeneration": STORAGE_GENERATION,
                "status": "active",
            }
        }

    async def retire(*args, **kwargs):
        calls.append(("retire", args, kwargs))
        return 200, {"success": True}

    monkeypatch.setattr(
        portraits_module.remote,
        "resolve_project_person_portrait_generation",
        resolve,
    )
    monkeypatch.setattr(portraits_module.remote, "retire_project_person_portrait", retire)
    asyncio.run(service.drain_pending_async())

    assert [call[0] for call in calls] == ["resolve", "retire"]
    assert calls[1][1][-1] == STORAGE_GENERATION
    assert service.sync_status() == []
    assert not os.path.exists(staged_path)


def test_lost_upload_response_keeps_staged_bytes_until_generation_receipt_is_durable(
    tmp_path,
    monkeypatch,
):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    _write_empty_completed_job(identity, workflow["workflowRef"], 61)
    payload = b"crash-safe-pending-portrait"
    staged_file = "crash-safe.jpg"
    staged_path = os.path.join(service.staging_dir, staged_file)
    with open(staged_path, "wb") as handle:
        handle.write(payload)
    operation = {
        "operationId": "crash-safe-upload",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": "34343434-3434-4434-8434-343434343434",
        "contentSha256": hashlib.sha256(payload).hexdigest(),
        "mimeType": "image/jpeg",
        "sizeBytes": len(payload),
        "displayOrder": 0,
        "makePrimary": True,
        "stagedFile": staged_file,
        "state": "pending",
        "attemptCount": 0,
    }
    write_private_json(
        service.outbox_path,
        {"schemaVersion": 1, "operations": {"crash-safe-upload": operation}},
    )
    calls = []

    async def upload_remote(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, _upload_receipt(STORAGE_GENERATION)

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        upload_remote,
    )
    durable_write = portraits_module.write_private_json

    def crash_before_receipt(path, value):
        if path == service.outbox_path:
            raise RuntimeError("crash after hosted response")
        durable_write(path, value)

    monkeypatch.setattr(portraits_module, "write_private_json", crash_before_receipt)
    with pytest.raises(RuntimeError, match="after hosted response"):
        asyncio.run(service.drain_pending_async())

    assert os.path.exists(staged_path)
    persisted = portraits_module._read_json(service.outbox_path)["operations"][
        "crash-safe-upload"
    ]
    assert persisted["state"] == "pending"
    assert "storageGeneration" not in persisted

    monkeypatch.setattr(portraits_module, "write_private_json", durable_write)
    asyncio.run(service.drain_pending_async())
    assert len(calls) == 2
    assert service.sync_status()[0]["state"] == "synced"
    assert not os.path.exists(staged_path)


def test_generation_receipt_survives_crash_before_staged_cleanup(
    tmp_path,
    monkeypatch,
):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    _write_empty_completed_job(identity, workflow["workflowRef"], 62)
    payload = b"durable-before-cleanup"
    staged_file = "cleanup-crash.jpg"
    staged_path = os.path.join(service.staging_dir, staged_file)
    with open(staged_path, "wb") as handle:
        handle.write(payload)
    operation = {
        "operationId": "cleanup-crash-upload",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": "35353535-3535-4535-8535-353535353535",
        "contentSha256": hashlib.sha256(payload).hexdigest(),
        "mimeType": "image/jpeg",
        "sizeBytes": len(payload),
        "displayOrder": 0,
        "makePrimary": True,
        "stagedFile": staged_file,
        "state": "pending",
        "attemptCount": 0,
    }
    write_private_json(
        service.outbox_path,
        {"schemaVersion": 1, "operations": {"cleanup-crash-upload": operation}},
    )
    calls = []

    async def upload_remote(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, _upload_receipt(STORAGE_GENERATION)

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        upload_remote,
    )
    remove_staged = service._remove_staged

    def crash_before_cleanup(_operation):
        raise RuntimeError("crash before staged cleanup")

    monkeypatch.setattr(service, "_remove_staged", crash_before_cleanup)
    with pytest.raises(RuntimeError, match="before staged cleanup"):
        asyncio.run(service.drain_pending_async())

    persisted = portraits_module._read_json(service.outbox_path)["operations"][
        "cleanup-crash-upload"
    ]
    assert persisted["state"] == "synced"
    assert persisted["storageGeneration"] == STORAGE_GENERATION
    assert os.path.exists(staged_path)

    monkeypatch.setattr(service, "_remove_staged", remove_staged)
    asyncio.run(service.drain_pending_async())
    assert len(calls) == 1
    remove_staged(persisted)
    assert not os.path.exists(staged_path)


def test_deselection_recovers_lost_receipt_as_already_absent_without_delete(
    tmp_path,
    monkeypatch,
):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    _write_empty_completed_job(identity, workflow["workflowRef"], 63)
    payload = b"lost-response-deselected"
    operation = {
        "operationId": "lost-response-deselected",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": "36363636-3636-4636-8636-363636363636",
        "contentSha256": hashlib.sha256(payload).hexdigest(),
        "state": "retire_pending",
        "attemptCount": 0,
    }
    write_private_json(
        service.outbox_path,
        {"schemaVersion": 1, "operations": {"lost-response-deselected": operation}},
    )
    calls = []

    async def resolve_remote(*args, **kwargs):
        calls.append("resolve")
        return 200, {"proof": {"found": False, "materialMatches": False}}

    async def retire_remote(*args, **kwargs):
        calls.append("retire")
        return 200, {"success": True}

    monkeypatch.setattr(
        portraits_module.remote,
        "resolve_project_person_portrait_generation",
        resolve_remote,
    )
    monkeypatch.setattr(
        portraits_module.remote,
        "retire_project_person_portrait",
        retire_remote,
    )

    asyncio.run(service.drain_pending_async())
    assert calls == ["resolve"]
    assert service.sync_status() == []


def test_full_set_replacement_retires_before_uploading_new_portrait(tmp_path, monkeypatch):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    _write_empty_completed_job(identity, workflow["workflowRef"], 7)
    payload = b"new-sanitized-portrait"
    staged_file = "replacement.jpg"
    with open(os.path.join(service.staging_dir, staged_file), "wb") as handle:
        handle.write(payload)
    retire = {
        "operationId": "z-retire",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": "44444444-4444-4444-8444-444444444444",
        "storageGeneration": STORAGE_GENERATION,
        "state": "retire_pending",
        "attemptCount": 0,
    }
    upload = {
        "operationId": "a-upload",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": "55555555-5555-4555-8555-555555555555",
        "contentSha256": hashlib.sha256(payload).hexdigest(),
        "mimeType": "image/jpeg",
        "sizeBytes": len(payload),
        "displayOrder": 4,
        "makePrimary": False,
        "stagedFile": staged_file,
        "state": "pending",
        "attemptCount": 0,
    }
    write_private_json(
        service.outbox_path,
        {
            "schemaVersion": 1,
            "operations": {"a-upload": upload, "z-retire": retire},
        },
    )
    calls = []

    async def retire_remote(*args, **kwargs):
        calls.append("retire")
        return 200, {"success": True}

    async def upload_remote(*args, **kwargs):
        calls.append("upload")
        return 200, _upload_receipt()

    monkeypatch.setattr(
        portraits_module.remote,
        "retire_project_person_portrait",
        retire_remote,
    )
    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        upload_remote,
    )

    asyncio.run(service.drain_pending_async())

    assert calls == ["retire", "upload"]


def test_alias_repoint_retires_frozen_owner_before_uploading_survivor(
    tmp_path,
    monkeypatch,
):
    identity, bindings, workflow, service = _workflow_state(tmp_path)
    _write_empty_completed_job(identity, workflow["workflowRef"], 8)
    local_person_id = "12345678-1234-4234-8234-123456789abc"
    old_hosted_person = "aaaaaaaa-0000-4000-8000-000000000001"
    new_hosted_person = "bbbbbbbb-0000-4000-8000-000000000002"
    _set_hosted_alias(
        bindings,
        workflow["workflowRef"],
        local_person_id,
        new_hosted_person,
    )
    payload = b"survivor-sanitized-portrait"
    staged_file = "survivor.jpg"
    with open(os.path.join(service.staging_dir, staged_file), "wb") as handle:
        handle.write(payload)
    retiring = {
        "operationId": "retire-old-owner",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": local_person_id,
        "canonicalPersonId": old_hosted_person,
        "clientPortraitId": "77777777-7777-4777-8777-777777777777",
        "storageGeneration": STORAGE_GENERATION,
        "state": "retire_pending",
        "attemptCount": 0,
    }
    upload = {
        "operationId": "upload-new-owner",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": local_person_id,
        "canonicalPersonId": new_hosted_person,
        "clientPortraitId": "88888888-8888-4888-8888-888888888888",
        "contentSha256": hashlib.sha256(payload).hexdigest(),
        "mimeType": "image/jpeg",
        "sizeBytes": len(payload),
        "displayOrder": 0,
        "makePrimary": True,
        "stagedFile": staged_file,
        "state": "pending",
        "attemptCount": 0,
    }
    write_private_json(
        service.outbox_path,
        {
            "schemaVersion": 1,
            "operations": {
                "retire-old-owner": retiring,
                "upload-new-owner": upload,
            },
        },
    )
    calls = []

    async def retire_remote(_connection, _project, person_id, _portrait, _generation):
        calls.append(("retire", person_id))
        return 200, {"success": True}

    async def upload_remote(
        _connection,
        _project,
        person_id,
        _portrait,
        _payload,
        **_kwargs,
    ):
        calls.append(("upload", person_id))
        return 200, _upload_receipt()

    monkeypatch.setattr(
        portraits_module.remote,
        "retire_project_person_portrait",
        retire_remote,
    )
    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        upload_remote,
    )

    asyncio.run(service.drain_pending_async())

    assert calls == [
        ("retire", old_hosted_person),
        ("upload", new_hosted_person),
    ]


def test_reintroduced_retiring_portrait_finishes_retirement_before_new_rank_upload(
    tmp_path,
    monkeypatch,
):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    job_id = str(uuid.uuid4())
    cache_key = "6" * 64
    identity._write_cache(cache_key, {"candidates": [], "occurrences": [], "artifacts": []})
    identity._write_job({
        "jobId": job_id,
        "state": "completed",
        "workflowRef": workflow["workflowRef"],
        "cacheKey": cache_key,
        "createdOrder": 1,
    })
    identity.put_links(job_id, {"baseRevision": 0, "links": []})
    payload = b"reintroduced-sanitized-portrait"
    staged_file = "reintroduced.jpg"
    with open(os.path.join(service.staging_dir, staged_file), "wb") as handle:
        handle.write(payload)
    client_portrait_id = "66666666-6666-4666-8666-666666666666"
    retiring = {
        "operationId": "retiring-operation",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": client_portrait_id,
        "storageGeneration": STORAGE_GENERATION,
        "contentSha256": hashlib.sha256(payload).hexdigest(),
        "mimeType": "image/jpeg",
        "sizeBytes": len(payload),
        "displayOrder": 0,
        "makePrimary": True,
        "stagedFile": staged_file,
        "state": "retire_pending",
        "attemptCount": 2,
    }
    write_private_json(
        service.outbox_path,
        {"schemaVersion": 1, "operations": {"retiring-operation": retiring}},
    )
    replacement = {
        **retiring,
        "displayOrder": 3,
        "makePrimary": False,
        "state": "pending",
        "attemptCount": 0,
    }
    monkeypatch.setattr(
        service,
        "_desired_project_portraits",
        lambda **_kwargs: [replacement],
    )

    service.reconcile_job(job_id)
    queued = service.sync_status()
    assert queued[0]["state"] == "retire_pending"

    calls = []

    async def retire_remote(*args, **kwargs):
        calls.append("retire")
        return 200, {"success": True}

    async def upload_remote(*args, **kwargs):
        calls.append(("upload", kwargs["display_order"], kwargs["make_primary"]))
        return 200, _upload_receipt("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    monkeypatch.setattr(
        portraits_module.remote,
        "retire_project_person_portrait",
        retire_remote,
    )
    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        upload_remote,
    )

    asyncio.run(service.drain_pending_async())

    assert calls == ["retire", ("upload", 3, False)]
    assert service.sync_status()[0]["state"] == "synced"


@pytest.mark.parametrize("proof", ["same", "mismatch", "missing"])
def test_stale_retirement_blocks_until_fresh_projection_proves_current_generation(
    tmp_path,
    monkeypatch,
    proof,
):
    identity, _bindings, workflow, service = _workflow_state(tmp_path)
    _write_empty_completed_job(identity, workflow["workflowRef"], 9)
    generation_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    generation_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    content_sha256 = "a" * 64
    operation = {
        "operationId": "stale-retirement",
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "personKey": PERSON_ID,
        "canonicalPersonId": PERSON_ID,
        "clientPortraitId": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
        "contentSha256": content_sha256,
        "storageGeneration": generation_a,
        "state": "retire_pending",
        "attemptCount": 0,
    }
    write_private_json(
        service.outbox_path,
        {"schemaVersion": 1, "operations": {"stale-retirement": operation}},
    )
    calls = []

    async def retire_remote(_connection, _project, _person, _portrait, generation):
        calls.append(generation)
        if generation == generation_a:
            response = {
                "error": "conflict",
                "pluginCode": "stale_portrait_generation",
                "currentStorageGeneration": generation_b,
            }
            if proof == "same":
                response["currentClientContentSha256"] = content_sha256
            elif proof == "mismatch":
                response["currentClientContentSha256"] = "b" * 64
            return 409, response
        return 200, {"success": True}

    monkeypatch.setattr(
        portraits_module.remote,
        "retire_project_person_portrait",
        retire_remote,
    )

    first = asyncio.run(service.drain_pending_async())
    assert calls == [generation_a]
    assert first[-1]["state"] == "projection_blocked"
    blocked = portraits_module._read_json(service.outbox_path)
    assert blocked["operations"]["stale-retirement"]["state"] == "retire_pending"
    assert blocked["operations"]["stale-retirement"]["conflictStorageGeneration"] == generation_b
    assert blocked["projectErrors"][PROJECT_ID]["code"] == "stale_portrait_generation"

    asyncio.run(service.drain_pending_async())
    assert calls == [generation_a]

    service.reconcile_completed_jobs()
    asyncio.run(service.drain_pending_async())
    if proof == "same":
        assert calls == [generation_a, generation_b]
        assert service.sync_status() == []
    else:
        assert calls == [generation_a]
        assert service.sync_status()[-1]["state"] == "projection_blocked"


def test_unresolved_waiting_person_does_not_starve_promoted_portrait(
    tmp_path,
    monkeypatch,
):
    identity, bindings, workflow, service = _workflow_state(tmp_path)
    _write_empty_completed_job(identity, workflow["workflowRef"], 10)
    unresolved = "aaaaaaaa-0000-4000-8000-000000000001"
    promoted = "bbbbbbbb-0000-4000-8000-000000000002"
    hosted = "cccccccc-0000-4000-8000-000000000003"
    _set_hosted_alias(bindings, workflow["workflowRef"], unresolved, "")
    _set_hosted_alias(bindings, workflow["workflowRef"], promoted, hosted)
    payload = b"promoted-portrait"
    staged_file = "promoted.jpg"
    with open(os.path.join(service.staging_dir, staged_file), "wb") as handle:
        handle.write(payload)
    common = {
        "workflowRef": workflow["workflowRef"],
        "projectId": PROJECT_ID,
        "contentSha256": hashlib.sha256(payload).hexdigest(),
        "mimeType": "image/jpeg",
        "sizeBytes": len(payload),
        "displayOrder": 0,
        "makePrimary": True,
        "stagedFile": staged_file,
        "state": "waiting_for_person",
        "attemptCount": 0,
    }
    operations = {
        "a-unresolved": {
            **common,
            "operationId": "a-unresolved",
            "personKey": unresolved,
            "clientPortraitId": "dddddddd-0000-4000-8000-000000000004",
            "stagedFile": "unresolved.jpg",
        },
        "b-promoted": {
            **common,
            "operationId": "b-promoted",
            "personKey": promoted,
            "clientPortraitId": "eeeeeeee-0000-4000-8000-000000000005",
        },
    }
    with open(os.path.join(service.staging_dir, "unresolved.jpg"), "wb") as handle:
        handle.write(payload)
    write_private_json(
        service.outbox_path,
        {"schemaVersion": 1, "operations": operations},
    )
    calls = []

    async def upload_remote(_connection, _project, person, *_args, **_kwargs):
        calls.append(person)
        return 200, _upload_receipt()

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        upload_remote,
    )
    states = asyncio.run(service.drain_pending_async())

    assert calls == [hosted]
    assert any(state["state"] == "waiting_for_person" for state in states)
    assert {state["state"] for state in service.sync_status()} == {
        "waiting_for_person",
        "synced",
    }


def test_missing_bound_workflow_job_freezes_project_wide_retirement(tmp_path):
    identity, bindings, first_workflow, service = _workflow_state(tmp_path)
    second_workflow = bindings.resolve_workflow("missing-job-workflow")
    bindings.associate(second_workflow["workflowRef"], PROJECT_ID, "production")
    first_person = "aaaaaaaa-1111-4111-8111-111111111111"
    second_person = "bbbbbbbb-2222-4222-8222-222222222222"
    first_job = "11111111-0000-4000-8000-000000000001"
    second_job = "22222222-0000-4000-8000-000000000002"
    _write_multi_portrait_job(
        identity,
        job_id=first_job,
        workflow_ref=first_workflow["workflowRef"],
        cache_key="1" * 64,
        created_order=1,
        person_id=first_person,
        appearances=[{"color": (30, 60, 90)}],
    )
    _write_multi_portrait_job(
        identity,
        job_id=second_job,
        workflow_ref=second_workflow["workflowRef"],
        cache_key="2" * 64,
        created_order=1,
        person_id=second_person,
        appearances=[{"color": (90, 60, 30)}],
    )
    assert len(service.reconcile_completed_jobs()) == 1
    before = portraits_module._read_json(service.outbox_path)["operations"]

    os.remove(os.path.join(identity.jobs_dir, f"{second_job}.json"))
    identity._jobs.pop(second_job, None)
    assert service.reconcile_completed_jobs() == []
    after = portraits_module._read_json(service.outbox_path)
    assert after["operations"] == before
    assert after["projectErrors"][PROJECT_ID]["code"] == "identity_evidence_unavailable"
    assert all(operation["state"] != "retire_pending" for operation in after["operations"].values())


def _sync_one_project_portrait(identity, workflow, service, monkeypatch):
    job_id = "73000000-0000-4000-8000-000000000007"
    _write_multi_portrait_job(
        identity,
        job_id=job_id,
        workflow_ref=workflow["workflowRef"],
        cache_key="3" * 64,
        created_order=1,
        person_id=PERSON_ID,
        appearances=[{"color": (70, 90, 110)}],
    )
    service.reconcile_job(job_id)

    async def upload_remote(*args, **kwargs):
        return 200, _upload_receipt()

    monkeypatch.setattr(
        portraits_module.remote,
        "upload_project_person_portrait",
        upload_remote,
    )
    asyncio.run(service.drain_pending_async())
    return job_id


def test_project_reassociation_retires_old_project_and_syncs_new_after_reload(
    tmp_path,
    monkeypatch,
):
    identity, bindings, workflow, service = _workflow_state(tmp_path)
    local_person_id = "12121212-1212-4212-8212-121212121212"
    _set_hosted_alias(
        bindings,
        workflow["workflowRef"],
        local_person_id,
        PERSON_ID,
    )
    _sync_one_project_portrait(identity, workflow, service, monkeypatch)
    new_project = "33333333-3333-4333-8333-333333333333"
    bindings.associate(workflow["workflowRef"], new_project, "production")

    reloaded_bindings = BindingStore(str(tmp_path / "bindings.json"))
    reloaded = ProjectPortraitService(
        identity,
        reloaded_bindings,
        connection_path=str(tmp_path / "connection.json"),
    )
    results = reloaded.reconcile_completed_jobs()
    assert {result["state"] for result in results} == {"sync_pending"}
    operations = portraits_module._read_json(reloaded.outbox_path)["operations"].values()
    assert {(operation["projectId"], operation["state"]) for operation in operations} == {
        (PROJECT_ID, "retire_pending"),
        (new_project, "waiting_for_person"),
    }
    calls = []

    async def retire_remote(_connection, project, *_args, **_kwargs):
        calls.append(("retire", project))
        return 200, {"success": True}

    async def upload_remote(_connection, project, person, *_args, **_kwargs):
        calls.append(("upload", project, person))
        return 200, _upload_receipt("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")

    monkeypatch.setattr(portraits_module.remote, "retire_project_person_portrait", retire_remote)
    monkeypatch.setattr(portraits_module.remote, "upload_project_person_portrait", upload_remote)
    asyncio.run(reloaded.drain_pending_async())
    assert calls == [("retire", PROJECT_ID)]

    new_project_person = "45454545-4545-4545-8545-454545454545"
    reloaded_bindings.record_workspace_alias(
        workflow["workflowRef"],
        new_project,
        local_person_id,
        new_project_person,
        "new",
        "e" * 64,
    )
    asyncio.run(reloaded.drain_pending_async())
    assert calls == [
        ("retire", PROJECT_ID),
        ("upload", new_project, new_project_person),
    ]
    assert not any(
        call[0] == "upload" and call[-1] == PERSON_ID
        for call in calls
    )


def test_explicit_project_disassociation_retires_old_portrait_after_reload(
    tmp_path,
    monkeypatch,
):
    identity, bindings, workflow, service = _workflow_state(tmp_path)
    _sync_one_project_portrait(identity, workflow, service, monkeypatch)
    bindings.disassociate(workflow["workflowRef"])
    reloaded = ProjectPortraitService(
        identity,
        BindingStore(str(tmp_path / "bindings.json")),
        connection_path=str(tmp_path / "connection.json"),
    )
    reloaded.reconcile_completed_jobs()
    operations = portraits_module._read_json(reloaded.outbox_path)["operations"].values()
    assert {(operation["projectId"], operation["state"]) for operation in operations} == {
        (PROJECT_ID, "retire_pending"),
    }


def test_reassociation_preserves_old_project_portrait_still_desired_by_other_workflow(
    tmp_path,
    monkeypatch,
):
    identity, bindings, first_workflow, service = _workflow_state(tmp_path)
    second_workflow = bindings.resolve_workflow("project-a-survivor-workflow")
    bindings.associate(second_workflow["workflowRef"], PROJECT_ID, "production")
    job_ids = []
    for index, workflow in enumerate((first_workflow, second_workflow), start=1):
        job_id = f"74000000-0000-4000-8000-00000000000{index}"
        job_ids.append(job_id)
        _write_multi_portrait_job(
            identity,
            job_id=job_id,
            workflow_ref=workflow["workflowRef"],
            cache_key=str(index + 3) * 64,
            created_order=1,
            person_id=PERSON_ID,
            appearances=[{"color": (40, 80, 120), "bbox": (80, 100)}],
        )
    service.reconcile_completed_jobs()

    async def upload_remote(*args, **kwargs):
        return 200, _upload_receipt()

    monkeypatch.setattr(portraits_module.remote, "upload_project_person_portrait", upload_remote)
    asyncio.run(service.drain_pending_async())
    before_rebind = portraits_module._read_json(service.outbox_path)["operations"].values()
    project_a_before = [
        operation for operation in before_rebind if operation["projectId"] == PROJECT_ID
    ]
    assert set(project_a_before[0]["workflowRefs"]) == {
        first_workflow["workflowRef"],
        second_workflow["workflowRef"],
    }
    new_project = "44444444-4444-4444-8444-444444444444"
    bindings.associate(first_workflow["workflowRef"], new_project, "production")
    service.reconcile_completed_jobs()
    operations = portraits_module._read_json(service.outbox_path)["operations"].values()
    project_a = [operation for operation in operations if operation["projectId"] == PROJECT_ID]
    assert len(project_a) == 1
    assert project_a[0]["state"] == "synced"
    assert project_a[0]["workflowRefs"] == [second_workflow["workflowRef"]]

    os.remove(os.path.join(identity.jobs_dir, f"{job_ids[1]}.json"))
    identity._jobs.pop(job_ids[1], None)
    before = deepcopy(portraits_module._read_json(service.outbox_path)["operations"])
    service.reconcile_completed_jobs()
    after = portraits_module._read_json(service.outbox_path)
    assert after["operations"] == before
    assert after["projectErrors"][PROJECT_ID]["code"] == "identity_evidence_unavailable"
    assert project_a[0]["state"] == "synced"
