from __future__ import annotations

import asyncio
import json
import os
import uuid

import pytest

import pluribus.identity_decisions as decisions_module
from pluribus.bindings import BindingStore
from pluribus.identity_decisions import (
    IdentityDecisionService,
    identity_review_hash,
    person_source_projection,
    source_person_projection,
)
from pluribus.identity_service import (
    IdentityAnalysisService,
    IdentityConflictError,
)
from pluribus.storage import write_private_json


PERSON_A = "11111111-1111-4111-8111-111111111111"
PERSON_B = "22222222-2222-4222-8222-222222222222"
CANONICAL_A = "33333333-3333-4333-8333-333333333333"
CANONICAL_B = "44444444-4444-4444-8444-444444444444"


def _state(tmp_path, *, project=True, connected=False):
    bindings_path = str(tmp_path / "bindings.json")
    bindings = BindingStore(bindings_path)
    workflow = bindings.resolve_workflow("identity-decision-workflow")
    if project:
        bindings.associate(workflow["workflowRef"], "project-test", "production")
    sources = [
        bindings.resolve_source(
            workflow["workflowRef"], f"source-{index}.png", "reference"
        )["sourceRef"]
        for index in range(3)
    ]
    identity = IdentityAnalysisService(str(tmp_path / "state"), analyzer=object())
    job_id = str(uuid.uuid4())
    cache_key = "c" * 64
    cached = {
        "schemaVersion": 3,
        "candidates": [
            {
                "candidateId": "candidate-a",
                "occurrenceIds": ["a-shared"],
                "sourceRefs": [sources[0]],
            },
            {
                "candidateId": "candidate-b",
                "occurrenceIds": ["b-shared", "b-other"],
                "sourceRefs": [sources[0], sources[1]],
            },
            {
                "candidateId": "candidate-c",
                "occurrenceIds": ["c-third"],
                "sourceRefs": [sources[2]],
            },
        ],
        "occurrences": [
            {
                "occurrenceId": "a-shared",
                "candidateId": "candidate-a",
                "sourceRef": sources[0],
            },
            {
                "occurrenceId": "b-shared",
                "candidateId": "candidate-b",
                "sourceRef": sources[0],
            },
            {
                "occurrenceId": "b-other",
                "candidateId": "candidate-b",
                "sourceRef": sources[1],
            },
            {
                "occurrenceId": "c-third",
                "candidateId": "candidate-c",
                "sourceRef": sources[2],
            },
        ],
    }
    identity._write_cache(cache_key, cached)
    job = {
        "jobId": job_id,
        "state": "completed",
        "workflowRef": workflow["workflowRef"],
        "cacheKey": cache_key,
        "createdOrder": 1,
    }
    identity._write_job(job)
    connection_path = str(tmp_path / "connection.json")
    if connected:
        write_private_json(
            connection_path,
            {"token": "private-device-token", "server_url": "https://example.test"},
        )
    decisions = IdentityDecisionService(
        identity, bindings, connection_path=connection_path
    )
    return {
        "bindings": bindings,
        "identity": identity,
        "decisions": decisions,
        "workflow": workflow,
        "sources": sources,
        "cached": cached,
        "jobId": job_id,
        "connectionPath": connection_path,
    }


def _draft(state, draft_id, name, source_refs, **extra):
    return state["bindings"].put_person_draft(
        state["workflow"]["workflowRef"],
        {
            "draftId": draft_id,
            "displayName": name,
            "sourceRefs": source_refs,
            **extra,
        },
    )


def _confirmed(candidate_id, person_id, occurrence_ids):
    return {
        "candidateId": candidate_id,
        "personId": person_id,
        "state": "confirmed",
        "occurrenceIds": occurrence_ids,
    }


def _decision(state, *, base_revision, candidate="candidate-b", **overrides):
    body = {
        "baseRevision": base_revision,
        "candidateId": candidate,
        "decision": "confirmed",
        "occurrenceIds": ["b-other"],
        "action": "assign",
        "target": {"draftId": PERSON_A},
        "mergeDraftIds": [],
        **overrides,
    }
    return state["decisions"].put_decision(state["jobId"], body)


def test_complete_projector_keeps_one_person_across_candidates_and_sources(tmp_path):
    state = _state(tmp_path)
    links = [
        _confirmed("candidate-a", PERSON_A, ["a-shared"]),
        _confirmed("candidate-b", PERSON_A, ["b-other"]),
    ]

    assert person_source_projection(links, state["cached"]) == {
        PERSON_A: sorted(state["sources"][:2])
    }
    assert source_person_projection(links, state["cached"]) == {
        state["sources"][0]: [PERSON_A],
        state["sources"][1]: [PERSON_A],
    }
    assert identity_review_hash(links) == identity_review_hash(list(reversed(links)))


