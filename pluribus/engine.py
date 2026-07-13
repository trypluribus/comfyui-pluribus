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
PROMPT_TYPES = {"CLIPTextEncode"}
MARKER_TYPES = {"PluribusSourceMarker"}

# Downstream node types that are plumbing, not operations on the likeness.
OPS_IGNORE = {
    "CheckpointLoaderSimple",
    "CLIPTextEncode",
    "EmptyLatentImage",
    "LoadImage",
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
        seen: set[tuple[str, str, str]] = set()
        connected: set[str] = set()
        for output_id in adapter.terminal_image_nodes():
            connected.add(output_id)
            connected.update(adapter.upstream_node_ids(output_id))
            for person in self._classify_output(adapter, output_id):
                identity = (person.output_node_id, person.source_kind, person.source_key)
                if identity in seen:
                    continue
                seen.add(identity)
                result.persons.append(person)
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
            identity = (person.output_node_id, person.source_kind, person.source_key)
            if identity not in seen:
                result.persons.append(person)
        for person in result.persons:
            person.ops = self._ops_for(adapter, person)
        return result

    @staticmethod
    def _ops_for(adapter: WorkflowAdapter, person: PersonInstance) -> list[dict]:
        if not person.source_node_id:
            return []
        ops: list[dict] = []
        for node_id in adapter.downstream_node_ids(person.source_node_id):
            class_type = adapter.nodes.get(node_id, {}).get("class_type", "")
            if class_type and class_type not in OPS_IGNORE:
                ops.append({"node_id": node_id, "class_type": class_type})
        return ops

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
        key = str(inputs.get("source_key") or "").strip()
        display_name = str(inputs.get("display_name") or "").strip()
        note = str(inputs.get("note") or "").strip()

        # Double-clicking a node-library result can insert two fresh marker
        # nodes in ComfyUI. A blank marker is an annotation placeholder, not a
        # person-bearing source, so it must never become actionable. Prompt
        # markers may omit a key, but must include a human-readable name or
        # note to distinguish an intentional prompt-only source from a blank
        # node.
        if kind == "prompt":
            if not (key or display_name or note):
                return None
        elif not key:
            return None

        if kind == "prompt" and not key:
            return PersonInstance(
                output_node_id=node_id,
                source_kind="prompt",
                source_key="",
                state=ClearanceState.SYNTHETIC_UNVERIFIED,
                source_node_id=node_id,
                name=display_name or None,
                note=note or SYNTHETIC_NOTE,
                provenance=["PluribusSourceMarker"],
                synthetic_only=True,
            )

        person = self._resolve(
            node_id,
            "lora" if kind == "lora" else "reference",
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
        provenance = adapter.provenance_path(output_id)
        persons: list[PersonInstance] = []

        loras = adapter.nodes_of_type(output_id, LORA_TYPES)
        for source_node_id, node in loras:
            key = str(node.get("inputs", {}).get("lora_name", ""))
            persons.append(self._resolve(output_id, "lora", key, provenance, source_node_id=source_node_id))

        face_nodes = adapter.nodes_of_type(output_id, FACE_TYPES)
        face_people: list[PersonInstance] = []
        for face_node_id, _node in face_nodes:
            for source_id, key in self._source_images(adapter, face_node_id):
                face_people.append(
                    self._resolve(output_id, "reference", key, provenance, source_node_id=source_id)
                )
        if face_people:
            persons.extend(face_people)
        elif face_nodes and not persons:
            face_node_id = face_nodes[0][0]
            persons.append(
                self._resolve(output_id, "reference", "", provenance, source_node_id=face_node_id)
            )

        if not persons:
            images = adapter.nodes_of_type(output_id, LOAD_IMAGE_TYPES)
            for source_node_id, node in images:
                key = str(node.get("inputs", {}).get("image", ""))
                persons.append(
                    self._resolve(
                        output_id, "reference", key, provenance, source_node_id=source_node_id
                    )
                )

        if persons:
            return persons

        prompt_node_id = self._person_prompt_node(adapter, output_id)
        if prompt_node_id is None:
            return []

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
