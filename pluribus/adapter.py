from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

OUTPUT_NODE_TYPES = {"SaveImage", "PreviewImage", "SaveVideo", "VHS_VideoCombine"}


@dataclass
class WorkflowAdapter:
    """Normalize and inspect ComfyUI API-format workflow graphs.

    ComfyUI is adapter #1. Future adapters should normalize into this same
    graph shape rather than leaking platform-specific logic into the engine.
    """

    nodes: dict
    _upstream_cache: dict[str, tuple[str, ...]] = field(
        default_factory=dict, init=False, repr=False
    )
    _downstream_cache: dict[str, tuple[str, ...]] = field(
        default_factory=dict, init=False, repr=False
    )
    _source_path_cache: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict, init=False, repr=False
    )
    _source_input_cache: dict[tuple[str, str], tuple[str, ...]] = field(
        default_factory=dict, init=False, repr=False
    )

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

    def scan_terminal_nodes(self) -> list[str]:
        """Return every declared output and every otherwise-unconsumed sink.

        ComfyUI workflows can contain several independent lanes, and custom
        generation nodes are not required to terminate in a core Save/Preview
        node. Scanning only known output classes silently drops those lanes as
        soon as *any* known output exists elsewhere in the workflow. Keep the
        legacy ``terminal_image_nodes`` contract for callers that need it, but
        give the scanner the complete set of branch endpoints.
        """

        declared_outputs = {
            node_id
            for node_id, node in self.nodes.items()
            if node.get("class_type") in OUTPUT_NODE_TYPES
        }
        referenced = {
            source_id
            for node in self.nodes.values()
            for source_id in self._linked_node_ids(node.get("inputs", {}))
        }
        sinks = {node_id for node_id in self.nodes if node_id not in referenced}
        return self._sort_node_ids(declared_outputs | sinks)

    def upstream_node_ids(self, node_id: str) -> list[str]:
        cached = self._upstream_cache.get(node_id)
        if cached is not None:
            return list(cached)
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
        self._upstream_cache[node_id] = tuple(found)
        return found

    def downstream_node_ids(self, node_id: str) -> list[str]:
        """Node ids reachable by following outputs away from `node_id`."""
        cached = self._downstream_cache.get(node_id)
        if cached is not None:
            return list(cached)
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
        sorted_found = self._sort_node_ids(found)
        self._downstream_cache[node_id] = tuple(sorted_found)
        return sorted_found

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

    def source_path_node_ids(self, source_node_id: str, output_node_id: str) -> list[str]:
        """Return only nodes lying on a path from one source to one output.

        The order mirrors :meth:`provenance_path`: output first, source last.
        At a multimodal join this deliberately excludes sibling inputs, so an
        image reference cannot inherit operations from an unrelated video or
        audio branch merely because both feed the same final output.
        """

        cache_key = (source_node_id, output_node_id)
        cached = self._source_path_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        if source_node_id not in self.nodes or output_node_id not in self.nodes:
            return []

        reachable_from_source = {
            source_node_id,
            *self.downstream_node_ids(source_node_id),
        }
        path: list[str] = []
        visited: set[str] = set()

        def walk(current_id: str) -> None:
            if current_id in visited or current_id not in reachable_from_source:
                return
            visited.add(current_id)
            path.append(current_id)
            node = self.nodes.get(current_id, {})
            for linked_id in self._linked_node_ids(node.get("inputs", {})):
                walk(linked_id)

        walk(output_node_id)
        if source_node_id not in visited:
            self._source_path_cache[cache_key] = ()
            return []
        self._source_path_cache[cache_key] = tuple(path)
        return path

    def source_provenance_path(
        self, source_node_id: str, output_node_id: str
    ) -> list[str]:
        """Return class types for one source-specific output path."""

        return [
            str(self.nodes[node_id].get("class_type"))
            for node_id in self.source_path_node_ids(source_node_id, output_node_id)
            if self.nodes.get(node_id, {}).get("class_type")
        ]

    def source_input_names(self, source_node_id: str, node_id: str) -> list[str]:
        """Return destination input names carrying ``source_node_id`` to a node.

        Names are preserved exactly (including custom-node dotted names such
        as ``model.reference_audios.audio_1``) so downstream rights mapping can
        distinguish image, video, and audio roles on the same operation.
        """

        cache_key = (source_node_id, node_id)
        cached = self._source_input_cache.get(cache_key)
        if cached is not None:
            return list(cached)
        node = self.nodes.get(node_id, {})
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict) or source_node_id not in self.nodes:
            return []
        reachable = {source_node_id, *self.downstream_node_ids(source_node_id)}
        names = [
            str(input_name)
            for input_name, value in inputs.items()
            if any(linked_id in reachable for linked_id in self._linked_node_ids(value))
        ]
        self._source_input_cache[cache_key] = tuple(names)
        return names

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