def test_projector_includes_manual_source_only_drafts_and_resolves_aliases(tmp_path):
    state = _state(tmp_path)

    assert source_person_projection(
        [],
        state["cached"],
        {PERSON_B: PERSON_A},
        {PERSON_B: {"sourceRefs": [state["sources"][2]]}},
    ) == {
        state["sources"][2]: [PERSON_A],
    }


def test_decision_projects_sources_from_all_candidates_not_only_edited_candidate(
    tmp_path,
):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    state["identity"].put_links(
        state["jobId"],
        {
            "baseRevision": 0,
            "links": [_confirmed("candidate-a", PERSON_A, ["a-shared"])],
        },
    )

    result = _decision(state, base_revision=1)

    draft = next(value for value in result["personDrafts"] if value["draftId"] == PERSON_A)
    assert draft["sourceRefs"] == sorted(state["sources"][:2])
    assert result["syncState"] == "saved_local"
    assert result["syncDetails"]["clientPersonId"] == PERSON_A


def test_assign_replaces_checked_appearances_in_candidate_and_keeps_other_candidates(
    tmp_path,
):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Nisreen", state["sources"][:2])
    state["identity"].put_links(
        state["jobId"],
        {
            "baseRevision": 0,
            "links": [
                _confirmed("candidate-a", PERSON_A, ["a-shared"]),
                _confirmed("candidate-b", PERSON_A, ["b-shared"]),
            ],
        },
    )

    result = _decision(state, base_revision=1)

    by_candidate = {
        value["candidateId"]: value
        for value in result["links"]
        if value.get("personId") == PERSON_A
    }
    assert by_candidate["candidate-a"]["occurrenceIds"] == ["a-shared"]
    assert by_candidate["candidate-b"]["occurrenceIds"] == ["b-other"]


def test_assign_uses_complete_target_selection_then_allows_unchecking(tmp_path):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Source person", [state["sources"][0]])
    _draft(state, PERSON_B, "Target person", [state["sources"][1]])
    state["identity"].put_links(
        state["jobId"],
        {
            "baseRevision": 0,
            "links": [
                _confirmed("candidate-b", PERSON_A, ["b-shared"]),
                _confirmed("candidate-b", PERSON_B, ["b-other"]),
            ],
        },
    )

    assigned = _decision(
        state,
        base_revision=1,
        occurrenceIds=["b-shared", "b-other"],
        target={"draftId": PERSON_B},
    )
    target_link = next(
        value
        for value in assigned["links"]
        if value.get("personId") == PERSON_B
    )
    assert target_link["occurrenceIds"] == ["b-other", "b-shared"]
    assert all(value.get("personId") != PERSON_A for value in assigned["links"])

    unchecked = _decision(
        state,
        base_revision=assigned["revision"],
        occurrenceIds=["b-shared"],
        target={"draftId": PERSON_B},
    )
    target_link = next(
        value
        for value in unchecked["links"]
        if value.get("personId") == PERSON_B
    )
    assert target_link["occurrenceIds"] == ["b-shared"]


def test_derived_source_membership_drops_after_last_occurrence_is_unchecked(
    tmp_path,
):
    state = _state(tmp_path, project=False)
    first = state["decisions"].put_decision(
        state["jobId"],
        {
            "baseRevision": 0,
            "candidateId": "candidate-b",
            "decision": "confirmed",
            "occurrenceIds": ["b-shared", "b-other"],
            "action": "assign",
            "target": {"displayName": "Derived person"},
            "mergeDraftIds": [],
        },
    )
    draft_id = first["personDrafts"][0]["draftId"]
    assert first["personDrafts"][0]["sourceRefs"] == sorted(state["sources"][:2])

    second = state["decisions"].put_decision(
        state["jobId"],
        {
            "baseRevision": first["revision"],
            "candidateId": "candidate-b",
            "decision": "confirmed",
            "occurrenceIds": ["b-shared"],
            "action": "assign",
            "target": {"draftId": draft_id},
            "mergeDraftIds": [],
        },
    )

    assert second["personDrafts"][0]["sourceRefs"] == [state["sources"][0]]


def test_explicit_manual_source_membership_survives_occurrence_uncheck(tmp_path):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Manual person", [state["sources"][1]])
    first = _decision(
        state,
        base_revision=0,
        occurrenceIds=["b-shared", "b-other"],
    )
    second = _decision(
        state,
        base_revision=first["revision"],
        occurrenceIds=["b-shared"],
    )

    assert second["personDrafts"][0]["sourceRefs"] == sorted(state["sources"][:2])


