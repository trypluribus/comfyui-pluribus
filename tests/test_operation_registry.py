import json
from pathlib import Path
import subprocess
import sys

from pluribus.operation_registry import (
    operation_actions,
    operation_registry,
    rights_relevant_operations,
)
from tools.generate_operation_registry import is_monorepo_checkout, projection_targets


ROOT = Path(__file__).resolve().parents[1]


def test_registry_covers_partner_nodes_and_audio():
    operations = rights_relevant_operations()

    assert "LoadAudio" in operations
    assert "ByteDanceSeedreamNodeV2" in operations
    assert "ByteDance2ReferenceNode" in operations


def test_seedance_actions_follow_the_specific_source_role():
    assert operation_actions("ByteDance2ReferenceNode", "reference_audio") == [
        {"modality": "voice", "action": "process"}
    ]
    assert operation_actions("ByteDance2ReferenceNode", "reference_video") == [
        {"modality": "full_body_performance", "action": "edit"}
    ]
    assert operation_actions("ByteDance2ReferenceNode", "reference_image") == [
        {"modality": "face", "action": "generate"}
    ]


def test_registry_entries_are_unique_and_have_actions():
    with (ROOT / "operation-registry.json").open(encoding="utf-8") as handle:
        payload = json.load(handle)

    class_types = [item["classType"] for item in payload["operations"]]
    assert len(class_types) == len(set(class_types))
    assert set(class_types) == set(operation_registry())
    assert all(item.get("actions") for item in payload["operations"])


def test_generated_registry_projections_are_current():
    subprocess.run(
        [sys.executable, str(ROOT / "tools" / "generate_operation_registry.py"), "--check"],
        check=True,
    )


def test_generated_registry_targets_match_the_checkout_boundary():
    with (ROOT / "operation-registry.json").open(encoding="utf-8") as handle:
        targets = projection_targets(json.load(handle))

    assert ROOT / "web" / "operation-registry.js" in targets
    app_target = ROOT.parent / "src" / "lib" / "plugin" / "generated-operation-registry.ts"
    if is_monorepo_checkout():
        assert app_target in targets
    else:
        assert app_target not in targets
        assert all(path.is_relative_to(ROOT) for path in targets)
