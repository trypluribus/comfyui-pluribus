from __future__ import annotations

from .adapter import WorkflowAdapter
from .models import ClearanceState, PersonInstance, ScanResult, TalentAsset
from .roster import Roster

LORA_TYPES = {"LoraLoader", "LoraLoaderModelOnly"}
# Editing-model identity transfer (reference photo in, scene out) counts as a
# face-consistency operation: the reference image carries the likeness.
FACE_TYPES = {
    "ReActorFaceSwap",
    "IPAdapter",
    "IPAdapterAdvanced",
    "IPAdapterApply",
    "GeminiImage2Node",
    "FluxKontextProImageNode",
}
DEFAULT_FACE_SOURCE_INPUTS = (
    "source_image",
    "reference_image",
    "ref_image",
    "face_image",
    "identity_image",
    "person_image",
    "subject_image",
    "image",
    "images",
    "input_image",
)
FACE_SOURCE_INPUTS_BY_TYPE = {
    "ReActorFaceSwap": (
        "source_image",
        "reference_image",
        "ref_image",
        "face_image",
        "identity_image",
    ),
    "IPAdapter": DEFAULT_FACE_SOURCE_INPUTS,
    "IPAdapterAdvanced": DEFAULT_FACE_SOURCE_INPUTS,
    "IPAdapterApply": DEFAULT_FACE_SOURCE_INPUTS,
    "GeminiImage2Node": (
        "source_image",
        "reference_image",
        "ref_image",
        "images",
        "image",
        "input_image",
    ),
    "FluxKontextProImageNode": (
        "source_image",
        "reference_image",
        "ref_image",
        "image",
        "images",
        "input_image",
    ),
}
LOAD_IMAGE_TYPES = {"LoadImage"}
LOAD_VIDEO_TYPES = {"LoadVideo"}
LOAD_AUDIO_TYPES = {"LoadAudio"}
VIDEO_EDIT_TYPES = {"RunwayAleph2VideoToVideoNode"}
VIDEO_SOURCE_INPUTS_BY_TYPE = {
    "RunwayAleph2VideoToVideoNode": ("video",),
}
PROMPT_TYPES = {"CLIPTextEncode"}
MARKER_TYPES = {"PluribusSourceMarker"}
SOURCE_OPERATION_TYPES = (
    LORA_TYPES
    | FACE_TYPES
    | LOAD_IMAGE_TYPES
    | LOAD_VIDEO_TYPES
    | LOAD_AUDIO_TYPES
    | VIDEO_EDIT_TYPES
    | PROMPT_TYPES
)

# Downstream node types that are plumbing, not operations on the likeness.
OPS_IGNORE = {
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "LoadAudio",
    "LoadImage",
    "LoadVideo",
    "PluribusSourceMarker",
    "PreviewImage",
    "SaveImage",
    "CreateVideo",
    "SaveVideo",
    "VAEDecode",
    "VAELoader",
}

PERSON_WORDS = {
    "actor",
    "actress",
    "barista",
    "boy",
    "face",
    "girl",
    "guy",
    "human",
    "man",
    "model",
    "people",
    "person",
    "portrait",
    "presenter",
    "woman",
}

SYNTHETIC_NOTE = (
    "No known real-person source detected in graph. "
    "This is not a legal clearance determination."
)