def test_explicit_combine_rewrites_all_links_and_persists_alias_tombstone(tmp_path):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    _draft(state, PERSON_B, "Nisreen duplicate", [state["sources"][1]])
    state["identity"].put_links(
        state["jobId"],
        {
            "baseRevision": 0,
            "links": [
                _confirmed("candidate-a", PERSON_A, ["a-shared"]),
                _confirmed("candidate-b", PERSON_B, ["b-other"]),
            ],
        },
    )

    result = _decision(
        state,
        base_revision=1,
        action="combine",
        mergeDraftIds=[PERSON_B],
    )

    assert {value.get("personId") for value in result["links"]} == {PERSON_A}
    assert [value["draftId"] for value in result["personDrafts"]] == [PERSON_A]
    assert state["bindings"].resolve_person_alias(
        state["workflow"]["workflowRef"], PERSON_B
    ) == PERSON_A
    assert state["bindings"].list_person_draft_tombstones(
        state["workflow"]["workflowRef"]
    )[0]["mergedIntoDraftId"] == PERSON_A
    assert result["personDrafts"][0]["sourceRefs"] == sorted(state["sources"][:2])

    restarted = IdentityDecisionService(
        state["identity"],
        BindingStore(state["bindings"].path),
        connection_path=state["connectionPath"],
    )
    assert restarted.reconciliation_preview(state["jobId"])["counts"]["tombstones"] == 1


def test_combine_unions_same_candidate_appearances_before_tombstoning_alias(tmp_path):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    _draft(state, PERSON_B, "Nisreen duplicate", [state["sources"][1]])
    state["identity"].put_links(
        state["jobId"],
        {
            "baseRevision": 0,
            "links": [
                _confirmed("candidate-b", PERSON_A, ["b-shared"]),
                _confirmed("candidate-b", PERSON_B, ["b-other"]),
            ],
        },
    )

    result = _decision(
        state,
        base_revision=1,
        occurrenceIds=["b-other"],
        target={"draftId": PERSON_A},
        action="combine",
        mergeDraftIds=[PERSON_B],
    )

    assert result["links"] == [
        {
            "candidateId": "candidate-b",
            "personId": PERSON_A,
            "state": "confirmed",
            "displayName": "Nisreen",
            "occurrenceIds": ["b-other", "b-shared"],
        }
    ]
    assert [value["draftId"] for value in result["personDrafts"]] == [PERSON_A]
    assert result["personDrafts"][0]["sourceRefs"] == sorted(state["sources"][:2])
    assert state["identity"].get_links(state["jobId"])["links"] == result["links"]
    assert state["decisions"].pending_sync_entries()[-1]["sourcePeople"] == [
        {"sourceRef": source_ref, "personIds": [PERSON_A]}
        for source_ref in sorted(state["sources"][:2])
    ]
    assert state["bindings"].resolve_person_alias(
        state["workflow"]["workflowRef"], PERSON_B
    ) == PERSON_A


def test_combine_rejects_drafts_mapped_to_different_project_people(tmp_path):
    state = _state(tmp_path)
    _draft(
        state,
        PERSON_A,
        "Person A",
        [state["sources"][0]],
        canonicalPersonId="canonical-a",
    )
    _draft(
        state,
        PERSON_B,
        "Person B",
        [state["sources"][1]],
        canonicalPersonId="canonical-b",
    )

    with pytest.raises(ValueError, match="different project people"):
        _decision(
            state,
            base_revision=0,
            action="combine",
            mergeDraftIds=[PERSON_B],
        )


