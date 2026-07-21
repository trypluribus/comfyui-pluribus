import json
import os
import re
import uuid

import pytest

from pluribus.bindings import BindingStore, normalize_source_links


GRAPH_HASH = "a" * 64


def test_workflow_and_source_refs_are_random_stable_and_private(tmp_path):
    path = str(tmp_path / "bindings.json")
    store = BindingStore(path)

    first = store.resolve_workflow("/private/jobs/real-brand-storyboard.json", GRAPH_HASH)
    again = store.resolve_workflow("/private/jobs/real-brand-storyboard.json")
    other = store.resolve_workflow("/private/jobs/other-storyboard.json")

    assert uuid.UUID(first["workflowRef"])
    assert first["workflowRef"] == again["workflowRef"]
    assert first["workflowRef"] != other["workflowRef"]
    assert first["graphHash"] == GRAPH_HASH

    source = store.resolve_source(
        first["workflowRef"], "/Users/person/reference/alex-final.png", "reference"
    )
    source_again = store.resolve_source(
        first["workflowRef"], "/Users/person/reference/alex-final.png", "reference"
    )
    assert re.fullmatch(r"[0-9a-f]{64}", source["sourceRef"])
    assert source == source_again
    assert store.source_matches_workflow(
        first["workflowRef"],
        source["sourceRef"],
        "/Users/person/reference/alex-final.png",
        "reference",
    )
    assert not store.source_matches_workflow(
        first["workflowRef"],
        source["sourceRef"],
        "/Users/person/reference/other.png",
        "reference",
    )
    assert not store.source_matches_workflow(
        first["workflowRef"],
        source["sourceRef"],
        "/Users/person/reference/alex-final.png",
        "audio",
    )

    with open(path, encoding="utf-8") as handle:
        private_text = handle.read()
    assert "real-brand-storyboard" not in private_text
    assert "alex-final" not in private_text
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"


def test_source_manifest_ignores_graph_hash_for_rights_hash_and_strips_local_fields(tmp_path):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("local-workflow", GRAPH_HASH)
    store.associate(workflow["workflowRef"], "project-1", "storyboard")
    source = store.resolve_source(workflow["workflowRef"], "secret-source-path.png", "reference")
    base = {
        "workflowRef": workflow["workflowRef"],
        "workflowKind": "storyboard",
        "baseManifestVersion": 0,
        "graphHash": GRAPH_HASH,
        "sources": [
            {
                "sourceRef": source["sourceRef"],
                "sourceKind": "reference",
                "sourceKey": "secret-source-path.png",
                "sourceNodeId": "42",
                "prompt": "put this person in the ad",
                "disposition": "linked",
                "talentRecordIds": ["talent-2", "talent-1", "talent-1"],
                "operations": [
                    {"node_id": "77", "class_type": "IPAdapter Advanced!"},
                    {"classType": "IPAdapterAdvanced"},
                ],
            }
        ],
    }

    first = store.source_links_payload(workflow["workflowRef"], "project-1", base)
    assert "manifestHash" not in store.get(workflow["workflowRef"])
    changed_graph = store.source_links_payload(
        workflow["workflowRef"],
        "project-1",
        {**base, "graphHash": "b" * 64},
    )

    assert first["manifestHash"] == changed_graph["manifestHash"]
    assert first["baseManifestVersion"] == 0
    assert changed_graph["graphHash"] == "b" * 64
    changed_kind = store.source_links_payload(
        workflow["workflowRef"],
        "project-1",
        {**base, "workflowKind": "character_sheet"},
    )
    assert changed_kind["manifestHash"] != first["manifestHash"]
    changed_operations = store.source_links_payload(
        workflow["workflowRef"],
        "project-1",
        {
            **base,
            "sources": [
                {
                    **base["sources"][0],
                    "operations": [{"classType": "KlingImage2VideoNode"}],
                }
            ],
        },
    )
    assert changed_operations["manifestHash"] != first["manifestHash"]
    outbound = json.dumps(first)
    assert "secret-source-path" not in outbound
    assert "put this person" not in outbound
    assert "node_id" not in outbound
    assert first["sources"][0]["talentRecordIds"] == ["talent-1", "talent-2"]
    assert first["sources"][0]["operations"] == [
        {"classType": "IPAdapterAdvanced"}
    ]

    synced = store.record_source_links(workflow["workflowRef"], "project-1", first)
    assert synced["manifestHash"] == first["manifestHash"]

    with pytest.raises(ValueError, match="baseManifestVersion"):
        store.source_links_payload(
            workflow["workflowRef"],
            "project-1",
            {key: value for key, value in base.items() if key != "baseManifestVersion"},
        )


