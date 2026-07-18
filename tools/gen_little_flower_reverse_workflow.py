#!/usr/bin/env python3
"""Generate a pre-Pluribus ComfyUI reconstruction of the Little Flower cut.

The graph models a plausible advanced workflow, not the film's actual
production history. It follows an image-first previsualization pattern:

* face-cropped principal evidence, anonymous party candidates, storyboards,
  location and prop references;
* a multi-reference scene master for each of eight sequences;
* wide, close and insert/over-shoulder storyboard branches;
* image-to-video shot generation plus video-to-video motion transfer;
* scene recombination using three still references, motion and temp audio;
* no Pluribus-specific nodes or rights annotations.

The committed sanitized input manifest is sufficient to reproduce and check
the JSON fixtures without access to the film. To load the generated workflow
in ComfyUI, run ``build_little_flower_reverse_assets.py`` and copy the staged
``little_flower_reverse__*`` files directly into ``ComfyUI/input`` because the
core loader widgets list only that directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from build_little_flower_reverse_assets import comfyui_input_name
from gen_rights_stress_test_workflow import NodeSpec, SCHEMAS, build_graph


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DEFAULT_MANIFEST = FIXTURES / "little_flower_reverse_input_manifest.json"
LOCAL_RECONSTRUCTION_MANIFEST = (
    ROOT / "outputs" / "little-flower-reconstruction" / "reconstruction_manifest.json"
)
UI_FIXTURE_NAME = "little_flower_reverse_workflow.json"
API_FIXTURE_NAME = "little_flower_reverse_workflow_api.json"


def asset_path(relative_path: str) -> str:
    return comfyui_input_name(relative_path)


def manifest_input_assets(manifest: dict[str, object]) -> list[str]:
    """Return and validate the graph-facing inventory without reading media."""

    try:
        cast = manifest["cast"]
        storyboards = manifest["storyboards"]
        scene_media = manifest["scene_media"]
        scenes = manifest["scenes"]
        if not all(isinstance(records, list) for records in (cast, storyboards, scene_media, scenes)):
            raise TypeError
        assets = [str(record["asset"]) for record in cast]
        assets.extend(
            [str(manifest["location_reference"]), str(manifest["prop_reference"])]
        )
        assets.extend(str(record["asset"]) for record in storyboards)
        for record in scene_media:
            assets.extend([str(record["motion_proxy"]), str(record["temp_audio"])])
        scene_ids = [str(record["scene_id"]) for record in scenes]
        storyboard_ids = [str(record["scene_id"]) for record in storyboards]
        scene_media_ids = [str(record["scene_id"]) for record in scene_media]
    except (KeyError, TypeError) as error:
        raise SystemExit("Little Flower manifest is missing graph input fields.") from error

    if not assets or any(
        not asset
        or Path(asset).is_absolute()
        or ".." in Path(asset).parts
        or "\\" in asset
        for asset in assets
    ):
        raise SystemExit("Little Flower manifest contains an unsafe graph input path.")
    if len(set(assets)) != len(assets):
        raise SystemExit("Little Flower manifest contains duplicate graph inputs.")
    if (
        len(set(scene_ids)) != len(scene_ids)
        or len(set(storyboard_ids)) != len(storyboard_ids)
        or set(storyboard_ids) != set(scene_ids)
    ):
        raise SystemExit("Little Flower storyboards do not match the scene inventory.")
    if (
        len(set(scene_media_ids)) != len(scene_media_ids)
        or set(scene_media_ids) != set(scene_ids)
    ):
        raise SystemExit("Little Flower scene media does not match the scene inventory.")

    expected_count = manifest.get("workflow_input_count")
    if expected_count != len(assets):
        raise SystemExit("Little Flower manifest has an invalid workflow input count.")

    declared_inputs = manifest.get("workflow_inputs")
    if declared_inputs is not None:
        if not isinstance(declared_inputs, list):
            raise SystemExit("Little Flower manifest has an invalid workflow input inventory.")
        try:
            declared_assets = [str(record["asset"]) for record in declared_inputs]
        except (KeyError, TypeError) as error:
            raise SystemExit(
                "Little Flower manifest has an invalid workflow input inventory."
            ) from error
        if sorted(declared_assets) != sorted(assets):
            raise SystemExit(
                "Little Flower manifest workflow inputs do not match its graph inputs."
            )
    return assets


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, object]:
    if not path.is_file():
        raise SystemExit(
            f"Little Flower fixture input manifest is missing: {path}"
        )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise SystemExit("Little Flower manifest must be a JSON object.")
    manifest_input_assets(manifest)
    return manifest


def seedream_values(prompt: str, seed: int) -> tuple[list[object], dict[str, object]]:
    values = [
        prompt,
        "seedream 5.0 lite",
        "(2K) 2848x1600 (16:9)",
        2048,
        2048,
        4,
        False,
        seed,
        "fixed",
        False,
    ]
    widgets = {
        "prompt": prompt,
        "model": "seedream 5.0 lite",
        "model.size_preset": "(2K) 2848x1600 (16:9)",
        "model.width": 2048,
        "model.height": 2048,
        "model.max_images": 4,
        "model.fail_on_partial": False,
        "seed": seed,
        "watermark": False,
    }
    return values, widgets


def flux_values(prompt: str, seed: int) -> tuple[list[object], dict[str, object]]:
    values = [prompt, "16:9", 3.0, 50, seed, "fixed", False]
    widgets = {
        "prompt": prompt,
        "aspect_ratio": "16:9",
        "guidance": 3.0,
        "steps": 50,
        "seed": seed,
        "prompt_upsampling": False,
    }
    return values, widgets


def kling_values(prompt: str) -> tuple[list[object], dict[str, object]]:
    negative = (
        "identity drift, face morph, wardrobe changes, extra people, missing people, "
        "warped hands, broken eyelines, jump cuts, text, watermark"
    )
    values = [prompt, negative, "kling-v2-5-turbo", 0.8, "pro", "16:9", "5"]
    widgets = {
        "prompt": prompt,
        "negative_prompt": negative,
        "model_name": "kling-v2-5-turbo",
        "cfg_scale": 0.8,
        "mode": "pro",
        "aspect_ratio": "16:9",
        "duration": "5",
    }
    return values, widgets


def runway_values(prompt: str, seed: int) -> tuple[list[object], dict[str, object]]:
    values = [prompt, seed, "fixed", "low"]
    widgets = {"prompt": prompt, "seed": seed, "public_figure_threshold": "low"}
    return values, widgets


def seedance_values(prompt: str, seed: int) -> tuple[list[object], dict[str, object]]:
    values = [
        "Seedance 2.0 Fast",
        prompt,
        "480p",
        "adaptive",
        5,
        True,
        True,
        False,
        seed,
        "fixed",
        False,
    ]
    widgets = {
        "model": "Seedance 2.0 Fast",
        "model.prompt": prompt,
        "model.resolution": "480p",
        "model.ratio": "adaptive",
        "model.duration": 5,
        "model.generate_audio": True,
        "model.auto_downscale": True,
        "model.auto_upscale": False,
        "seed": seed,
        "watermark": False,
    }
    return values, widgets


def save_video_values(prefix: str) -> tuple[list[object], dict[str, object]]:
    return [prefix, "auto", "auto"], {"filename_prefix": prefix, "format": "auto", "codec": "auto"}


def build_specs(manifest: dict[str, object]) -> tuple[list[NodeSpec], list[tuple], list[tuple]]:
    nodes: list[NodeSpec] = []
    notes: list[tuple] = []
    groups: list[tuple] = []
    next_id = 1

    def add(
        class_type: str,
        title: str,
        pos: tuple[int, int],
        size: tuple[int, int],
        widgets_values: list[object],
        api_widgets: dict[str, object],
        connections: dict[str, tuple[int, int]] | None = None,
    ) -> int:
        nonlocal next_id
        node_id = next_id
        next_id += 1
        nodes.append(
            NodeSpec(
                node_id,
                class_type,
                title,
                pos,
                size,
                widgets_values,
                api_widgets,
                {} if connections is None else connections,
            )
        )
        return node_id

    cast_node_by_role: dict[str, int] = {}
    party_candidate_nodes: list[int] = []
    cast_records = manifest["cast"]
    for index, record in enumerate(cast_records):
        column = index % 3
        row = index // 3
        filename = asset_path(record["asset"])
        role = record["role"]
        identity_state = str(record.get("identity_state") or "needs producer review")
        node_id = add(
            "LoadImage",
            f"IDENTITY EVIDENCE · {role} · {identity_state.replace('_', ' ')}",
            (-2540 + (column * 400), -420 + (row * 430)),
            (350, 370),
            [filename, "image"],
            {"image": filename, "upload": "image"},
        )
        if record.get("evidence_type") == "anonymous_face_cluster":
            party_candidate_nodes.append(node_id)
        else:
            cast_node_by_role[role] = node_id

    cast_rows = max(1, (len(cast_records) + 2) // 3)
    visual_bible_y = -420 + (cast_rows * 430) + 80
    location_filename = asset_path(manifest["location_reference"])
    prop_filename = asset_path(manifest["prop_reference"])
    location_node = add(
        "LoadImage",
        "VISUAL BIBLE · locations",
        (-2540, visual_bible_y),
        (350, 370),
        [location_filename, "image"],
        {"image": location_filename, "upload": "image"},
    )
    prop_node = add(
        "LoadImage",
        "VISUAL BIBLE · props + wardrobe",
        (-2140, visual_bible_y),
        (350, 370),
        [prop_filename, "image"],
        {"image": prop_filename, "upload": "image"},
    )

    crowd_batch_nodes: list[int] = []
    for batch_index in range(0, len(party_candidate_nodes), 4):
        batch = party_candidate_nodes[batch_index : batch_index + 4]
        prompt = (
            "Build a neutral garden-party crowd continuity board from these anonymous visual "
            "candidates. Keep each person distinct; do not infer names, relationships, consent "
            "or clearance. This is a producer-review reference, not an identity claim."
        )
        values, widgets = seedream_values(prompt, 3900 + batch_index)
        crowd_batch_nodes.append(
            add(
                "ByteDanceSeedreamNodeV2",
                f"SC08 CROWD BATCH {1 + (batch_index // 4):02d} · anonymous candidates",
                (-1260, -420 + ((batch_index // 4) * 460)),
                (500, 410),
                values,
                widgets,
                {
                    f"model.images.image_{index}": (node_id, 0)
                    for index, node_id in enumerate(batch, start=1)
                },
            )
        )
    crowd_master: int | None = None
    if len(crowd_batch_nodes) == 1:
        crowd_master = crowd_batch_nodes[0]
    elif crowd_batch_nodes:
        prompt = (
            "Combine these anonymous crowd batches into one SC08 continuity reference. Preserve "
            "all distinct candidate appearances. Do not attach credited names or rights states."
        )
        values, widgets = seedream_values(prompt, 3999)
        crowd_master = add(
            "ByteDanceSeedreamNodeV2",
            "SC08 CROWD MASTER · anonymous producer-review composite",
            (-680, -420),
            (500, 410),
            values,
            widgets,
            {
                f"model.images.image_{index}": (node_id, 0)
                for index, node_id in enumerate(crowd_batch_nodes, start=1)
            },
        )

    group_height = max(2050, visual_bible_y + 1120)
    groups.append(
        (
            "REFERENCE LIBRARY · IDENTITY EVIDENCE / LOCATIONS / PROPS",
            (-2640, -620, 2560, group_height),
            "#49351f",
        )
    )
    notes.append(
        (
            10001,
            (-2140, visual_bible_y + 390),
            (760, 300),
            "# Little Flower · reverse-engineered pre-Pluribus graph\nFace-cropped identity evidence is derived from actual finished-film frames. Role labels are local similarity suggestions requiring producer confirmation. Party candidates remain anonymous and are not mapped to credited names. No sheet supplies rights state, roster IDs, contracts or Pluribus Source Markers.",
        )
    )

    scene_media = {record["scene_id"]: record for record in manifest["scene_media"]}
    storyboards = {record["scene_id"]: record for record in manifest["storyboards"]}
    prop_scenes = {"SC01", "SC03", "SC05"}
    camera_moves = {
        "SC01": "slow creeping dolly-in followed by a sharp macro insert",
        "SC02": "gentle handheld push-in with natural breathing drift",
        "SC03": "measured slider move across the tea table",
        "SC04": "observational aisle tracking move",
        "SC05": "oppressive slow push followed by a reactive handheld snap",
        "SC06": "side-by-side handheld walk-and-talk tracking",
        "SC07": "nearly locked intimate push-in",
        "SC08": "energetic reactive handheld movement with crowd whip-pans",
    }

    for scene_index, scene in enumerate(manifest["scenes"]):
        scene_id = scene["scene_id"]
        base_y = -520 + (scene_index * 1320)
        roles = scene["roles"]
        storyboard_filename = asset_path(storyboards[scene_id]["asset"])
        motion_filename = asset_path(scene_media[scene_id]["motion_proxy"])
        audio_filename = asset_path(scene_media[scene_id]["temp_audio"])

        storyboard_node = add(
            "LoadImage",
            f"{scene_id} STORYBOARD · {storyboards[scene_id]['shot_count']} reconstructed shots",
            (0, base_y),
            (340, 350),
            [storyboard_filename, "image"],
            {"image": storyboard_filename, "upload": "image"},
        )
        motion_node = add(
            "LoadVideo",
            f"{scene_id} MOTION REFERENCE · recorded performances",
            (0, base_y + 400),
            (340, 300),
            [motion_filename, "file"],
            {"file": motion_filename, "upload": "file"},
        )
        audio_node = add(
            "LoadAudio",
            f"{scene_id} TEMP AUDIO · dialogue / voices / music / ambience",
            (0, base_y + 750),
            (340, 170),
            [audio_filename, "audio"],
            {"audio": audio_filename},
        )

        reference_nodes = [storyboard_node]
        reference_labels = ["the reconstructed storyboard"]
        for role in roles:
            if role == "Featured Extras":
                if crowd_master is not None:
                    reference_nodes.append(crowd_master)
                    reference_labels.append(
                        "the anonymous party-candidate continuity master"
                    )
                continue
            reference_nodes.append(cast_node_by_role[role])
            reference_labels.append(f"the {role} identity-evidence anchor")
        if len(reference_nodes) < 4:
            bible_node = prop_node if scene_id in prop_scenes else location_node
            reference_nodes.append(bible_node)
            reference_labels.append("the prop/wardrobe bible" if scene_id in prop_scenes else "the location bible")
        reference_nodes = reference_nodes[:4]
        reference_labels = reference_labels[:4]
        numbered_refs = "; ".join(
            f"image {index} is {label}" for index, label in enumerate(reference_labels, start=1)
        )
        master_prompt = (
            f"Photorealistic cinematic previsualization for {scene_id}, {scene['title']}. "
            f"{numbered_refs}. {scene['generation_prompt']} Build one approved 16:9 scene-master frame. "
            "Preserve the producer-confirmed character appearances, wardrobe, lighting direction and location geography across later shots. Do not treat visual similarity or a credit label as rights clearance."
        )
        seed_values, seed_widgets = seedream_values(master_prompt, 4100 + scene_index)
        seed_connections = {
            f"model.images.image_{index}": (node_id, 0)
            for index, node_id in enumerate(reference_nodes, start=1)
        }
        scene_master = add(
            "ByteDanceSeedreamNodeV2",
            f"{scene_id} SCENE MASTER · multi-reference continuity lock",
            (410, base_y),
            (500, 410),
            seed_values,
            seed_widgets,
            seed_connections,
        )

        shot_prompts = (
            (
                "A · 24mm wide master",
                "Reframe the approved scene master as a 24mm wide establishing/master shot. Preserve every character, wardrobe detail, prop, eyeline and location feature. Maintain the established light and a clear physical geography.",
            ),
            (
                "B · 50mm performance coverage",
                "Reframe as a 50mm medium performance shot favoring the speaking or acting character while retaining the other scene participants at the correct eyeline. Preserve identity and wardrobe exactly; natural cinematic depth of field.",
            ),
            (
                "C · 85mm insert / over-shoulder",
                "Reframe as an 85mm close reaction, insert or over-shoulder drawn from the storyboard. A person may be cropped or soft, but preserve every contributing identity, gesture, hand, prop and wardrobe detail.",
            ),
        )
        flux_nodes: list[int] = []
        for shot_index, (shot_title, shot_prompt) in enumerate(shot_prompts):
            values, widgets = flux_values(
                f"{shot_prompt} Sequence context: {scene['generation_prompt']}",
                5100 + (scene_index * 10) + shot_index,
            )
            flux_nodes.append(
                add(
                    "FluxKontextProImageNode",
                    f"{scene_id} SHOT {shot_title}",
                    (980, base_y + (shot_index * 330)),
                    (420, 300),
                    values,
                    widgets,
                    {"input_image": (scene_master, 0)},
                )
            )

        for shot_index, flux_node in enumerate(flux_nodes):
            motion_prompt = (
                f"Animate this locked {scene_id} storyboard frame with a {camera_moves[scene_id]}. "
                f"Use this physical beat: {scene['generation_prompt']} Preserve all distinct faces, bodies, "
                "wardrobe, hand actions and screen direction. One continuous five-second shot with no cut."
            )
            values, widgets = kling_values(motion_prompt)
            kling_node = add(
                "KlingImage2VideoNode",
                f"{scene_id} I2V SHOT {chr(65 + shot_index)} · camera + performance",
                (1460, base_y + (shot_index * 330)),
                (430, 310),
                values,
                widgets,
                {"start_frame": (flux_node, 0)},
            )
            prefix = f"video/little_flower_reverse/{scene_id}/shot_{chr(65 + shot_index)}"
            save_values, save_widgets = save_video_values(prefix)
            add(
                "SaveVideo",
                f"{scene_id} SHOT {chr(65 + shot_index)} OUTPUT",
                (1940, base_y + (shot_index * 330)),
                (350, 280),
                save_values,
                save_widgets,
                {"video": (kling_node, 0)},
            )

        runway_prompt = (
            f"Re-render the recorded {scene_id} motion reference as the approved scene design. "
            "Retain original timing, blocking, gestures, facial performance, dialogue pacing, eyelines and camera motion, "
            "while matching the reconstructed cast, wardrobe, lighting and location established in the scene master."
        )
        runway_values_list, runway_widgets = runway_values(runway_prompt, 6100 + scene_index)
        runway_node = add(
            "RunwayAleph2VideoToVideoNode",
            f"{scene_id} V2V · recorded motion + performance transfer",
            (1460, base_y + 990),
            (470, 330),
            runway_values_list,
            runway_widgets,
            {"video": (motion_node, 0)},
        )

        final_prompt = (
            f"Build the approved {scene_id} sequence from reference images 1-3, moving from wide master to "
            "performance coverage to close insert. Reference video 1 controls the original actors' blocking, gestures, "
            "facial timing and camera rhythm. Reference audio 1 controls dialogue, voice identity, timing, music and room tone. "
            f"Maintain cast and continuity throughout. Scene brief: {scene['generation_prompt']}"
        )
        final_values, final_widgets = seedance_values(final_prompt, 7100 + scene_index)
        final_node = add(
            "ByteDance2ReferenceNode",
            f"{scene_id} FINAL SCENE · stills + motion + temp audio",
            (2350, base_y + 330),
            (540, 470),
            final_values,
            final_widgets,
            {
                "model.reference_images.image_1": (flux_nodes[0], 0),
                "model.reference_images.image_2": (flux_nodes[1], 0),
                "model.reference_images.image_3": (flux_nodes[2], 0),
                "model.reference_videos.video_1": (runway_node, 0),
                "model.reference_audios.audio_1": (audio_node, 0),
            },
        )
        final_prefix = f"video/little_flower_reverse/{scene_id}/final_scene"
        final_save_values, final_save_widgets = save_video_values(final_prefix)
        add(
            "SaveVideo",
            f"{scene_id} FINAL SCENE OUTPUT",
            (2950, base_y + 390),
            (380, 330),
            final_save_values,
            final_save_widgets,
            {"video": (final_node, 0)},
        )

        notes.append(
            (
                10100 + scene_index,
                (410, base_y + 880),
                (900, 240),
                f"## {scene_id} · {scene['title']}\nReconstructed range: {scene['start_seconds']:.3f}s–{scene['end_seconds']:.3f}s · "
                f"{storyboards[scene_id]['shot_count']} candidate shots · cast roles: {', '.join(roles)}. "
                "This scene template stands in for a batched per-shot production run; queueing every hosted node would consume credits.",
            )
        )
        groups.append(
            (
                f"{scene_id} · {scene['title'].upper()}",
                (-80, base_y - 120, 3480, 1270),
                "#2d3c50" if scene_index % 2 == 0 else "#274237",
            )
        )

    notes.append(
        (
            10200,
            (2350, 10070),
            (980, 250),
            "## Editorial conform outside the generation graph\nThe eight scene outputs would be conformed to the 147-shot manifest, mixed, subtitled and combined with the reconstructed credits in an NLE. The finished delivery is not loaded here, so Pluribus must reason from cast sheets, storyboard pixels, motion proxies, temp audio and generated branches rather than from a declared master-source marker.",
        )
    )
    return nodes, notes, groups


def build_fixture_documents(
    manifest: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    """Build both committed workflow documents from a validated manifest."""

    manifest_input_assets(manifest)
    nodes, notes, groups = build_specs(manifest)
    schemas = dict(SCHEMAS)
    schemas["LoadAudio"] = {"inputs": [], "outputs": [("AUDIO", "AUDIO")]}
    return build_graph(
        nodes,
        notes,
        groups,
        "little-flower-reverse-engineered-pre-pluribus-v2-identity-evidence",
        schemas=schemas,
    )


def fixture_text(document: dict[str, object]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def sync_fixture_documents(
    manifest: dict[str, object], fixtures_dir: Path, *, check: bool
) -> tuple[dict[str, object], dict[str, object]]:
    """Write fixtures or fail without mutation when committed files are stale."""

    ui, api = build_fixture_documents(manifest)
    documents = {
        fixtures_dir / UI_FIXTURE_NAME: fixture_text(ui),
        fixtures_dir / API_FIXTURE_NAME: fixture_text(api),
    }
    if check:
        stale = [
            path.name
            for path, expected in documents.items()
            if not path.is_file() or path.read_text(encoding="utf-8") != expected
        ]
        if stale:
            raise SystemExit(
                "Little Flower workflow fixtures are stale: "
                + ", ".join(sorted(stale))
                + ". Regenerate without --check."
            )
        return ui, api

    fixtures_dir.mkdir(parents=True, exist_ok=True)
    for path, document in documents.items():
        path.write_text(document, encoding="utf-8")
    return ui, api


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=(
            "Graph input manifest. Defaults to the committed sanitized fixture manifest; "
            f"use {LOCAL_RECONSTRUCTION_MANIFEST} to regenerate from local analysis."
        ),
    )
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail without writing when committed workflow fixtures are not deterministic.",
    )
    args = parser.parse_args()
    manifest = load_manifest(args.manifest.expanduser().resolve())
    fixtures_dir = args.fixtures_dir.expanduser().resolve()
    ui, api = sync_fixture_documents(manifest, fixtures_dir, check=args.check)
    action = "verified" if args.check else "wrote"
    print(
        f"{action} {len(ui['nodes'])} UI nodes / {len(api)} API nodes / "
        f"{ui['last_link_id']} links from {manifest['workflow_input_count']} validated inputs"
    )


if __name__ == "__main__":
    main()