def test_combine_preserves_alias_history_when_switching_back_to_another_project(
    tmp_path,
):
    state = _state(tmp_path)
    workflow_ref = state["workflow"]["workflowRef"]
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    _draft(state, PERSON_B, "Nisreen duplicate", [state["sources"][1]])
    for draft_id in (PERSON_A, PERSON_B):
        state["bindings"].record_workspace_alias(
            workflow_ref,
            "project-test",
            draft_id,
            CANONICAL_A,
            "existing",
            "a" * 64,
        )

    state["bindings"].associate(workflow_ref, "project-b", "production")
    for draft_id in (PERSON_A, PERSON_B):
        state["bindings"].record_workspace_alias(
            workflow_ref,
            "project-b",
            draft_id,
            CANONICAL_B,
            "existing",
            "b" * 64,
        )
    state["bindings"].associate(workflow_ref, "project-test", "production")

    _decision(
        state,
        base_revision=0,
        action="combine",
        target={"draftId": PERSON_A},
        mergeDraftIds=[PERSON_B],
    )
    state["bindings"].associate(workflow_ref, "project-b", "production")

    survivor = state["bindings"].list_person_drafts(workflow_ref)[0]
    tombstone = state["bindings"].list_person_draft_tombstones(workflow_ref)[0]
    assert survivor["canonicalPersonId"] == CANONICAL_B
    assert tombstone["resolvedPersonId"] == CANONICAL_B
    assert tombstone["workspaceAlias"]["canonicalPersonId"] == CANONICAL_B
    with state["bindings"]._lock:
        binding = state["bindings"]._find(
            state["bindings"]._read(), workflow_ref
        )
    assert {
        project_id: marker["canonicalPersonId"]
        for project_id, marker in binding["person_draft_tombstones"][PERSON_B][
            "workspaceAliases"
        ].items()
    } == {
        "project-b": CANONICAL_B,
        "project-test": CANONICAL_A,
    }


def test_committed_decision_replays_without_new_revision_or_outbox_entry(tmp_path):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])

    first = _decision(state, base_revision=0)
    replay = _decision(state, base_revision=0)

    assert replay == first
    assert state["identity"].get_links(state["jobId"])["revision"] == 1
    assert len(state["decisions"].pending_sync_entries()) == 1


def test_interrupted_cross_file_commit_rolls_back_before_next_start(tmp_path, monkeypatch):
    state = _state(tmp_path, project=False)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    original_write = state["bindings"]._write
    failed = False

    def fail_binding_once(value):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("simulated binding write failure")
        return original_write(value)

    monkeypatch.setattr(state["bindings"], "_write", fail_binding_once)
    with pytest.raises(RuntimeError, match="simulated"):
        _decision(state, base_revision=0)
    monkeypatch.setattr(state["bindings"], "_write", original_write)

    restarted_identity = IdentityAnalysisService(
        str(tmp_path / "state"), analyzer=object()
    )
    restarted_bindings = BindingStore(state["bindings"].path)
    restarted = IdentityDecisionService(
        restarted_identity,
        restarted_bindings,
        connection_path=state["connectionPath"],
    )

    assert restarted_identity.get_links(state["jobId"])["revision"] == 0
    assert restarted.pending_sync_entries() == []
    assert restarted_bindings.list_person_drafts(
        state["workflow"]["workflowRef"]
    )[0]["sourceRefs"] == [state["sources"][0]]


def test_outbox_promotes_with_stable_client_id_before_manifest_ack(tmp_path, monkeypatch):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    calls = []

    async def create_person(_connection_path, project_id, body, fetch=None):
        calls.append((project_id, body))
        return 201, {"person": {"id": "canonical-person"}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)
    result = _decision(state, base_revision=0)

    assert result["syncState"] == "sync_pending"
    drained = asyncio.run(
        state["decisions"].drain_sync_entry(result["syncDetails"]["entryId"])
    )

    assert calls[0][1]["clientPersonId"] == PERSON_A
    assert calls[0][1]["mode"] == "new"
    assert drained["state"] == "sync_pending"
    draft = state["bindings"].list_person_drafts(
        state["workflow"]["workflowRef"]
    )[0]
    assert draft["canonicalPersonId"] == "canonical-person"
    assert state["decisions"].pending_sync_entries()[0]["sourcePeople"] == [
        {"sourceRef": source_ref, "personIds": ["canonical-person"]}
        for source_ref in sorted(state["sources"][:2])
    ]

    synced = state["decisions"].mark_workflow_revision_synced(
        state["workflow"]["workflowRef"], result["revision"]
    )
    assert synced[0]["state"] == "synced"
    assert state["decisions"].pending_sync_entries() == []