def test_source_ref_must_have_been_minted_for_workflow(tmp_path):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("local-workflow")
    with pytest.raises(ValueError, match="not minted"):
        store.source_links_payload(
            workflow["workflowRef"],
            "project-1",
            {
                "workflowKind": "other",
                "baseManifestVersion": 0,
                "sources": [
                    {
                        "sourceRef": "c" * 64,
                        "sourceKind": "unknown",
                        "disposition": "review_required",
                        "talentRecordIds": [],
                        "operations": [],
                    }
                ],
            },
        )


def test_source_manifest_preserves_bounded_operation_input_roles(tmp_path):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("audio-workflow")
    source = store.resolve_source(
        workflow["workflowRef"], "dialogue.wav", "reference"
    )
    payload = store.source_links_payload(
        workflow["workflowRef"],
        "project-1",
        {
            "workflowKind": "production",
            "baseManifestVersion": 0,
            "sources": [
                {
                    "sourceRef": source["sourceRef"],
                    "sourceKind": "reference",
                    "disposition": "review_required",
                    "talentRecordIds": [],
                    "operations": [
                        {
                            "classType": "ByteDance2ReferenceNode",
                            "sourceRole": "reference_audio",
                        }
                    ],
                }
            ],
        },
    )

    assert payload["sources"][0]["operations"] == [
        {
            "classType": "ByteDance2ReferenceNode",
            "sourceRole": "reference_audio",
        }
    ]

    with pytest.raises(ValueError, match="sourceRole"):
        normalize_source_links(
            workflow_ref=workflow["workflowRef"],
            workflow_kind="production",
            graph_hash=None,
            sources=[
                {
                    "sourceRef": source["sourceRef"],
                    "sourceKind": "reference",
                    "disposition": "review_required",
                    "operations": [
                        {
                            "classType": "ByteDance2ReferenceNode",
                            "sourceRole": "private_socket_name",
                        }
                    ],
                }
            ],
        )


def test_local_person_drafts_support_many_to_many_source_links(tmp_path):
    path = str(tmp_path / "bindings.json")
    store = BindingStore(path)
    workflow = store.resolve_workflow("legacy-workflow")
    store.associate(workflow["workflowRef"], "project-1", "storyboard")
    source_a = store.resolve_source(
        workflow["workflowRef"], "/private/alex-closeup.png", "reference"
    )["sourceRef"]
    source_b = store.resolve_source(
        workflow["workflowRef"], "/private/alex-profile.png", "reference"
    )["sourceRef"]

    alex = store.put_person_draft(
        workflow["workflowRef"],
        {
            "canonicalPersonId": "person_123",
            "displayName": "  Alex Person  ",
            "role": "Lead actor",
            "talentEmail": "alex@example.com",
            "representative": {
                "role": "manager",
                "name": "Pat Manager",
                "email": "pat@example.com",
                "privateNodeId": "42",
            },
            "notes": "Confirm the profile and close-up are the same person.",
            "sourceRefs": [source_b, source_a, source_a],
            "workflow": {"private": "graph JSON must not persist"},
            "sourcePath": "/private/alex-closeup.png",
            "thumbnail": "data:image/png;base64,private",
        },
    )
    jordan = store.put_person_draft(
        workflow["workflowRef"],
        {
            "displayName": "Jordan Double",
            "sourceRefs": [source_a],
        },
    )

    assert uuid.UUID(alex["draftId"])
    assert alex["canonicalPersonId"] == "person_123"
    assert alex["sourceRefs"] == sorted([source_a, source_b])
    assert {draft["draftId"] for draft in store.list_person_drafts(
        workflow["workflowRef"], source_a
    )} == {alex["draftId"], jordan["draftId"]}
    assert store.list_person_drafts(workflow["workflowRef"], source_b) == [alex]

    with open(path, encoding="utf-8") as handle:
        persisted = json.load(handle)
    assert persisted["version"] == 1
    binding = next(iter(persisted["workflows"].values()))
    assert binding["project_id"] == "project-1"
    assert binding["workflow_kind"] == "storyboard"
    assert len(binding["person_drafts"]) == 2
    private_text = json.dumps(persisted)
    assert "graph JSON" not in private_text
    assert "alex-closeup" not in private_text
    assert "data:image" not in private_text
    assert "privateNodeId" not in private_text