class ClearanceEngine:
    def __init__(self, roster: Roster):
        self.roster = roster

    def scan(self, adapter: WorkflowAdapter) -> ScanResult:
        result = ScanResult()
        sources: dict[tuple[str, str], PersonInstance] = {}

        def add_source(person: PersonInstance, *, marker_only: bool = False) -> None:
            person.ops = self._ops_for(adapter, person)
            if marker_only:
                self._initialize_marker(person)
            else:
                self._initialize_occurrence(adapter, person)
            identity = self._source_identity(person)
            existing = sources.get(identity)
            if existing is None:
                sources[identity] = person
                result.persons.append(person)
                return
            if marker_only:
                self._merge_marker_metadata(existing, person)
            else:
                self._merge_source(existing, person)

        for output_id in adapter.scan_terminal_nodes():
            output_people = self._classify_output(adapter, output_id)
            for person in output_people:
                add_source(person)
            output_type = str(adapter.nodes.get(output_id, {}).get("class_type") or "")
            if output_people and output_type not in {
                "SaveImage",
                "PreviewImage",
                "SaveVideo",
                "VHS_VideoCombine",
            }:
                result.issues.append(
                    {
                        "code": "unsupported_terminal_node",
                        "node_id": output_id,
                        "class_type": output_type,
                        "message": (
                            f"Scanned source lineage ending at unsupported terminal node "
                            f"'{output_type or output_id}'. Downstream-use coverage may be incomplete."
                        ),
                    }
                )
        for node_id, node in adapter.nodes.items():
            class_type = node.get("class_type")
            if class_type in MARKER_TYPES:
                person = self._classify_marker(node_id, node)
            else:
                continue
            if person is None:
                result.issues.append(
                    {
                        "code": "incomplete_source_marker",
                        "node_id": node_id,
                        "message": (
                            "Pluribus Source Marker is incomplete and was ignored. "
                            "Add a source key (or describe a prompt-only source)."
                        ),
                    }
                )
                continue
            add_source(person, marker_only=True)
        return result

    @staticmethod
    def _source_identity(person: PersonInstance) -> tuple[str, str]:
        """Return one stable identity for a graph source, not an output use.

        Non-empty keys are the strongest identity already used by the local
        binding store. Preserve them exactly: filenames can be case-sensitive,
        and whitespace may be meaningful to a custom node. Keyless
        prompt/unknown sources fall back to their source node so unrelated
        blank sources are never collapsed together.
        """

        kind = str(person.source_kind or "unknown").strip().casefold()
        key = str(person.source_key or "")
        if key:
            return kind, f"key:{key}"
        node_id = str(person.source_node_id or person.output_node_id or "").strip()
        return kind, f"node:{node_id}"

    @staticmethod
    def _initialize_marker(person: PersonInstance) -> None:
        """Represent an explicit marker as source metadata, not an output use."""

        source_id = str(person.source_node_id or "")
        person.output_node_ids = []
        person.source_node_ids = [source_id] if source_id else []
        person.occurrences = []

    @staticmethod
    def _initialize_occurrence(adapter: WorkflowAdapter, person: PersonInstance) -> None:
        output_id = str(person.output_node_id or "")
        source_id = str(person.source_node_id or "")
        person.output_node_ids = [output_id] if output_id else []
        person.source_node_ids = [source_id] if source_id else []

        path_node_ids = {output_id, *adapter.upstream_node_ids(output_id)} if output_id else set()
        occurrence_ops = [
            dict(operation)
            for operation in person.ops
            if str(operation.get("node_id") or "") in path_node_ids
        ]
        person.occurrences = [
            {
                "output_node_id": output_id,
                "source_node_id": source_id,
                "provenance": list(person.provenance),
                "ops": occurrence_ops,
            }
        ]

    @staticmethod
    def _merge_source(existing: PersonInstance, incoming: PersonInstance) -> None:
        for output_id in incoming.output_node_ids:
            if output_id and output_id not in existing.output_node_ids:
                existing.output_node_ids.append(output_id)
        for source_id in incoming.source_node_ids:
            if source_id and source_id not in existing.source_node_ids:
                existing.source_node_ids.append(source_id)

        seen_ops = {
            (
                str(operation.get("node_id") or ""),
                str(operation.get("class_type") or ""),
                str(operation.get("input_name") or ""),
                str(operation.get("source_role") or ""),
            )
            for operation in existing.ops
        }
        for operation in incoming.ops:
            identity = (
                str(operation.get("node_id") or ""),
                str(operation.get("class_type") or ""),
                str(operation.get("input_name") or ""),
                str(operation.get("source_role") or ""),
            )
            if identity in seen_ops:
                continue
            seen_ops.add(identity)
            existing.ops.append(dict(operation))

        seen_occurrences = {
            (
                str(occurrence.get("output_node_id") or ""),
                str(occurrence.get("source_node_id") or ""),
            )
            for occurrence in existing.occurrences
        }
        for occurrence in incoming.occurrences:
            identity = (
                str(occurrence.get("output_node_id") or ""),
                str(occurrence.get("source_node_id") or ""),
            )
            if identity in seen_occurrences:
                continue
            seen_occurrences.add(identity)
            existing.occurrences.append(
                {
                    "output_node_id": occurrence.get("output_node_id", ""),
                    "source_node_id": occurrence.get("source_node_id", ""),
                    "provenance": list(occurrence.get("provenance") or []),
                    "ops": [dict(operation) for operation in occurrence.get("ops") or []],
                }
            )

    @staticmethod
    def _merge_marker_metadata(existing: PersonInstance, marker: PersonInstance) -> None:
        """Apply explicit annotation without inventing another graph use."""

        if marker.name and existing.name is None:
            existing.name = marker.name
        if marker.note:
            existing.note = marker.note

    @staticmethod
    def _ops_for(adapter: WorkflowAdapter, person: PersonInstance) -> list[dict]:
        if not person.source_node_id or not person.output_node_id:
            return []
        path_node_ids = set(
            adapter.source_path_node_ids(person.source_node_id, person.output_node_id)
        )
        ops: list[dict] = []
        source_class_type = str(
            adapter.nodes.get(person.source_node_id, {}).get("class_type") or ""
        )
        if source_class_type in SOURCE_OPERATION_TYPES:
            source_operation = {
                "node_id": person.source_node_id,
                "class_type": source_class_type,
            }
            source_role = {
                "LoadImage": "reference_image",
                "LoadVideo": "reference_video",
                "LoadAudio": "reference_audio",
            }.get(source_class_type, "")
            if source_role:
                source_operation["source_role"] = source_role
            ops.append(source_operation)
        for node_id in adapter.downstream_node_ids(person.source_node_id):
            if node_id not in path_node_ids:
                continue
            class_type = adapter.nodes.get(node_id, {}).get("class_type", "")
            if class_type and class_type not in OPS_IGNORE:
                input_names = adapter.source_input_names(person.source_node_id, node_id)
                for source_role, role_input_names in ClearanceEngine._source_roles(
                    person, input_names
                ):
                    operation = {"node_id": node_id, "class_type": class_type}
                    if role_input_names:
                        operation["input_name"] = role_input_names[0]
                        if len(role_input_names) > 1:
                            operation["input_names"] = role_input_names
                    if source_role:
                        operation["source_role"] = source_role
                    ops.append(operation)
        return ops

    @staticmethod
    def _source_role(person: PersonInstance, input_names: list[str]) -> str:
        """Normalize one role, preferring the exact destination port."""

        for input_name in input_names:
            source_role = ClearanceEngine._source_role_for_input(input_name)
            if source_role:
                return source_role

        key = str(person.source_key or "").lower()
        if person.source_kind == "audio" or key.endswith(
            (".aac", ".flac", ".m4a", ".mp3", ".ogg", ".wav")
        ):
            return "reference_audio"
        if key.endswith((".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm")):
            return "reference_video"
        if person.source_kind == "reference":
            return "reference_image"
        return ""

    @staticmethod
    def _source_role_for_input(input_name: str) -> str:
        """Map one destination port to the role used at that operation."""

        name = str(input_name or "").lower()
        if "audio" in name or "voice" in name:
            return "reference_audio"
        if "image" in name or "frame" in name:
            return "reference_image"
        if "video" in name:
            return "reference_video"
        return ""

    @staticmethod
    def _source_roles(
        person: PersonInstance, input_names: list[str]
    ) -> list[tuple[str, list[str]]]:
        """Return every distinct role a source occupies on one operation.

        A source can branch through a frame extractor while also feeding the
        original video into another port on the same multimodal node.  Keep
        those roles separate so neither rights action is lost.  Source media
        kind and filename are only a fallback when the destination port is not
        descriptive.
        """

        inputs_by_role: dict[str, list[str]] = {}
        for input_name in input_names:
            source_role = ClearanceEngine._source_role_for_input(input_name)
            if source_role:
                inputs_by_role.setdefault(source_role, []).append(input_name)
        if inputs_by_role:
            return [
                (source_role, inputs_by_role[source_role])
                for source_role in sorted(inputs_by_role)
            ]

        fallback_role = ClearanceEngine._source_role(person, [])
        return [(fallback_role, input_names)]

    def _classify_talent(
        self,
        output_id: str,
        node: dict,
        provenance: list[str],
        source_node_id: str,
    ) -> PersonInstance:
        name = str(node.get("inputs", {}).get("talent", ""))
        asset = self.roster.match_name(name)
        if asset is None:
            return PersonInstance(
                output_node_id=output_id,
                source_kind="talent",
                source_key=name,
                state=ClearanceState.UNIDENTIFIED,
                source_node_id=source_node_id,
                name=name or None,
                note=f"'{name}' is not in the roster. Pick a roster talent or identify the source.",
                provenance=provenance,
            )
        key = asset.asset_keys[0] if asset.asset_keys else name
        return self._person_from_asset(
            output_id, "talent", key, provenance, asset, "", source_node_id
        )

    def _classify_marker(self, node_id: str, node: dict) -> PersonInstance | None:
        inputs = node.get("inputs", {})
        if not isinstance(inputs, dict):
            return None
        kind = str(inputs.get("source_kind") or "unknown").strip().lower()
        key = str(inputs.get("source_key") or "")
        has_key = bool(key.strip())
        display_name = str(inputs.get("display_name") or "").strip()
        note = str(inputs.get("note") or "").strip()

        # Double-clicking a node-library result can insert two fresh marker
        # nodes in ComfyUI. A blank marker is an annotation placeholder, not a
        # person-bearing source, so it must never become actionable. Prompt
        # markers may omit a key, but must include a human-readable name or
        # note to distinguish an intentional prompt-only source from a blank
        # node.
        if kind == "prompt":
            if not (has_key or display_name or note):
                return None
        elif not has_key:
            return None

        if kind == "prompt":
            return PersonInstance(
                output_node_id=node_id,
                source_kind="prompt",
                source_key=key,
                state=ClearanceState.SYNTHETIC_UNVERIFIED,
                source_node_id=node_id,
                name=display_name or None,
                note=note or SYNTHETIC_NOTE,
                provenance=["PluribusSourceMarker"],
                synthetic_only=True,
            )

        person = self._resolve(
            node_id,
            kind if kind in {"audio", "lora", "reference", "unknown"} else "unknown",
            key,
            ["PluribusSourceMarker"],
            source_node_id=node_id,
        )
        if display_name and person.name is None:
            person.name = display_name
        if note:
            person.note = note
        return person

    def _classify_output(self, adapter: WorkflowAdapter, output_id: str) -> list[PersonInstance]:
        persons: list[PersonInstance] = []

        def resolve(kind: str, key: str, source_node_id: str) -> PersonInstance:
            return self._resolve(
                output_id,
                kind,
                key,
                adapter.source_provenance_path(source_node_id, output_id),
                source_node_id=source_node_id,
            )

        loras = adapter.nodes_of_type(output_id, LORA_TYPES)
        for source_node_id, node in loras:
            key = str(node.get("inputs", {}).get("lora_name", ""))
            persons.append(resolve("lora", key, source_node_id))

        face_nodes = adapter.nodes_of_type(output_id, FACE_TYPES)
        face_people: list[PersonInstance] = []
        for face_node_id, _node in face_nodes:
            for source_id, key in self._source_images(adapter, face_node_id):
                face_people.append(resolve("reference", key, source_id))
        if face_people:
            persons.extend(face_people)
        elif face_nodes:
            face_node_id = face_nodes[0][0]
            persons.append(resolve("reference", "", face_node_id))

        video_nodes = adapter.nodes_of_type(output_id, VIDEO_EDIT_TYPES)
        video_people: list[PersonInstance] = []
        for video_node_id, _node in video_nodes:
            for source_id, key in self._source_videos(adapter, video_node_id):
                video_people.append(resolve("reference", key, source_id))
        if video_people:
            persons.extend(video_people)
        elif video_nodes:
            video_node_id = video_nodes[0][0]
            persons.append(resolve("reference", "", video_node_id))

        # Loaders are collected independently. A video or LoRA in the same
        # multimodal lane must never suppress an image or audio source. This is
        # metadata-only: the scanner records graph filenames and never opens or
        # uploads local media.
        for source_node_id, node in adapter.nodes_of_type(output_id, LOAD_VIDEO_TYPES):
            key = str(node.get("inputs", {}).get("file", ""))
            persons.append(resolve("reference", key, source_node_id))

        for source_node_id, node in adapter.nodes_of_type(output_id, LOAD_IMAGE_TYPES):
            key = str(node.get("inputs", {}).get("image", ""))
            persons.append(resolve("reference", key, source_node_id))

        for source_node_id, node in adapter.nodes_of_type(output_id, LOAD_AUDIO_TYPES):
            inputs = node.get("inputs", {})
            key = str(inputs.get("audio") or inputs.get("file") or "")
            persons.append(resolve("audio", key, source_node_id))

        if persons:
            return persons

        prompt_node_id = self._person_prompt_node(adapter, output_id)
        if prompt_node_id is None:
            return []

        provenance = adapter.source_provenance_path(prompt_node_id, output_id)
        return [
            PersonInstance(
                output_node_id=output_id,
                source_kind="prompt",
                source_key="",
                state=ClearanceState.SYNTHETIC_UNVERIFIED,
                source_node_id=prompt_node_id,
                note=SYNTHETIC_NOTE,
                provenance=provenance,
                synthetic_only=True,
            )
        ]

    @staticmethod
    def _source_images(adapter: WorkflowAdapter, face_node_id: str) -> list[tuple[str, str]]:
        face_node = adapter.nodes.get(face_node_id, {})
        inputs = face_node.get("inputs", {})
        class_type = str(face_node.get("class_type", ""))
        source_input_names = FACE_SOURCE_INPUTS_BY_TYPE.get(class_type, DEFAULT_FACE_SOURCE_INPUTS)

        saw_source_input = False
        seen: set[str] = set()
        for input_name in source_input_names:
            if input_name not in inputs:
                continue
            saw_source_input = True
            results: list[tuple[str, str]] = []
            for linked_id in adapter.linked_node_ids(inputs[input_name]):
                for image_id, key in ClearanceEngine._load_images_for_source(adapter, linked_id):
                    if image_id in seen:
                        continue
                    seen.add(image_id)
                    results.append((image_id, key))
            if results:
                return results

        if saw_source_input:
            return []

        return [
            (image_id, str(node.get("inputs", {}).get("image", "")))
            for image_id, node in adapter.nodes_of_type(face_node_id, LOAD_IMAGE_TYPES)
        ]

    @staticmethod
    def _load_images_for_source(adapter: WorkflowAdapter, source_node_id: str) -> list[tuple[str, str]]:
        node = adapter.nodes.get(source_node_id)
        if not node:
            return []
        if node.get("class_type") in LOAD_IMAGE_TYPES:
            return [(source_node_id, str(node.get("inputs", {}).get("image", "")))]
        return [
            (image_id, str(image_node.get("inputs", {}).get("image", "")))
            for image_id, image_node in adapter.nodes_of_type(source_node_id, LOAD_IMAGE_TYPES)
        ]

    @staticmethod
    def _source_videos(adapter: WorkflowAdapter, video_node_id: str) -> list[tuple[str, str]]:
        video_node = adapter.nodes.get(video_node_id, {})
        inputs = video_node.get("inputs", {})
        class_type = str(video_node.get("class_type", ""))
        source_input_names = VIDEO_SOURCE_INPUTS_BY_TYPE.get(class_type, ("video",))

        saw_source_input = False
        seen: set[str] = set()
        for input_name in source_input_names:
            if input_name not in inputs:
                continue
            saw_source_input = True
            results: list[tuple[str, str]] = []
            for linked_id in adapter.linked_node_ids(inputs[input_name]):
                for source_id, key in ClearanceEngine._load_videos_for_source(adapter, linked_id):
                    if source_id in seen:
                        continue
                    seen.add(source_id)
                    results.append((source_id, key))
            if results:
                return results

        if saw_source_input:
            return []

        return [
            (source_id, str(node.get("inputs", {}).get("file", "")))
            for source_id, node in adapter.nodes_of_type(video_node_id, LOAD_VIDEO_TYPES)
        ]

    @staticmethod
    def _load_videos_for_source(
        adapter: WorkflowAdapter, source_node_id: str
    ) -> list[tuple[str, str]]:
        node = adapter.nodes.get(source_node_id)
        if not node:
            return []
        if node.get("class_type") in LOAD_VIDEO_TYPES:
            return [(source_node_id, str(node.get("inputs", {}).get("file", "")))]
        return [
            (video_id, str(video_node.get("inputs", {}).get("file", "")))
            for video_id, video_node in adapter.nodes_of_type(
                source_node_id, LOAD_VIDEO_TYPES
            )
        ]

    @staticmethod
    def _source_image(adapter: WorkflowAdapter, face_node_id: str) -> tuple[str | None, str]:
        images = ClearanceEngine._source_images(adapter, face_node_id)
        if images:
            return images[0]
        return None, ""

    @staticmethod
    def _person_prompt_node(adapter: WorkflowAdapter, output_id: str) -> str | None:
        for node_id, node in adapter.nodes_of_type(output_id, PROMPT_TYPES):
            text = str(node.get("inputs", {}).get("text", "")).lower()
            if any(word in text for word in PERSON_WORDS):
                return node_id
        return None

    def _resolve(
        self,
        output_id: str,
        kind: str,
        key: str,
        provenance: list[str],
        source_node_id: str = "",
    ) -> PersonInstance:
        asset = self.roster.match(key)
        replacement_key = self.roster.replacement_key_for(kind)
        if asset is None:
            return PersonInstance(
                output_node_id=output_id,
                source_kind=kind,
                source_key=key,
                state=ClearanceState.UNIDENTIFIED,
                source_node_id=source_node_id,
                note=f"Unrecognized source '{key}'. Identify the source before clearance review.",
                provenance=provenance,
                replacement_asset_key=replacement_key,
            )
        return self._person_from_asset(
            output_id, kind, key, provenance, asset, replacement_key, source_node_id
        )

    @staticmethod
    def _person_from_asset(
        output_id: str,
        kind: str,
        key: str,
        provenance: list[str],
        asset: TalentAsset,
        replacement_key: str,
        source_node_id: str = "",
    ) -> PersonInstance:
        shared = dict(
            source_node_id=source_node_id,
            talent_id=asset.talent_id,
            name=asset.name,
            provenance=provenance,
            allowed_uses=asset.allowed_uses,
            prohibited_uses=asset.prohibited_uses,
            conflicts=asset.conflicts,
            scope=asset.scope,
            union_status=asset.union_status,
            rep=asset.rep,
            synthetic_only=asset.synthetic_only,
        )

        if asset.clearance_status == "restricted":
            reason = "; ".join(asset.conflicts) or "restriction on file"
            return PersonInstance(
                output_node_id=output_id,
                source_kind=kind,
                source_key=key,
                state=ClearanceState.RESTRICTED,
                note=f"Restriction on file: {reason}. Review against campaign before use.",
                replacement_asset_key=replacement_key,
                **shared,
            )

        if asset.clearance_status == "cleared":
            return PersonInstance(
                output_node_id=output_id,
                source_kind=kind,
                source_key=key,
                state=ClearanceState.CLEARED,
                note=f"Scope on file: {asset.scope}. Review against the intended use.",
                **shared,
            )

        return PersonInstance(
            output_node_id=output_id,
            source_kind=kind,
            source_key=key,
            state=ClearanceState.NEEDS_REVIEW,
            note="Known person; review workflow-specific terms or route for BA review.",
            replacement_asset_key=replacement_key,
            **shared,
        )