def test_concurrent_pending_drains_post_each_logical_person_once(tmp_path, monkeypatch):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Selected person", [state["sources"][0]])
    _draft(state, PERSON_B, "Manual source-only person", [state["sources"][2]])
    _decision(state, base_revision=0)
    calls = []
    first_call_started = asyncio.Event()
    release_first_call = asyncio.Event()

    async def create_person(_connection_path, _project_id, body, fetch=None):
        calls.append(body)
        if len(calls) == 1:
            first_call_started.set()
            await release_first_call.wait()
        canonical_id = (
            CANONICAL_A
            if body["clientPersonId"] == PERSON_A
            else CANONICAL_B
        )
        return 201, {"person": {"id": canonical_id}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)

    async def run_concurrent_drains():
        drains = [
            asyncio.create_task(state["decisions"].drain_pending_async())
            for _ in range(12)
        ]
        await asyncio.wait_for(first_call_started.wait(), timeout=1)
        # Give every trigger a chance to join the in-flight drain before its
        # first remote operation is allowed to finish.
        await asyncio.sleep(0)
        release_first_call.set()
        return await asyncio.gather(*drains)

    results = asyncio.run(run_concurrent_drains())

    assert [value["clientPersonId"] for value in calls].count(PERSON_A) == 1
    assert [value["clientPersonId"] for value in calls].count(PERSON_B) == 1
    assert len(calls) == 2
    assert len(results) == 12
    assert all(result[0]["personPhaseState"] == "synced" for result in results)
    assert all(result == results[0] for result in results[1:])


def test_direct_entry_and_background_drain_share_one_person_post(tmp_path, monkeypatch):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    result = _decision(state, base_revision=0)
    entry_id = result["syncDetails"]["entryId"]
    calls = []
    direct_call_started = asyncio.Event()
    release_direct_call = asyncio.Event()

    async def create_person(_connection_path, _project_id, body, fetch=None):
        calls.append(body)
        direct_call_started.set()
        await release_direct_call.wait()
        return 201, {"person": {"id": CANONICAL_A}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)

    async def race_immediate_save_with_background_drain():
        direct = asyncio.create_task(state["decisions"].drain_sync_entry(entry_id))
        await asyncio.wait_for(direct_call_started.wait(), timeout=1)
        background = asyncio.create_task(state["decisions"].drain_pending_async())
        await asyncio.sleep(0)
        release_direct_call.set()
        return await direct, await background

    direct_state, background_states = asyncio.run(
        race_immediate_save_with_background_drain()
    )

    assert len(calls) == 1
    assert calls[0]["clientPersonId"] == PERSON_A
    assert direct_state["personPhaseState"] == "synced"
    assert background_states == [direct_state]