def test_local_person_draft_upsert_replaces_fields_and_delete_is_scoped(tmp_path):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("workflow-one")
    other_workflow = store.resolve_workflow("workflow-two")
    source = store.resolve_source(
        workflow["workflowRef"], "person.png", "reference"
    )["sourceRef"]
    other_source = store.resolve_source(
        other_workflow["workflowRef"], "person.png", "reference"
    )["sourceRef"]
    first = store.put_person_draft(
        workflow["workflowRef"],
        {
            "displayName": "Alex",
            "role": "Actor",
            "representative": {"name": "Morgan"},
            "sourceRefs": [source],
        },
    )
    other = store.put_person_draft(
        other_workflow["workflowRef"],
        {"displayName": "Alex", "sourceRefs": [other_source]},
    )

    updated = store.put_person_draft(
        workflow["workflowRef"],
        {
            "draftId": first["draftId"],
            "displayName": "Alex Updated",
            "sourceRefs": [source],
        },
    )

    assert updated == {
        "draftId": first["draftId"],
        "displayName": "Alex Updated",
        "sourceRefs": [source],
    }
    assert store.delete_person_draft(workflow["workflowRef"], first["draftId"])
    assert not store.delete_person_draft(workflow["workflowRef"], first["draftId"])
    assert store.list_person_drafts(workflow["workflowRef"]) == []
    assert store.list_person_drafts(other_workflow["workflowRef"]) == [other]


def test_workspace_alias_receipt_survives_restart_and_public_put_cannot_forge_it(
    tmp_path,
):
    path = str(tmp_path / "bindings.json")
    store = BindingStore(path)
    workflow = store.resolve_workflow("workspace-alias-workflow")
    source = store.resolve_source(
        workflow["workflowRef"], "person.png", "reference"
    )["sourceRef"]
    draft = store.put_person_draft(
        workflow["workflowRef"],
        {
            "canonicalPersonId": "canonical-person",
            "displayName": "Alex",
            "sourceRefs": [source],
        },
    )
    marker = {
        "state": "synced",
        "clientPersonId": draft["draftId"],
        "canonicalPersonId": "canonical-person",
        "requestMode": "new",
        "requestHash": "a" * 64,
    }
    data = store._read()
    binding = store._find(data, workflow["workflowRef"])
    binding["person_drafts"][draft["draftId"]]["workspaceAlias"] = marker
    store._write(data)

    restarted = BindingStore(path)
    assert restarted.list_person_drafts(workflow["workflowRef"])[0][
        "workspaceAlias"
    ] == marker
    updated = restarted.put_person_draft(
        workflow["workflowRef"],
        {
            **draft,
            "displayName": "Alex Updated",
            "workspaceAlias": {
                **marker,
                "requestHash": "b" * 64,
            },
        },
    )

    assert updated["displayName"] == "Alex Updated"
    assert updated["workspaceAlias"] == marker


