from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

OUTPUT_NODE_TYPES = {"SaveImage", "PreviewImage", "SaveVideo", "VHS_VideoCombine"}


@dataclass
class WorkflowAdapter:
    """Normalize and inspect ComfyUI API-format workflow graphs.

    ComfyUI is adapter #1. Future adapters should normalize into this same
    graph shape rather than leaking platform-specific logic into the engine.
    """

    nodes: dict

    @classmethod
    def from_comfyui_api(cls, data: dict) -> "WorkflowAdapter":
        prompt = data.get("prompt") if isinstance(data, dict) else None
        nodes = prompt if isinstance(prompt, dict) else data
        if not isinstance(nodes, dict):
            raise TypeError("ComfyUI API workflow must be a dict or {'prompt': dict}")
        return cls(nodes)

    def terminal_image_nodes(self) -> list[str]:
        outputs = [
            node_id
            for node_id, node in self.nodes.items()
            if node.get("class_type") in OUTPUT_NODE_TYPES
        ]
        if outputs:
            return self._sort_node_ids(outputs)

        referenced = {
            source_id
            for node in self.nodes.values()
            for source_id in self._linked_node_ids(node.get("inputs", {}))
        }
        terminals = [node_id for node_id in self.nodes if node_id not in referenced]
        return self._sort_node_ids(terminals)

    def upstream_node_ids(self, node_id: str) -> list[str]:
        found: list[str] = []
        visited: set[str] = {node_id}

        def walk(current_id: str) -> None:
            node = self.nodes.get(current_id, {})
            for source_id in self._linked_node_ids(node.get("inputs", {})):
                if source_id in visited:
                    continue
                visited.add(source_id)
                found.append(source_id)
                walk(source_id)

        walk(node_id)
        return found

    def downstream_node_ids(self, node_id: str) -> list[str]:
        """Node ids reachable by following outputs away from `node_id`."""
        found: list[str] = []
        visited: set[str] = {node_id}

        def consumers(source_id: str) -> list[str]:
            return [
                nid
                for nid, node in self.nodes.items()
                if nid not in visited
                and source_id in set(self._linked_node_ids(node.get("inputs", {})))
            ]

        def walk(current_id: str) -> None:
            for nid in consumers(current_id):
                if nid in visited:
                    continue
                visited.add(nid)
                found.append(nid)
                walk(nid)

        walk(node_id)
        return self._sort_node_ids(found)

    def nodes_of_type(self, node_id: str, class_types: set[str]) -> list[tuple[str, dict]]:
        matched: list[tuple[str, dict]] = []
        for current_id in [node_id, *self.upstream_node_ids(node_id)]:
            node = self.nodes.get(current_id)
            if node and node.get("class_type") in class_types:
                matched.append((current_id, node))
        return matched

    def linked_node_ids(self, value: object) -> list[str]:
        return list(self._linked_node_ids(value))

    def provenance_path(self, output_node_id: str) -> list[str]:
        path: list[str] = []
        visited: set[str] = set()

        def walk(current_id: str) -> None:
            if current_id in visited:
                return
            visited.add(current_id)
            node = self.nodes.get(current_id)
            if not node:
                return
            class_type = node.get("class_type")
            if class_type:
                path.append(class_type)
            for source_id in self._linked_node_ids(node.get("inputs", {})):
                walk(source_id)

        walk(output_node_id)
        return path

    def _linked_node_ids(self, value: object) -> Iterable[str]:
        if isinstance(value, dict):
            for item in value.values():
                yield from self._linked_node_ids(item)
            return
        if isinstance(value, list):
            if (
                len(value) == 2
                and isinstance(value[0], str)
                and value[0] in self.nodes
                and isinstance(value[1], int)
            ):
                yield value[0]
                return
            for item in value:
                yield from self._linked_node_ids(item)

    @staticmethod
    def _sort_node_ids(node_ids: Iterable[str]) -> list[str]:
        def key(node_id: str) -> tuple[int, int | str]:
            return (0, int(node_id)) if node_id.isdigit() else (1, node_id)

        return sorted(node_ids, key=key)