def test_concurrent_failed_drain_singleflights_then_later_retry_recovers(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    _decision(state, base_revision=0)
    calls = []
    failed_call_started = asyncio.Event()
    release_failed_call = asyncio.Event()

    async def create_person(_connection_path, _project_id, body, fetch=None):
        calls.append(body)
        if len(calls) == 1:
            failed_call_started.set()
            await release_failed_call.wait()
            return 503, {"state": "offline"}
        return 201, {"person": {"id": CANONICAL_A}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)

    async def run_failed_wave():
        drains = [
            asyncio.create_task(state["decisions"].drain_pending_async())
            for _ in range(10)
        ]
        await asyncio.wait_for(failed_call_started.wait(), timeout=1)
        await asyncio.sleep(0)
        release_failed_call.set()
        return await asyncio.gather(*drains)

    failed_results = asyncio.run(run_failed_wave())

    assert len(calls) == 1
    assert len(failed_results) == 10
    assert all(result == failed_results[0] for result in failed_results[1:])
    assert failed_results[0][0]["personPhaseState"] == "pending"

    # A new explicit retry may run in a new asyncio.run loop.  It must create a
    # fresh singleflight, re-read the durable pending operation, and recover.
    retried = asyncio.run(state["decisions"].drain_pending_async())

    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert retried[0]["personPhaseState"] == "synced"
    assert state["decisions"].pending_sync_entries()[0]["attemptCount"] == 2


def test_manual_source_only_draft_is_promoted_and_projected_after_mapping(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Selected person", [state["sources"][0]])
    _draft(state, PERSON_B, "Manual source-only person", [state["sources"][2]])
    calls = []

    async def create_person(_connection_path, _project_id, body, fetch=None):
        calls.append(body)
        return 201, {
            "person": {
                "id": CANONICAL_A if body["clientPersonId"] == PERSON_A else "44444444-4444-4444-8444-444444444444"
            }
        }

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)
    result = _decision(state, base_revision=0)
    entry = state["decisions"].pending_sync_entries()[0]
    source_only = next(
        value
        for value in entry["sourcePeople"]
        if value["sourceRef"] == state["sources"][2]
    )
    assert source_only["personIds"] == [PERSON_B]
    assert {value["clientPersonId"] for value in entry["people"]} == {
        PERSON_A,
        PERSON_B,
    }

    asyncio.run(state["decisions"].drain_sync_entry(result["syncDetails"]["entryId"]))

    entry = state["decisions"].pending_sync_entries()[0]
    source_only = next(
        value
        for value in entry["sourcePeople"]
        if value["sourceRef"] == state["sources"][2]
    )
    assert source_only["personIds"] == ["44444444-4444-4444-8444-444444444444"]
    assert {value["clientPersonId"] for value in calls} == {PERSON_A, PERSON_B}


def test_source_only_decision_persists_exact_selected_source_subset(tmp_path):
    state = _state(tmp_path, project=False)
    state["cached"]["candidates"].append(
        {
            "candidateId": "candidate-source-only",
            "occurrenceIds": [],
            "sourceRefs": state["sources"][:2],
        }
    )
    state["identity"]._write_cache("c" * 64, state["cached"])

    result = state["decisions"].put_decision(
        state["jobId"],
        {
            "baseRevision": 0,
            "candidateId": "candidate-source-only",
            "decision": "confirmed",
            "occurrenceIds": [],
            "sourceRefs": [state["sources"][0]],
            "action": "assign",
            "target": {"displayName": "Source-only person"},
            "mergeDraftIds": [],
        },
    )

    assert result["links"][0]["sourceRefs"] == [state["sources"][0]]
    assert result["personDrafts"][0]["sourceRefs"] == [state["sources"][0]]
    assert state["identity"].get_links(state["jobId"])["links"][0][
        "sourceRefs"
    ] == [state["sources"][0]]
    assert state["sources"][1] not in source_person_projection(
        result["links"], state["cached"]
    )


def test_workspace_alias_receipt_prevents_future_person_post_after_restart(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    calls = []

    async def create_person(_connection_path, _project_id, body, fetch=None):
        calls.append(body)
        return 201, {"person": {"id": CANONICAL_A}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)
    first = _decision(state, base_revision=0)
    asyncio.run(
        state["decisions"].drain_sync_entry(first["syncDetails"]["entryId"])
    )
    state["decisions"].mark_workflow_revision_synced(
        state["workflow"]["workflowRef"], first["revision"]
    )
    persisted = state["bindings"].list_person_drafts(
        state["workflow"]["workflowRef"]
    )[0]
    assert persisted["workspaceAlias"] == {
        "state": "synced",
        "clientPersonId": PERSON_A,
        "canonicalPersonId": CANONICAL_A,
        "requestMode": "new",
        "requestHash": persisted["workspaceAlias"]["requestHash"],
    }

    restarted = IdentityDecisionService(
        state["identity"],
        BindingStore(state["bindings"].path),
        connection_path=state["connectionPath"],
    )
    second = restarted.put_decision(
        state["jobId"],
        {
            "baseRevision": first["revision"],
            "candidateId": "candidate-c",
            "decision": "confirmed",
            "occurrenceIds": ["c-third"],
            "action": "assign",
            "target": {"draftId": PERSON_A},
            "mergeDraftIds": [],
        },
    )
    assert second["syncDetails"]["personPhaseState"] == "synced"
    asyncio.run(restarted.drain_sync_entry(second["syncDetails"]["entryId"]))
    assert len(calls) == 1
    assert restarted.mark_workflow_revision_synced(
        state["workflow"]["workflowRef"], second["revision"]
    )
    assert restarted.pending_sync_entries() == []


def test_bound_but_disconnected_decision_requires_reconnect(tmp_path):
    state = _state(tmp_path, connected=False)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])

    result = _decision(state, base_revision=0)

    assert result["syncState"] == "reconnect_required"


def test_saved_local_entry_hydrates_project_after_later_association_and_restart(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, project=False, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    result = _decision(state, base_revision=0)
    assert result["syncState"] == "saved_local"

    state["bindings"].associate(
        state["workflow"]["workflowRef"], "project-later", "production"
    )
    restarted = IdentityDecisionService(
        state["identity"],
        BindingStore(state["bindings"].path),
        connection_path=state["connectionPath"],
    )
    calls = []

    async def create_person(_connection_path, project_id, body, fetch=None):
        calls.append((project_id, body))
        return 201, {"person": {"id": CANONICAL_A}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)
    drained = asyncio.run(
        restarted.drain_sync_entry(result["syncDetails"]["entryId"])
    )

    assert calls[0][0] == "project-later"
    assert calls[0][1]["clientPersonId"] == PERSON_A
    assert drained["state"] == "sync_pending"
    assert restarted.pending_sync_entries()[0]["projectId"] == "project-later"


def test_latest_manifest_ack_clears_prior_revisions_but_not_future(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])

    async def create_person(_connection_path, _project_id, _body, fetch=None):
        return 201, {"person": {"id": CANONICAL_A}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)
    first = _decision(state, base_revision=0)
    asyncio.run(
        state["decisions"].drain_sync_entry(first["syncDetails"]["entryId"])
    )
    second = _decision(
        state,
        base_revision=first["revision"],
        candidate="candidate-c",
        occurrenceIds=["c-third"],
    )
    third = _decision(
        state,
        base_revision=second["revision"],
        candidate="candidate-a",
        occurrenceIds=["a-shared"],
    )

    changed = state["decisions"].mark_workflow_revision_synced(
        state["workflow"]["workflowRef"], second["revision"]
    )

    assert {value["revision"] for value in changed} == {
        first["revision"],
        second["revision"],
    }
    assert [
        value["revision"] for value in state["decisions"].pending_sync_entries()
    ] == [third["revision"]]