@pytest.mark.parametrize(
    "body, message",
    [
        ({"sourceRefs": []}, "non-empty"),
        (
            {"sourceRefs": ["SOURCE"], "talentEmail": "not-an-email"},
            "valid email",
        ),
        (
            {
                "sourceRefs": ["SOURCE"],
                "representative": {"role": "publicist"},
            },
            "not supported",
        ),
        (
            {"sourceRefs": ["SOURCE"], "displayName": "x" * 161},
            "at most 160",
        ),
        (
            {"sourceRefs": ["SOURCE"], "canonicalPersonId": "not opaque"},
            "opaque identifier",
        ),
    ],
)
def test_local_person_draft_validates_private_contract(tmp_path, body, message):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("workflow")
    source = store.resolve_source(
        workflow["workflowRef"], "person.png", "reference"
    )["sourceRef"]
    body = {
        key: ([source] if value == ["SOURCE"] else value)
        for key, value in body.items()
    }

    with pytest.raises(ValueError, match=message):
        store.put_person_draft(workflow["workflowRef"], body)


def test_local_person_draft_rejects_source_from_another_workflow(tmp_path):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("workflow-one")
    other_workflow = store.resolve_workflow("workflow-two")
    other_source = store.resolve_source(
        other_workflow["workflowRef"], "person.png", "reference"
    )["sourceRef"]

    with pytest.raises(ValueError, match="not minted"):
        store.put_person_draft(
            workflow["workflowRef"],
            {"displayName": "Alex", "sourceRefs": [other_source]},
        )


def test_local_source_reviews_are_private_scoped_and_reversible(tmp_path):
    path = str(tmp_path / "bindings.json")
    store = BindingStore(path)
    workflow = store.resolve_workflow("workflow-one")
    other_workflow = store.resolve_workflow("workflow-two")
    source = store.resolve_source(
        workflow["workflowRef"], "/private/nightmare-shadow.png", "reference"
    )["sourceRef"]
    other_source = store.resolve_source(
        other_workflow["workflowRef"], "/private/other.png", "reference"
    )["sourceRef"]

    assert store.list_source_reviews(workflow["workflowRef"]) == []
    assert store.put_source_review(
        workflow["workflowRef"], source, {"state": "not_person", "sourceHash": "a" * 64}
    ) == {"sourceRef": source, "state": "not_person", "sourceHash": "a" * 64}
    assert store.put_source_review(
        workflow["workflowRef"], source, {"state": "review_required", "sourceHash": "b" * 64}
    ) == {"sourceRef": source, "state": "review_required", "sourceHash": "b" * 64}
    assert store.list_source_reviews(workflow["workflowRef"]) == [
        {"sourceRef": source, "state": "review_required", "sourceHash": "b" * 64}
    ]
    assert store.list_source_reviews(other_workflow["workflowRef"]) == []

    with pytest.raises(ValueError, match="not supported"):
        store.put_source_review(
            workflow["workflowRef"], source, {"state": "person_added"}
        )
    with pytest.raises(ValueError, match="not minted"):
        store.put_source_review(
            workflow["workflowRef"], other_source, {"state": "not_person", "sourceHash": "c" * 64}
        )

    persisted = json.loads((tmp_path / "bindings.json").read_text(encoding="utf-8"))
    assert "/private" not in json.dumps(persisted)
    assert "nightmare-shadow" not in json.dumps(persisted)


def test_local_person_draft_pii_never_enters_outbound_source_manifest(tmp_path):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("private-workflow")
    store.associate(workflow["workflowRef"], "project-1", "storyboard")
    source_ref = store.resolve_source(
        workflow["workflowRef"], "/private/alex.png", "reference"
    )["sourceRef"]
    store.put_person_draft(
        workflow["workflowRef"],
        {
            "canonicalPersonId": "person_private_123",
            "displayName": "Alex Private Person",
            "talentEmail": "alex.private@example.com",
            "representative": {
                "role": "agent",
                "name": "Riley Private Rep",
                "email": "riley.private@example.com",
            },
            "notes": "Private casting and contact notes must remain local.",
            "sourceRefs": [source_ref],
        },
    )

    outbound = store.source_links_payload(
        workflow["workflowRef"],
        "project-1",
        {
            "workflowKind": "storyboard",
            "baseManifestVersion": 0,
            "sources": [
                {
                    "sourceRef": source_ref,
                    "sourceKind": "reference",
                    "disposition": "review_required",
                    "talentRecordIds": [],
                    "operations": [{"classType": "IPAdapter"}],
                }
            ],
        },
    )

    serialized = json.dumps(outbound)
    for private_value in (
        "Alex Private Person",
        "alex.private@example.com",
        "Riley Private Rep",
        "riley.private@example.com",
        "Private casting and contact notes must remain local.",
        "person_private_123",
    ):
        assert private_value not in serialized
    assert set(outbound) == {
        "workflowRef",
        "workflowKind",
        "baseManifestVersion",
        "sources",
        "manifestHash",
    }
    assert set(outbound["sources"][0]) == {
        "sourceRef",
        "sourceKind",
        "disposition",
        "talentRecordIds",
        "operations",
    }


