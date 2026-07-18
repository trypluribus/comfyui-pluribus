"""Canonical rights-operation metadata shared by scanner and clients.

The JSON registry is language-neutral. Browser and TypeScript projections are
generated from the same file and checked in tests so the three runtimes cannot
silently disagree about a partner node.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


REGISTRY_PATH = Path(__file__).resolve().parent.parent / "operation-registry.json"


@lru_cache(maxsize=1)
def operation_registry() -> dict[str, dict[str, Any]]:
    with REGISTRY_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schemaVersion") != 1:
        raise ValueError("Unsupported operation registry schema version.")
    operations = payload.get("operations")
    if not isinstance(operations, list):
        raise ValueError("Operation registry must contain an operations list.")
    result: dict[str, dict[str, Any]] = {}
    for operation in operations:
        class_type = str(operation.get("classType") or "")
        if not class_type or class_type in result:
            raise ValueError("Operation registry classType values must be unique.")
        result[class_type] = operation
    return result


def rights_relevant_operations() -> frozenset[str]:
    return frozenset(
        class_type
        for class_type, operation in operation_registry().items()
        if operation.get("rightsRelevant") is True
    )


def operation_actions(
    class_type: str,
    source_role: str | None = None,
) -> list[dict[str, str]]:
    operation = operation_registry().get(class_type)
    if not operation:
        return []
    role_actions = (operation.get("actionsBySourceRole") or {}).get(source_role or "")
    values = role_actions if role_actions is not None else operation.get("actions", [])
    return [
        {"modality": str(value["modality"]), "action": str(value["action"])}
        for value in values
    ]


def operation_label(class_type: str) -> str:
    operation = operation_registry().get(class_type)
    return str(operation.get("label") if operation else class_type)