def test_offline_combine_supersedes_duplicate_creation_and_attaches_alias(
    tmp_path, monkeypatch
):
    state = _state(tmp_path, connected=False)
    _draft(state, PERSON_A, "Survivor", [state["sources"][0]])
    _draft(state, PERSON_B, "Duplicate", [state["sources"][1]])
    first = _decision(
        state,
        base_revision=0,
        candidate="candidate-a",
        occurrenceIds=["a-shared"],
        target={"draftId": PERSON_A},
    )
    second = _decision(
        state,
        base_revision=first["revision"],
        occurrenceIds=["b-other"],
        target={"draftId": PERSON_B},
    )
    combined = _decision(
        state,
        base_revision=second["revision"],
        occurrenceIds=["b-other"],
        action="combine",
        target={"draftId": PERSON_A},
        mergeDraftIds=[PERSON_B],
    )

    pre_reconnect = state["decisions"].pending_sync_entries()
    old_duplicate_op = next(
        operation
        for entry in pre_reconnect
        for operation in entry["people"]
        if operation["clientPersonId"] == PERSON_B
        and operation.get("operationKind") == "person"
    )
    assert old_duplicate_op["state"] == "superseded"
    assert "requestBody" not in old_duplicate_op

    # Simulate an older outbox written before tombstone supersession existed.
    # Restart-time drain must still neutralize this mode:new operation before
    # any network request can create the duplicate.
    legacy_outbox = json.loads(
        open(state["decisions"].outbox_path, encoding="utf-8").read()
    )
    legacy_duplicate_op = next(
        operation
        for entry in legacy_outbox["entries"].values()
        for operation in entry["people"]
        if operation["clientPersonId"] == PERSON_B
        and operation.get("operationKind") == "person"
    )
    legacy_duplicate_op["state"] = "pending"
    legacy_duplicate_op["requestBody"] = {
        "mode": "new",
        "clientPersonId": PERSON_B,
        "displayName": "Duplicate",
    }
    write_private_json(state["decisions"].outbox_path, legacy_outbox)

    write_private_json(
        state["connectionPath"],
        {"token": "private-device-token", "server_url": "https://example.test"},
    )
    restarted = IdentityDecisionService(
        state["identity"],
        BindingStore(state["bindings"].path),
        connection_path=state["connectionPath"],
    )
    calls = []

    async def create_person(_connection_path, _project_id, body, fetch=None):
        calls.append(body)
        if body["mode"] == "new":
            assert body["clientPersonId"] == PERSON_A
        else:
            assert body == {
                "mode": "existing",
                "clientPersonId": PERSON_B,
                "talentRecordId": CANONICAL_A,
            }
        return 201, {"person": {"id": CANONICAL_A}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", create_person)
    asyncio.run(restarted.drain_pending_async())

    assert [(value["mode"], value["clientPersonId"]) for value in calls] == [
        ("new", PERSON_A),
        ("existing", PERSON_B),
    ]
    tombstone = restarted.bindings.list_person_draft_tombstones(
        state["workflow"]["workflowRef"]
    )[0]
    assert tombstone["workspaceAlias"]["canonicalPersonId"] == CANONICAL_A
    assert restarted.bindings.list_person_drafts(
        state["workflow"]["workflowRef"]
    )[0]["canonicalPersonId"] == CANONICAL_A
    assert all(
        value["personPhaseState"] == "synced"
        for value in restarted.pending_sync_entries()
    )
    restarted.mark_workflow_revision_synced(
        state["workflow"]["workflowRef"], combined["revision"]
    )
    assert restarted.pending_sync_entries() == []

    replay = IdentityDecisionService(
        state["identity"],
        BindingStore(state["bindings"].path),
        connection_path=state["connectionPath"],
    )
    asyncio.run(replay.drain_pending_async())
    assert len(calls) == 2


def test_manifest_ack_cannot_clear_entry_when_person_phase_failed(tmp_path, monkeypatch):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])

    async def unavailable(*_args, **_kwargs):
        return 503, {"state": "offline"}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", unavailable)
    result = _decision(state, base_revision=0)
    drained = asyncio.run(
        state["decisions"].drain_sync_entry(result["syncDetails"]["entryId"])
    )

    assert drained["state"] == "sync_pending"
    assert state["decisions"].mark_workflow_revision_synced(
        state["workflow"]["workflowRef"], result["revision"]
    ) == []
    assert len(state["decisions"].pending_sync_entries()) == 1