def test_manifest_hash_is_order_stable():
    workflow_ref = str(uuid.uuid4())
    source_a = {
        "sourceRef": "a" * 64,
        "sourceKind": "reference",
        "disposition": "linked",
        "talentRecordIds": ["t2", "t1"],
        "operations": [{"classType": "KSampler"}, {"classType": "IPAdapter"}],
    }
    source_b = {
        "sourceRef": "b" * 64,
        "sourceKind": "lora",
        "disposition": "review_required",
        "talentRecordIds": [],
        "operations": [],
    }
    left = normalize_source_links(
        workflow_ref=workflow_ref,
        workflow_kind="production",
        graph_hash=None,
        sources=[source_a, source_b],
    )
    right = normalize_source_links(
        workflow_ref=workflow_ref,
        workflow_kind="production",
        graph_hash=None,
        sources=[source_b, source_a],
    )
    assert left == right

    relabeled = normalize_source_links(
        workflow_ref=workflow_ref,
        workflow_kind="production",
        graph_hash="f" * 64,
        sources=[{**source_a, "displayLabel": "Hero reference"}, source_b],
    )
    assert relabeled["manifestHash"] == left["manifestHash"]


def test_manifest_hash_matches_typescript_contract_example():
    payload = normalize_source_links(
        workflow_ref="11111111-1111-4111-8111-111111111111",
        workflow_kind="storyboard",
        graph_hash="f" * 64,
        sources=[
            {
                "sourceRef": "b" * 64,
                "sourceKind": "lora",
                "disposition": "linked",
                "talentRecordIds": ["33333333-3333-4333-8333-333333333333"],
                "operations": [
                    {"classType": "ReActorFaceSwap"},
                    {"classType": "IPAdapter"},
                ],
            },
        ],
    )
    assert payload["manifestHash"] == (
        "1c8d11070aa7066dafda1e7944614fc9ed95d88b9a4f6119f8d80ac1618caa7c"
    )


def test_identity_review_hash_changes_manifest_and_revision_is_forwarded_only():
    workflow_ref = str(uuid.uuid4())
    source = {
        "sourceRef": "a" * 64,
        "sourceKind": "reference",
        "disposition": "review_required",
        "talentRecordIds": [],
        "operations": [],
    }
    legacy = normalize_source_links(
        workflow_ref=workflow_ref,
        workflow_kind="production",
        graph_hash=None,
        sources=[source],
    )
    first = normalize_source_links(
        workflow_ref=workflow_ref,
        workflow_kind="production",
        graph_hash=None,
        sources=[source],
        identity_review_hash="b" * 64,
        identity_revision=7,
    )
    newer_revision = normalize_source_links(
        workflow_ref=workflow_ref,
        workflow_kind="production",
        graph_hash=None,
        sources=[source],
        identity_review_hash="b" * 64,
        identity_revision=8,
    )

    assert first["identityReviewHash"] == "b" * 64
    assert first["identityRevision"] == 7
    assert first["manifestHash"] != legacy["manifestHash"]
    assert newer_revision["manifestHash"] == first["manifestHash"]
    with pytest.raises(ValueError, match="identityReviewHash"):
        normalize_source_links(
            workflow_ref=workflow_ref,
            workflow_kind="production",
            graph_hash=None,
            sources=[source],
            identity_review_hash="not-a-hash",
        )
