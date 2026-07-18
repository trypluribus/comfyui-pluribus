"""Generate the multi-performer rights stress-test workflow.

The graph is intentionally more complicated than a normal production graph:

* one self-likeness LoRA and two ordinary photos resolve to the same person;
* three distinct assets for one performer feed a multi-reference image edit;
* the resulting ensemble frame branches into three camera/scene variants;
* one branch becomes image-to-video while an unrelated source performance is
  separately transformed video-to-video;
* the still and motion branches are recombined in one multi-reference video;
* no Pluribus-specific nodes or annotations are present, so the scanner must
  work only from the creative graph it encounters.

The generated UI JSON only uses node shapes already present in the local
ComfyUI 0.25.0 / frontend 1.45.15 installation. The API JSON is suitable for
the Pluribus scanner and for regression inspection without opening ComfyUI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@dataclass(frozen=True)
class NodeSpec:
    node_id: int
    class_type: str
    title: str
    pos: tuple[int, int]
    size: tuple[int, int]
    widgets_values: list[object] = field(default_factory=list)
    api_widgets: dict[str, object] = field(default_factory=dict)
    connections: dict[str, tuple[int, int]] = field(default_factory=dict)
    mode: int = 0
    color: str | None = None
    bgcolor: str | None = None


# Connection sockets and outputs are kept explicit so this generator remains
# deterministic even when ComfyUI is not running locally.
SCHEMAS: dict[str, dict[str, list[tuple[str, str, bool]] | list[tuple[str, str]]]] = {
    "CheckpointLoaderSimple": {
        "inputs": [],
        "outputs": [("MODEL", "MODEL"), ("CLIP", "CLIP"), ("VAE", "VAE")],
    },
    "LoraLoader": {
        "inputs": [("model", "MODEL", False), ("clip", "CLIP", False)],
        "outputs": [("MODEL", "MODEL"), ("CLIP", "CLIP")],
    },
    "IPAdapterUnifiedLoader": {
        "inputs": [("model", "MODEL", False), ("ipadapter", "IPADAPTER", True)],
        "outputs": [("model", "MODEL"), ("ipadapter", "IPADAPTER")],
    },
    "LoadImage": {
        "inputs": [],
        "outputs": [("IMAGE", "IMAGE"), ("MASK", "MASK")],
    },
    "ImageScale": {
        "inputs": [("image", "IMAGE", False)],
        "outputs": [("IMAGE", "IMAGE")],
    },
    "ImageBatch": {
        "inputs": [("image1", "IMAGE", False), ("image2", "IMAGE", False)],
        "outputs": [("IMAGE", "IMAGE")],
    },
    "IPAdapterAdvanced": {
        "inputs": [
            ("model", "MODEL", False),
            ("ipadapter", "IPADAPTER", False),
            ("image", "IMAGE", False),
            ("image_negative", "IMAGE", True),
            ("attn_mask", "MASK", True),
            ("clip_vision", "CLIP_VISION", True),
        ],
        "outputs": [("MODEL", "MODEL")],
    },
    "CLIPTextEncode": {
        "inputs": [("clip", "CLIP", False)],
        "outputs": [("CONDITIONING", "CONDITIONING")],
    },
    "EmptyLatentImage": {
        "inputs": [],
        "outputs": [("LATENT", "LATENT")],
    },
    "KSampler": {
        "inputs": [
            ("model", "MODEL", False),
            ("positive", "CONDITIONING", False),
            ("negative", "CONDITIONING", False),
            ("latent_image", "LATENT", False),
        ],
        "outputs": [("LATENT", "LATENT")],
    },
    "VAEDecode": {
        "inputs": [("samples", "LATENT", False), ("vae", "VAE", False)],
        "outputs": [("IMAGE", "IMAGE")],
    },
    "SaveImage": {
        "inputs": [("images", "IMAGE", False)],
        "outputs": [],
    },
    "ByteDanceSeedreamNodeV2": {
        "inputs": [
            ("model.images.image_1", "IMAGE", True),
            ("model.images.image_2", "IMAGE", True),
            ("model.images.image_3", "IMAGE", True),
            ("model.images.image_4", "IMAGE", True),
        ],
        "outputs": [("IMAGE", "IMAGE")],
    },
    "FluxKontextProImageNode": {
        "inputs": [("input_image", "IMAGE", True)],
        "outputs": [("IMAGE", "IMAGE")],
    },
    "KlingImage2VideoNode": {
        "inputs": [("start_frame", "IMAGE", False)],
        "outputs": [("VIDEO", "VIDEO"), ("video_id", "STRING"), ("duration", "STRING")],
    },
    "LoadVideo": {
        "inputs": [],
        "outputs": [("VIDEO", "VIDEO")],
    },
    "RunwayAleph2VideoToVideoNode": {
        "inputs": [
            ("video", "VIDEO", False),
            ("keyframes", "RUNWAY_ALEPH2_KEYFRAME", True),
            ("prompt_images", "RUNWAY_ALEPH2_PROMPT_IMAGE", True),
        ],
        "outputs": [("VIDEO", "VIDEO")],
    },
    "ByteDance2ReferenceNode": {
        "inputs": [
            ("model.reference_images.image_1", "IMAGE", True),
            ("model.reference_images.image_2", "IMAGE", True),
            ("model.reference_images.image_3", "IMAGE", True),
            ("model.reference_videos.video_1", "VIDEO", True),
            ("model.reference_audios.audio_1", "AUDIO", True),
            ("model.reference_assets.asset_1", "STRING", True),
        ],
        "outputs": [("VIDEO", "VIDEO")],
    },
    "SaveVideo": {
        "inputs": [("video", "VIDEO", False)],
        "outputs": [],
    },
}


POSITIVE = (
    "alhassan_lrx26, a man in his early 30s watching a tense movie "
    "on a deep blue couch in a warm modern living room, expressive reaction, "
    "premium streaming-service campaign, cinematic practical lighting, natural "
    "skin texture, crisp face, believable hands, 35mm lens, shallow depth of field"
)

NEGATIVE = (
    "duplicate person, merged faces, deformed hands, extra fingers, extra limbs, "
    "warped eyes, waxy skin, text, watermark, logo, low resolution, motion blur"
)


NODES = [
    # ---- 1. Base generation: one self-likeness LoRA + two photos of the same person
    NodeSpec(
        1,
        "CheckpointLoaderSimple",
        "SDXL base",
        (-2240, -140),
        (340, 100),
        ["sd_xl_base_1.0.safetensors"],
        {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    ),
    NodeSpec(
        2,
        "LoraLoader",
        "Identity LoRA · alhassan_lrx26 self-likeness",
        (-1840, -140),
        (360, 130),
        ["alhassan_lrx26_sdxl_webcam_v0.safetensors", 0.72, 0.72],
        {
            "lora_name": "alhassan_lrx26_sdxl_webcam_v0.safetensors",
            "strength_model": 0.72,
            "strength_clip": 0.72,
        },
        {"model": (1, 0), "clip": (1, 1)},
    ),
    NodeSpec(
        4,
        "IPAdapterUnifiedLoader",
        "IPAdapter · PLUS FACE",
        (-960, -140),
        (340, 90),
        ["PLUS FACE (portraits)"],
        {"preset": "PLUS FACE (portraits)"},
        {"model": (2, 0)},
    ),
    NodeSpec(
        5,
        "LoadImage",
        "Identity reference A · neutral headshot",
        (-1840, 80),
        (310, 320),
        ["IMG_0003.jpeg", "image"],
        {"image": "IMG_0003.jpeg", "upload": "image"},
    ),
    NodeSpec(
        6,
        "LoadImage",
        "Identity reference B · location / wardrobe photo",
        (-1480, 80),
        (310, 320),
        ["IMG_2505.jpeg", "image"],
        {"image": "IMG_2505.jpeg", "upload": "image"},
    ),
    NodeSpec(
        7,
        "ImageBatch",
        "Two photos of one performer → conditioning batch",
        (-760, 430),
        (300, 90),
        [],
        {},
        {"image1": (33, 0), "image2": (34, 0)},
    ),
    NodeSpec(
        8,
        "IPAdapterAdvanced",
        "Multi-photo IPAdapter identity conditioning",
        (-560, -140),
        (360, 280),
        [0.76, "linear", "average", 0.0, 0.82, "K+V"],
        {
            "weight": 0.76,
            "weight_type": "linear",
            "combine_embeds": "average",
            "start_at": 0.0,
            "end_at": 0.82,
            "embeds_scaling": "K+V",
        },
        {"model": (4, 0), "ipadapter": (4, 1), "image": (37, 0)},
    ),
    NodeSpec(
        9,
        "CLIPTextEncode",
        "Hero performer prompt",
        (-160, 160),
        (440, 180),
        [POSITIVE],
        {"text": POSITIVE},
        {"clip": (2, 1)},
    ),
    NodeSpec(
        10,
        "CLIPTextEncode",
        "Negative prompt",
        (-160, 350),
        (440, 140),
        [NEGATIVE],
        {"text": NEGATIVE},
        {"clip": (2, 1)},
    ),
    NodeSpec(
        11,
        "EmptyLatentImage",
        "16:9 campaign keyframe",
        (300, 430),
        (300, 110),
        [1216, 704, 1],
        {"width": 1216, "height": 704, "batch_size": 1},
    ),
    NodeSpec(
        12,
        "KSampler",
        "Generate blended ensemble keyframe",
        (300, -80),
        (310, 270),
        [20260717, "fixed", 30, 6.2, "dpmpp_2m", "karras", 1.0],
        {
            "seed": 20260717,
            "steps": 30,
            "cfg": 6.2,
            "sampler_name": "dpmpp_2m",
            "scheduler": "karras",
            "denoise": 1.0,
        },
        {"model": (8, 0), "positive": (9, 0), "negative": (10, 0), "latent_image": (11, 0)},
    ),
    NodeSpec(
        13,
        "VAEDecode",
        "Decode base ensemble",
        (670, -80),
        (230, 80),
        [],
        {},
        {"samples": (12, 0), "vae": (1, 2)},
    ),
    NodeSpec(
        14,
        "SaveImage",
        "Checkpoint · blended cast keyframe",
        (950, -100),
        (330, 320),
        ["rights_stress/01_blended_cast"],
        {"filename_prefix": "rights_stress/01_blended_cast"},
        {"images": (13, 0)},
    ),

    # ---- 2. Add a second performer through three different reference assets
    NodeSpec(
        15,
        "LoadImage",
        "Tyler · facial likeness reference",
        (-1840, 760),
        (310, 320),
        ["TYLER_FACE_MASTER_A.jpg", "image"],
        {"image": "TYLER_FACE_MASTER_A.jpg", "upload": "image"},
    ),
    NodeSpec(
        16,
        "LoadImage",
        "Tyler · body / physical-performance reference",
        (-1480, 760),
        (310, 320),
        ["TYLER_BODY_GYM_A.jpg", "image"],
        {"image": "TYLER_BODY_GYM_A.jpg", "upload": "image"},
    ),
    NodeSpec(
        17,
        "LoadImage",
        "Tyler · tattoo / identifying-feature sheet",
        (-1120, 760),
        (310, 320),
        ["TYLER_TATTOO_CONDITIONING_V02_3840x2160.png", "image"],
        {"image": "TYLER_TATTOO_CONDITIONING_V02_3840x2160.png", "upload": "image"},
    ),
    NodeSpec(
        18,
        "ByteDanceSeedreamNodeV2",
        "Multi-reference cast composite · same performer, 3 asset keys",
        (-520, 700),
        (500, 390),
        [
            (
                "Use image 1 as the exact living-room composition. Add the same male performer shown "
                "in reference images 2, 3 and 4 as a friend on the right side of the couch. "
                "Reference 2 controls face, reference 3 controls build and pose, and reference 4 controls "
                "tattoo placement. Preserve the first performer from image 1. Photoreal premium campaign."
            ),
            "seedream 5.0 lite",
            "(2K) 2848x1600 (16:9)",
            2048,
            2048,
            4,
            False,
            20260717,
            "fixed",
            False,
        ],
        {
            "prompt": (
                "Use image 1 as the exact living-room composition. Add the same male performer shown "
                "in reference images 2, 3 and 4 as a friend on the right side of the couch. "
                "Reference 2 controls face, reference 3 controls build and pose, and reference 4 controls "
                "tattoo placement. Preserve the first performer from image 1. Photoreal premium campaign."
            ),
            "model": "seedream 5.0 lite",
            "model.size_preset": "(2K) 2848x1600 (16:9)",
            "model.width": 2048,
            "model.height": 2048,
            "model.max_images": 4,
            "model.fail_on_partial": False,
            "seed": 20260717,
            "watermark": False,
        },
        {
            "model.images.image_1": (13, 0),
            "model.images.image_2": (15, 0),
            "model.images.image_3": (16, 0),
            "model.images.image_4": (17, 0),
        },
    ),
    NodeSpec(
        19,
        "SaveImage",
        "Checkpoint · multi-reference cast composite",
        (80, 720),
        (330, 320),
        ["rights_stress/02_multiref_cast"],
        {"filename_prefix": "rights_stress/02_multiref_cast"},
        {"images": (18, 0)},
    ),

    # ---- 3. Shot/angle branching, all inheriting the same sources
    NodeSpec(
        20,
        "FluxKontextProImageNode",
        "Shot A · wide couch master",
        (600, 600),
        (430, 330),
        [
            (
                "Reframe as a symmetrical wide master shot of both friends on the blue couch. "
                "Keep both faces, bodies, tattoos and wardrobe identity consistent. Add a glowing television "
                "off camera and warm practical lamps. Premium cinematic streaming campaign."
            ),
            "16:9",
            3.0,
            50,
            1717,
            "fixed",
            False,
        ],
        {
            "prompt": (
                "Reframe as a symmetrical wide master shot of both friends on the blue couch. "
                "Keep both faces, bodies, tattoos and wardrobe identity consistent. Add a glowing television "
                "off camera and warm practical lamps. Premium cinematic streaming campaign."
            ),
            "aspect_ratio": "16:9",
            "guidance": 3.0,
            "steps": 50,
            "seed": 1717,
            "prompt_upsampling": False,
        },
        {"input_image": (18, 0)},
    ),
    NodeSpec(
        21,
        "FluxKontextProImageNode",
        "Shot B · tight reaction (only some cast visible)",
        (600, 980),
        (430, 330),
        [
            (
                "Create a tight 85mm reaction two-shot favoring the first performer while the second "
                "remains soft and partially cropped at the frame edge. Preserve both identities and exact wardrobe. "
                "Tense, funny surprise, cinematic warm-blue contrast."
            ),
            "16:9",
            3.0,
            50,
            1818,
            "fixed",
            False,
        ],
        {
            "prompt": (
                "Create a tight 85mm reaction two-shot favoring the first performer while the second "
                "remains soft and partially cropped at the frame edge. Preserve both identities and exact wardrobe. "
                "Tense, funny surprise, cinematic warm-blue contrast."
            ),
            "aspect_ratio": "16:9",
            "guidance": 3.0,
            "steps": 50,
            "seed": 1818,
            "prompt_upsampling": False,
        },
        {"input_image": (18, 0)},
    ),
    NodeSpec(
        22,
        "FluxKontextProImageNode",
        "Shot C · over-shoulder product reveal",
        (600, 1360),
        (430, 330),
        [
            (
                "Create an over-the-shoulder shot from behind the tattooed performer toward his friend, "
                "with a fictional silver streaming remote prominent in the foreground. Preserve the tattooed "
                "performer's arm tattoos, both faces and wardrobe continuity. Cinematic commercial lighting."
            ),
            "16:9",
            3.0,
            50,
            1919,
            "fixed",
            False,
        ],
        {
            "prompt": (
                "Create an over-the-shoulder shot from behind the tattooed performer toward his friend, "
                "with a fictional silver streaming remote prominent in the foreground. Preserve the tattooed "
                "performer's arm tattoos, both faces and wardrobe continuity. Cinematic commercial lighting."
            ),
            "aspect_ratio": "16:9",
            "guidance": 3.0,
            "steps": 50,
            "seed": 1919,
            "prompt_upsampling": False,
        },
        {"input_image": (18, 0)},
    ),
    NodeSpec(
        23,
        "SaveImage",
        "Shot A output",
        (1100, 600),
        (320, 300),
        ["rights_stress/03A_wide"],
        {"filename_prefix": "rights_stress/03A_wide"},
        {"images": (20, 0)},
    ),
    NodeSpec(
        24,
        "SaveImage",
        "Shot B output",
        (1100, 980),
        (320, 300),
        ["rights_stress/03B_reaction"],
        {"filename_prefix": "rights_stress/03B_reaction"},
        {"images": (21, 0)},
    ),
    NodeSpec(
        25,
        "SaveImage",
        "Shot C output",
        (1100, 1360),
        (320, 300),
        ["rights_stress/03C_product"],
        {"filename_prefix": "rights_stress/03C_product"},
        {"images": (22, 0)},
    ),

    # ---- 4. Image-to-video and source-performance video-to-video
    NodeSpec(
        26,
        "KlingImage2VideoNode",
        "I2V · animate the wide ensemble",
        (1500, 560),
        (430, 350),
        [
            (
                "Slow push-in as both friends react to the television: the left performer leans forward and "
                "the tattooed performer laughs and lifts the remote. Preserve both faces, bodies, tattoos and "
                "wardrobe details. Natural overlapping performance, no cuts."
            ),
            "identity drift, face morph, extra people, warped hands, tattoo changes, jump cuts, text, watermark",
            "kling-v2-5-turbo",
            0.8,
            "pro",
            "16:9",
            "5",
        ],
        {
            "prompt": (
                "Slow push-in as both friends react to the television: the left performer leans forward and "
                "the tattooed performer laughs and lifts the remote. Preserve both faces, bodies, tattoos and "
                "wardrobe details. Natural overlapping performance, no cuts."
            ),
            "negative_prompt": (
                "identity drift, face morph, extra people, warped hands, tattoo changes, jump cuts, text, watermark"
            ),
            "model_name": "kling-v2-5-turbo",
            "cfg_scale": 0.8,
            "mode": "pro",
            "aspect_ratio": "16:9",
            "duration": "5",
        },
        {"start_frame": (20, 0)},
    ),
    NodeSpec(
        27,
        "SaveVideo",
        "Preview · generated ensemble performance",
        (1990, 560),
        (470, 330),
        ["video/rights_stress/04_generated_ensemble", "auto", "auto"],
        {
            "filename_prefix": "video/rights_stress/04_generated_ensemble",
            "format": "auto",
            "codec": "auto",
        },
        {"video": (26, 0)},
    ),
    NodeSpec(
        28,
        "LoadVideo",
        "Source performance · multi-person clip, cast unresolved",
        (-1840, 1880),
        (330, 300),
        ["little_flower/lf_fight_s02.mp4", "file"],
        {"file": "little_flower/lf_fight_s02.mp4", "upload": "file"},
    ),
    NodeSpec(
        29,
        "RunwayAleph2VideoToVideoNode",
        "V2V · retain motion, replace scene and styling",
        (-1420, 1880),
        (440, 350),
        [
            (
                "Transform the physical performance into a glossy streaming-service reaction insert set in "
                "the same warm living room as the campaign. Retain the original timing, gestures, facial "
                "expressions and camera movement, but replace wardrobe and environment."
            ),
            2727,
            "fixed",
            "low",
        ],
        {
            "prompt": (
                "Transform the physical performance into a glossy streaming-service reaction insert set in "
                "the same warm living room as the campaign. Retain the original timing, gestures, facial "
                "expressions and camera movement, but replace wardrobe and environment."
            ),
            "seed": 2727,
            "public_figure_threshold": "low",
        },
        {"video": (28, 0)},
    ),
    NodeSpec(
        30,
        "SaveVideo",
        "Preview · transformed source performance",
        (-900, 1880),
        (470, 330),
        ["video/rights_stress/05_source_performance_v2v", "auto", "auto"],
        {
            "filename_prefix": "video/rights_stress/05_source_performance_v2v",
            "format": "auto",
            "codec": "auto",
        },
        {"video": (29, 0)},
    ),

    # ---- 5. Recombine three still branches with transformed source motion
    NodeSpec(
        31,
        "ByteDance2ReferenceNode",
        "FINAL · 3 shot refs + inherited source performance",
        (1520, 1120),
        (520, 460),
        [
            "Seedance 2.0 Fast",
            (
                "Create a continuous five-second campaign moment with the two friends from reference images "
                "1-3 on the blue couch. Begin on the wide master, move through the tight reaction, and land on "
                "the over-shoulder product reveal. Use reference video 1 for timing and group reaction energy. "
                "Preserve both faces, bodies, tattoos, wardrobe and distinct performance. Generate subtle "
                "room tone and overlapping nonverbal reactions only; no intelligible dialogue."
            ),
            "480p",
            "adaptive",
            5,
            True,
            True,
            False,
            3131,
            "fixed",
            False,
        ],
        {
            "model": "Seedance 2.0 Fast",
            "model.prompt": (
                "Create a continuous five-second campaign moment with the two friends from reference images "
                "1-3 on the blue couch. Begin on the wide master, move through the tight reaction, and land on "
                "the over-shoulder product reveal. Use reference video 1 for timing and group reaction energy. "
                "Preserve both faces, bodies, tattoos, wardrobe and distinct performance. Generate subtle "
                "room tone and overlapping nonverbal reactions only; no intelligible dialogue."
            ),
            "model.resolution": "480p",
            "model.ratio": "adaptive",
            "model.duration": 5,
            "model.generate_audio": True,
            "model.auto_downscale": True,
            "model.auto_upscale": False,
            "seed": 3131,
            "watermark": False,
        },
        {
            "model.reference_images.image_1": (20, 0),
            "model.reference_images.image_2": (21, 0),
            "model.reference_images.image_3": (22, 0),
            "model.reference_videos.video_1": (29, 0),
        },
    ),
    NodeSpec(
        32,
        "SaveVideo",
        "FINAL OUTPUT · rights lineage is deliberately ambiguous",
        (2110, 1120),
        (500, 420),
        ["video/rights_stress/06_final_multisource", "auto", "auto"],
        {
            "filename_prefix": "video/rights_stress/06_final_multisource",
            "format": "auto",
            "codec": "auto",
        },
        {"video": (31, 0)},
    ),

    # The two photos have different native dimensions. An actual creative
    # graph must normalize them before ImageBatch or the local lane will fail.
    NodeSpec(
        33,
        "ImageScale",
        "Normalize headshot for IPAdapter batch",
        (-1840, 430),
        (310, 130),
        ["lanczos", 1024, 1024, "center"],
        {"upscale_method": "lanczos", "width": 1024, "height": 1024, "crop": "center"},
        {"image": (5, 0)},
    ),
    NodeSpec(
        34,
        "ImageScale",
        "Normalize location photo for IPAdapter batch",
        (-1480, 430),
        (310, 130),
        ["lanczos", 1024, 1024, "center"],
        {"upscale_method": "lanczos", "width": 1024, "height": 1024, "crop": "center"},
        {"image": (6, 0)},
    ),
    NodeSpec(
        35,
        "LoadImage",
        "Understory · derived character sheet of the same performer",
        (-1120, 80),
        (310, 320),
        ["hero-cast-final_00002_.png", "image"],
        {"image": "hero-cast-final_00002_.png", "upload": "image"},
    ),
    NodeSpec(
        36,
        "ImageScale",
        "Normalize character sheet for IPAdapter batch",
        (-1120, 430),
        (310, 130),
        ["lanczos", 1024, 1024, "center"],
        {"upscale_method": "lanczos", "width": 1024, "height": 1024, "crop": "center"},
        {"image": (35, 0)},
    ),
    NodeSpec(
        37,
        "ImageBatch",
        "Add derived character sheet to identity batch",
        (-400, 560),
        (300, 90),
        [],
        {},
        {"image1": (7, 0), "image2": (36, 0)},
    ),

]


NOTES = [
    (
        60,
        (-2940, -140),
        (600, 720),
        "# Multi-performer rights stress test\n\n"
        "An advanced creative graph as it would exist before a rights/provenance tool is used. It mixes "
        "model-derived likeness, multiple image references for the same people, physical performance, "
        "generated performance and repeated shot variants.\n\n"
        "**Do not queue everything casually.** Seedream, Flux Kontext, Kling, Runway and Seedance are hosted "
        "Partner Nodes and may use credits. Render individual lanes only when wanted.\n\n"
        "Read left → right, top → bottom. This graph contains no Pluribus-specific nodes or annotations."
    ),
    (
        61,
        (-2240, -420),
        (760, 200),
        "## 1 · One performer enters through four source assets\nA self-likeness LoRA, two ordinary photos, and a derived Understory character sheet condition the base frame. "
        "The graph itself does not say that all four assets resolve to one identity—or that the sheet was derived from the photos."
    ),
    (
        62,
        (-1840, 540),
        (760, 170),
        "## 2 · One performer, three source assets\nTyler's face, body and tattoo sheet are separate files. A file-key tracker may show three people; "
        "a visual tracker may miss the tattoo/body performance entirely."
    ),
    (
        63,
        (560, 380),
        (860, 170),
        "## 3 · Shot branching\nWide, tight and over-shoulder variants inherit the same source history even when a performer is cropped, blurred or only represented by an arm/tattoo."
    ),
    (
        64,
        (-1840, 1640),
        (950, 180),
        "## 4 · A second kind of performance\nThis lane starts from recorded motion. Runway changes wardrobe and scene while retaining timing, gesture, expression and camera motion. "
        "Those are performance uses even if faces drift."
    ),
    (
        65,
        (1500, 900),
        (1100, 170),
        "## 5 · Final recombination\nThree generated shot references plus one transformed physical-performance video become a new clip with generated audio. "
        "The final pixels do not reveal which face, body, motion or voice contribution survived."
    ),
]


GROUPS = [
    ("1 · IDENTITY LORA + MULTI-PHOTO CONDITIONING", (-2300, -500, 3650, 1120), "#49351f"),
    ("2 · MULTI-ASSET IDENTITY COMPOSITE", (-1900, 500, 2400, 700), "#4a2d1d"),
    ("3 · CAMERA / SHOT VARIATIONS", (520, 340, 940, 1360), "#2d3c50"),
    ("4A · GENERATED PERFORMANCE · I2V", (1460, 500, 1050, 440), "#274237"),
    ("4B · RECORDED PERFORMANCE · V2V", (-1900, 1600, 1540, 660), "#512b2b"),
    ("5 · FINAL MULTI-REFERENCE VIDEO", (1460, 860, 1210, 760), "#3c3154"),
]


def _widget_input(name: str, input_type: str) -> dict[str, object]:
    return {
        "localized_name": name,
        "name": name,
        "type": input_type,
        "widget": {"name": name},
        "link": None,
    }


def _dynamic_widget_inputs(node: NodeSpec) -> list[dict[str, object]]:
    if node.class_type == "ByteDanceSeedreamNodeV2":
        return [
            _widget_input("prompt", "STRING"),
            _widget_input("model", "COMFY_DYNAMICCOMBO_V3"),
            _widget_input("model.size_preset", "COMBO"),
            _widget_input("model.width", "INT"),
            _widget_input("model.height", "INT"),
            _widget_input("model.max_images", "INT"),
        ]
    if node.class_type == "ByteDance2ReferenceNode":
        return [
            _widget_input("model", "COMFY_DYNAMICCOMBO_V3"),
            _widget_input("model.prompt", "STRING"),
            _widget_input("model.resolution", "COMBO"),
            _widget_input("model.ratio", "COMBO"),
            _widget_input("model.duration", "INT"),
            _widget_input("model.generate_audio", "BOOLEAN"),
        ]
    return []


def build_graph(
    graph_nodes: list[NodeSpec],
    graph_notes: list[tuple[int, tuple[int, int], tuple[int, int], str]],
    graph_groups: list[tuple[str, tuple[int, int, int, int], str]],
    workflow_id: str,
    schemas: dict[str, dict[str, list[tuple[str, str, bool]] | list[tuple[str, str]]]] | None = None,
) -> tuple[dict, dict]:
    schemas = SCHEMAS if schemas is None else schemas
    node_by_id = {node.node_id: node for node in graph_nodes}
    if len(node_by_id) != len(graph_nodes):
        raise ValueError("duplicate node id")

    link_rows: list[list[object]] = []
    output_links: dict[tuple[int, int], list[int]] = {}
    link_by_target: dict[tuple[int, str], int] = {}

    for node in graph_nodes:
        schema_inputs = {name: kind for name, kind, _optional in schemas[node.class_type]["inputs"]}
        for input_name, (source_id, source_slot) in node.connections.items():
            if input_name not in schema_inputs:
                raise ValueError(f"{node.class_type} has no input {input_name}")
            if source_id not in node_by_id:
                raise ValueError(f"node {node.node_id} links missing source {source_id}")
            source_outputs = schemas[node_by_id[source_id].class_type]["outputs"]
            if source_slot >= len(source_outputs):
                raise ValueError(f"node {source_id} has no output slot {source_slot}")
            output_type = source_outputs[source_slot][1]
            input_type = schema_inputs[input_name]
            if output_type != input_type:
                raise ValueError(
                    f"type mismatch {source_id}:{source_slot} {output_type} -> "
                    f"{node.node_id}:{input_name} {input_type}"
                )
            link_id = len(link_rows) + 1
            input_slot = [row[0] for row in schemas[node.class_type]["inputs"]].index(input_name)
            link_rows.append([link_id, source_id, source_slot, node.node_id, input_slot, output_type])
            link_by_target[(node.node_id, input_name)] = link_id
            output_links.setdefault((source_id, source_slot), []).append(link_id)

    ui_nodes: list[dict] = []
    for order, node in enumerate(graph_nodes):
        inputs = _dynamic_widget_inputs(node)
        for input_name, input_type, optional in schemas[node.class_type]["inputs"]:
            link_id = link_by_target.get((node.node_id, input_name))
            entry: dict[str, object] = {"name": input_name, "type": input_type, "link": link_id}
            if optional:
                entry["shape"] = 7
            if node.class_type in {"ByteDanceSeedreamNodeV2", "ByteDance2ReferenceNode"}:
                entry["localized_name"] = input_name
                entry["label"] = input_name.rsplit(".", 1)[-1]
            inputs.append(entry)

        if node.class_type == "ByteDanceSeedreamNodeV2":
            inputs.extend(
                [
                    _widget_input("model.fail_on_partial", "BOOLEAN"),
                    _widget_input("seed", "INT"),
                    _widget_input("watermark", "BOOLEAN"),
                ]
            )
        elif node.class_type == "ByteDance2ReferenceNode":
            inputs.extend(
                [
                    _widget_input("model.auto_downscale", "BOOLEAN"),
                    _widget_input("model.auto_upscale", "BOOLEAN"),
                    _widget_input("seed", "INT"),
                    _widget_input("watermark", "BOOLEAN"),
                ]
            )

        outputs = [
            {
                "name": output_name,
                "type": output_type,
                "links": output_links.get((node.node_id, slot), []),
            }
            for slot, (output_name, output_type) in enumerate(schemas[node.class_type]["outputs"])
        ]

        properties: dict[str, object] = {"Node name for S&R": node.class_type}
        if node.class_type not in {"IPAdapterUnifiedLoader", "IPAdapterAdvanced", "ImageBatch"}:
            properties.update({"cnr_id": "comfy-core", "ver": "0.25.0"})

        ui_node: dict[str, object] = {
            "id": node.node_id,
            "type": node.class_type,
            "pos": list(node.pos),
            "size": list(node.size),
            "flags": {},
            "order": order,
            "mode": node.mode,
            "inputs": inputs,
            "outputs": outputs,
            "title": node.title,
            "properties": properties,
            "widgets_values": node.widgets_values,
        }
        if node.color:
            ui_node["color"] = node.color
        if node.bgcolor:
            ui_node["bgcolor"] = node.bgcolor
        ui_nodes.append(ui_node)

    note_order = len(ui_nodes)
    for offset, (node_id, pos, size, markdown) in enumerate(graph_notes):
        ui_nodes.append(
            {
                "id": node_id,
                "type": "MarkdownNote",
                "pos": list(pos),
                "size": list(size),
                "flags": {},
                "order": note_order + offset,
                "mode": 0,
                "inputs": [],
                "outputs": [],
                "properties": {},
                "widgets_values": [markdown],
            }
        )

    ui = {
        "id": workflow_id,
        "revision": 0,
        "last_node_id": max(node["id"] for node in ui_nodes),
        "last_link_id": len(link_rows),
        "nodes": ui_nodes,
        "links": link_rows,
        "groups": [
            {
                "id": index,
                "title": title,
                "bounding": list(bounds),
                "color": color,
                "font_size": 24,
                "flags": {},
            }
            for index, (title, bounds, color) in enumerate(graph_groups, start=1)
        ],
        "config": {},
        "extra": {"frontendVersion": "1.45.15"},
        "version": 0.4,
    }

    api: dict[str, dict[str, object]] = {}
    for node in graph_nodes:
        inputs = dict(node.api_widgets)
        for input_name, (source_id, source_slot) in node.connections.items():
            inputs[input_name] = [str(source_id), source_slot]
        api[str(node.node_id)] = {
            "class_type": node.class_type,
            "inputs": inputs,
            "_meta": {"title": node.title},
        }

    return ui, api


def build() -> tuple[dict, dict]:
    return build_graph(NODES, NOTES, GROUPS, "advanced-creative-pre-pluribus-v1")


def main() -> None:
    ui, api = build()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    ui_path = FIXTURES / "rights_stress_test_workflow.json"
    api_path = FIXTURES / "rights_stress_test_workflow_api.json"
    ui_path.write_text(json.dumps(ui, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    api_path.write_text(json.dumps(api, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"wrote {len(ui['nodes'])} UI nodes / {len(api)} API nodes / "
        f"{ui['last_link_id']} links"
    )


if __name__ == "__main__":
    main()
