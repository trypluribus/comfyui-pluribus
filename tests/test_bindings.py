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


def test_source_ref_must_have_been_minted_for_workflow(tmp_path):
    store = BindingStore(str(tmp_path / "bindings.json"))
    workflow = store.resolve_workflow("local-workflow")
    with pytest.raises(ValueError, match="not minted"):
        store.source_links_payload(
            workflow["workflowRef"],
            "project-1",
            {
                "workflowKind": "other",
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