def test_retry_reuses_frozen_new_person_request_after_mapping_write(tmp_path, monkeypatch):
    state = _state(tmp_path, connected=True)
    _draft(state, PERSON_A, "Nisreen", [state["sources"][0]])
    result = _decision(state, base_revision=0)
    entry = state["decisions"].pending_sync_entries()[0]
    frozen_request = entry["people"][0]["requestBody"]
    assert frozen_request["mode"] == "new"

    # Simulate a crash after the canonical binding write but before the outbox
    # person operation was marked complete.
    draft = state["bindings"].list_person_drafts(
        state["workflow"]["workflowRef"]
    )[0]
    state["bindings"].put_person_draft(
        state["workflow"]["workflowRef"],
        {**draft, "canonicalPersonId": "canonical-person"},
    )
    calls = []

    async def replay(_connection_path, _project_id, body, fetch=None):
        calls.append(body)
        return 200, {"person": {"id": "canonical-person"}}

    monkeypatch.setattr(decisions_module.remote, "create_project_person", replay)
    drained = asyncio.run(
        state["decisions"].drain_sync_entry(result["syncDetails"]["entryId"])
    )

    assert calls == [frozen_request]
    assert calls[0]["mode"] == "new"
    assert drained["personPhaseState"] == "synced"
    # The manifest phase remains pending and does not post the person again.
    asyncio.run(
        state["decisions"].drain_sync_entry(result["syncDetails"]["entryId"])
    )
    assert calls == [frozen_request]


def test_reconciliation_preview_is_deterministic_and_never_auto_merges(tmp_path):
    state = _state(tmp_path, project=False)
    _draft(
        state,
        PERSON_A,
        "  Nisreen  Salem ",
        [state["sources"][0]],
        talentEmail="nisreen@example.com",
    )
    _draft(
        state,
        PERSON_B,
        "nisreen salem",
        [state["sources"][1]],
        representative={"email": "NISREEN@example.com"},
    )
    link_path = state["identity"]._links_path_for_workflow_ref(
        state["workflow"]["workflowRef"]
    )
    write_private_json(
        link_path,
        {
            "schemaVersion": 3,
            "analysisJobId": state["jobId"],
            "analysisCacheKey": "c" * 64,
            "revision": 1,
            "links": [
                _confirmed("candidate-a", PERSON_A, ["a-shared"]),
                _confirmed("candidate-a", PERSON_B, ["a-shared"]),
            ],
        },
    )
    before_bindings = open(state["bindings"].path, encoding="utf-8").read()
    before_links = open(link_path, encoding="utf-8").read()

    preview = state["decisions"].reconciliation_preview(state["jobId"])

    assert preview["readOnly"] is True
    assert preview["counts"]["ownershipConflicts"] == 1
    assert preview["counts"]["suspectedAliasPairs"] == 1
    assert preview["suspectedAliasPairs"][0]["evidence"] == [
        "exact_normalized_name",
        "exact_normalized_contact",
    ]
    assert preview["suspectedAliasPairs"][0]["requiresExplicitCombine"] is True
    assert len(state["bindings"].list_person_drafts(state["workflow"]["workflowRef"])) == 2
    assert open(state["bindings"].path, encoding="utf-8").read() == before_bindings
    assert open(link_path, encoding="utf-8").read() == before_links


def test_reconciliation_preview_does_not_confuse_shared_representative_contact(tmp_path):
    state = _state(tmp_path, project=False)
    _draft(
        state,
        PERSON_A,
        "Nisreen Salem",
        [state["sources"][0]],
        representative={"email": "manager@example.com"},
    )
    _draft(
        state,
        PERSON_B,
        "Sawsan Mustafa",
        [state["sources"][1]],
        representative={"email": "MANAGER@example.com"},
    )

    preview = state["decisions"].reconciliation_preview(state["jobId"])

    assert preview["counts"]["suspectedAliasPairs"] == 0
    assert preview["suspectedAliasPairs"] == []
