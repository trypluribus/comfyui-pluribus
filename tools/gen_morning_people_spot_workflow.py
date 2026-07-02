"""Generate the 'Morning People' demo workflow (UI + API formats).

Node sockets/types are pulled from the live ComfyUI /object_info so the
fixture matches the local install exactly. Emits:
  - fixtures/morning_people_spot_workflow.json      (UI format, canvas demo)
  - fixtures/morning_people_spot_workflow_api.json  (API format, tests + /pluribus/scan)
"""

import json
import os
import urllib.request

BASE = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188")
FIXTURES = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fixtures"))

# ---------------------------------------------------------------- object_info

def object_info(class_type):
    with urllib.request.urlopen(f"{BASE}/object_info/{class_type}", timeout=10) as r:
        data = json.load(r)
    if class_type not in data:
        raise SystemExit(f"node type missing from install: {class_type}")
    return data[class_type]

INFO = {}

def widget_names(class_type):
    """Ordered non-connection input names (required then optional)."""
    info = INFO[class_type]
    names = []
    for section in ("required", "optional"):
        for name, spec in info["input"].get(section, {}).items():
            kind = spec[0]
            if isinstance(kind, list) or kind in (
                "STRING", "INT", "FLOAT", "BOOLEAN", "COMBO",
            ):
                names.append(name)
    return names

def connection_inputs(class_type):
    info = INFO[class_type]
    conns = []
    for section in ("required", "optional"):
        for name, spec in info["input"].get(section, {}).items():
            kind = spec[0]
            if not isinstance(kind, list) and kind not in (
                "STRING", "INT", "FLOAT", "BOOLEAN", "COMBO",
            ):
                conns.append((name, kind, section == "optional"))
    return conns

def output_defs(class_type):
    info = INFO[class_type]
    return list(zip(info.get("output_name", []), info.get("output", [])))

# ---------------------------------------------------------------- graph spec

PROMPT_NEG_STILL = (
    "blurry, deformed hands, extra fingers, warped face, watermark, text, "
    "logo distortion, oversaturated, plastic skin, low quality"
)

# (id, class_type, title, pos, size, widgets{name: value}, links{input: (from_id, out_slot)})
NODES = [
    # ---- SHOT 01 · Hero: Sarah Chen (cleared identity LoRA) -> Kling I2V
    (1, "CheckpointLoaderSimple", "SDXL base (shared)", (-1180, -60), (340, 100),
     {"ckpt_name": "sd_xl_base_1.0.safetensors"}, {}),
    (2, "LoraLoader", "Identity LoRA · Sarah Chen (cleared)", (-1180, 120), (340, 130),
     {"lora_name": "Woman877.v2.safetensors", "strength_model": 0.8, "strength_clip": 0.8},
     {"model": (1, 0), "clip": (1, 1)}),
    (3, "CLIPTextEncode", "Shot 01 · positive", (-760, -60), (420, 170),
     {"text": (
         "woman877, a woman in her mid 20s, sunlit modern kitchen at golden hour, "
         "softly smiling while pulling an espresso shot on a premium stainless "
         "espresso machine, steam rising, white linen shirt, warm "
         "amber tones, commercial product photography, 85mm, f/1.8, shallow depth "
         "of field, crisp product detail"
     )},
     {"clip": (2, 1)}),
    (4, "CLIPTextEncode", "Shot 01 · negative", (-760, 160), (420, 140),
     {"text": PROMPT_NEG_STILL}, {"clip": (2, 1)}),
    (5, "EmptyLatentImage", "Shot 01 · 1216x832", (-760, 350), (300, 110),
     {"width": 1216, "height": 832, "batch_size": 1}, {}),
    (6, "KSampler", "Shot 01 · sample", (-280, -60), (300, 270),
     {"seed": 271828, "steps": 28, "cfg": 6.5,
      "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0},
     {"model": (2, 0), "positive": (3, 0), "negative": (4, 0), "latent_image": (5, 0)}),
    (7, "VAEDecode", "Shot 01 · decode", (60, -60), (200, 60),
     {}, {"samples": (6, 0), "vae": (1, 2)}),
    (8, "SaveImage", "Shot 01 · hero still", (320, -60), (340, 340),
     {"filename_prefix": "morning_people/01_hero_still"}, {"images": (7, 0)}),
    (9, "KlingImage2VideoNode", "Shot 01 · animate (Kling 2.5 turbo)", (720, -60), (420, 340),
     {"prompt": (
         "Slow cinematic dolly-in: the woman finishes pulling the espresso shot, "
         "steam curls through golden morning light, she lifts the cup, takes a "
         "slow sip and smiles toward the window. Subtle handheld warmth, "
         "commercial grade, crisp machine reflections."
     ),
      "negative_prompt": "jump cuts, warped face, distorted hands, extra limbs, flicker, text, watermark",
      "model_name": "kling-v2-5-turbo", "cfg_scale": 0.8, "mode": "pro",
      "aspect_ratio": "16:9", "duration": "5"},
     {"start_frame": (7, 0)}),
    (10, "SaveVideo", "Shot 01 · hero 5s clip", (1200, -60), (500, 400),
     {"filename_prefix": "video/morning_people/01_hero", "format": "auto", "codec": "auto"},
     {"video": (9, 0)}),

    # ---- SHOT 02 · Lifestyle variant: Marcus Reed reference (pending)
    (11, "LoadImage", "Reference · marcus_ref.png", (-1180, 760), (300, 340),
     {"image": "marcus_ref.png"}, {}),
    (12, "GeminiImage2Node", "Shot 02 · identity composite (Nano Banana 2)", (-760, 760), (440, 400),
     {"prompt": (
         "Place the man from the reference photo in the same sunlit modern "
         "kitchen, leaning on the counter beside a premium stainless espresso "
         "machine, holding a double-walled glass espresso cup, relaxed morning "
         "energy, charcoal henley, golden-hour window light, photoreal commercial "
         "photography. Preserve the man's facial identity exactly."
     ),
      "model": "Nano Banana 2 (Gemini 3.1 Flash Image)", "seed": 20260702,
      "aspect_ratio": "16:9", "resolution": "2K", "response_modalities": "IMAGE",
      "system_prompt": ""},
     {"images": (11, 0)}),
    (13, "SaveImage", "Shot 02 · marcus variant", (-240, 760), (340, 340),
     {"filename_prefix": "morning_people/02_marcus_variant"}, {"images": (12, 0)}),

    # ---- SHOT 03 · Alt casting: Elena Vasquez reference (restricted)
    (14, "LoadImage", "Reference · elena_ref.png", (-1180, 1320), (300, 340),
     {"image": "elena_ref.png"}, {}),
    (15, "FluxKontextProImageNode", "Shot 03 · identity edit (Flux Kontext Pro)", (-760, 1320), (440, 360),
     {"prompt": (
         "The woman from the input image now stands in a bright modern kitchen "
         "pouring a cappuccino from a premium espresso machine, morning window "
         "light, editorial commercial look, cream knit sweater. Preserve her "
         "facial identity and hair."
     ),
      "aspect_ratio": "16:9", "guidance": 3.0, "steps": 50,
      "seed": 20260702, "prompt_upsampling": False},
     {"input_image": (14, 0)}),
    (16, "SaveImage", "Shot 03 · elena alt casting", (-240, 1320), (340, 340),
     {"filename_prefix": "morning_people/03_elena_alt"}, {"images": (15, 0)}),

    # ---- SHOT 04 · Extras look-dev: community LoRA (unidentified)
    (17, "LoraLoader", "Community LoRA · unknown source", (-1180, 1880), (340, 130),
     {"lora_name": "coffeehouse_regulars_SDXL_v3.safetensors",
      "strength_model": 0.85, "strength_clip": 0.85},
     {"model": (1, 0), "clip": (1, 1)}),
    (18, "CLIPTextEncode", "Shot 04 · positive", (-760, 1880), (420, 170),
     {"text": (
         "coffeehouse regulars, three people laughing around a kitchen island "
         "while espresso brews, premium espresso machine in frame, candid "
         "morning lifestyle, warm light, 35mm documentary commercial style"
     )},
     {"clip": (17, 1)}),
    (19, "CLIPTextEncode", "Shot 04 · negative", (-760, 2100), (420, 140),
     {"text": PROMPT_NEG_STILL}, {"clip": (17, 1)}),
    (20, "EmptyLatentImage", "Shot 04 · 1216x832", (-760, 2290), (300, 110),
     {"width": 1216, "height": 832, "batch_size": 1}, {}),
    (21, "KSampler", "Shot 04 · sample", (-280, 1880), (300, 270),
     {"seed": 314159, "steps": 28, "cfg": 6.5,
      "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0},
     {"model": (17, 0), "positive": (18, 0), "negative": (19, 0), "latent_image": (20, 0)}),
    (22, "VAEDecode", "Shot 04 · decode", (60, 1880), (200, 60),
     {}, {"samples": (21, 0), "vae": (1, 2)}),
    (23, "SaveImage", "Shot 04 · extras look-dev", (320, 1880), (340, 340),
     {"filename_prefix": "morning_people/04_extras_lookdev"}, {"images": (22, 0)}),

    # ---- SHOT 05 · Synthetic B-roll: prompt-only person (no real source)
    (24, "CLIPTextEncode", "Shot 05 · positive (synthetic barista)", (-760, 2620), (420, 170),
     {"text": (
         "extreme close-up, a barista's hands tamping fresh espresso grounds, "
         "portafilter locking into an espresso machine, crema pouring into a clear "
         "glass, macro detail, steam, warm morning backlight, high-end tabletop "
         "commercial photography"
     )},
     {"clip": (1, 1)}),
    (25, "CLIPTextEncode", "Shot 05 · negative", (-760, 2840), (420, 140),
     {"text": PROMPT_NEG_STILL}, {"clip": (1, 1)}),
    (26, "EmptyLatentImage", "Shot 05 · 1216x832", (-760, 3030), (300, 110),
     {"width": 1216, "height": 832, "batch_size": 1}, {}),
    (27, "KSampler", "Shot 05 · sample", (-280, 2620), (300, 270),
     {"seed": 161803, "steps": 28, "cfg": 6.5,
      "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 1.0},
     {"model": (1, 0), "positive": (24, 0), "negative": (25, 0), "latent_image": (26, 0)}),
    (28, "VAEDecode", "Shot 05 · decode", (60, 2620), (200, 60),
     {}, {"samples": (27, 0), "vae": (1, 2)}),
    (29, "SaveImage", "Shot 05 · synthetic B-roll", (320, 2620), (340, 340),
     {"filename_prefix": "morning_people/05_broll_synthetic"}, {"images": (28, 0)}),
]

NOTES = [
    (30, (-2080, -60), (400, 620), "# “Morning People” — 15s cutdown\n\n"
     "Talent-driven product spot: identity-consistent stills per shot, hero shot "
     "animated to video. Reads left → right; one lane per shot.\n\n"
     "**To render for real**\n"
     "1. Shot 01's identity LoRA (`Woman877.v2.safetensors` — synthetic "
     "character, commercial image use licensed) is real and renders locally. "
     "`coffeehouse_regulars_SDXL_v3.safetensors` stays a placeholder on "
     "purpose: it is the demo's unknown-source beat.\n"
     "2. Log into your Comfy account (Settings → User) and add credits — "
     "Nano Banana 2, Flux Kontext Pro and Kling are hosted Partner Nodes billed "
     "per run (≈$0.35 for the 5s Kling clip).\n"
     "3. Queue. SDXL lanes run locally on this Mac; API lanes need network.\n\n"
     "Pluribus: run a rights scan before rendering — this graph deliberately "
     "contains uncleared and unknown people."),
    (31, (-1560, -60), (340, 200), "## Shot 01 · Hero\nSarah Chen — **cleared** identity LoRA "
     "(small-appliance scope, through Dec 2026). Still → Kling 2.5-turbo image-to-video, 5s."),
    (32, (-1560, 760), (340, 200), "## Shot 02 · Lifestyle variant\nMarcus Reed reference photo → "
     "Nano Banana 2 identity composite. Marcus is **pending clearance** — expect a review flag."),
    (33, (-1560, 1320), (340, 200), "## Shot 03 · Alt casting\nElena Vasquez reference → Flux Kontext "
     "Pro edit. Elena is **restricted** (competing coffee-brand exclusivity) — left in from casting."),
    (34, (-1560, 1880), (340, 200), "## Shot 04 · Extras look-dev\nCommunity LoRA pulled from a model "
     "hub — **unidentified** real-person sources. Classic rights risk."),
    (35, (-1560, 2620), (340, 200), "## Shot 05 · Synthetic B-roll\nPrompt-only person (hands of a "
     "barista) — no real-person source in the chain, **synthetic, unverified**."),
]

GROUPS = [
    ("SHOT 01 · HERO — Sarah Chen (cleared) → I2V", (-1600, -160, 3340, 700), "#3f2d1d"),
    ("SHOT 02 · LIFESTYLE — Marcus Reed (pending)", (-1600, 660, 1740, 560), "#453a1e"),
    ("SHOT 03 · ALT CASTING — Elena Vasquez (restricted)", (-1600, 1220, 1740, 560), "#4a2320"),
    ("SHOT 04 · EXTRAS — unknown community LoRA", (-1600, 1780, 2300, 740), "#3a3a3a"),
    ("SHOT 05 · B-ROLL — synthetic person, prompt only", (-1600, 2520, 2300, 740), "#1e3a4a"),
]

# ------------------------------------------------------------------- emitters

def build():
    for _id, ct, *_ in NODES:
        if ct not in INFO:
            INFO[ct] = object_info(ct)

    seed_controlled = {"KSampler", "GeminiImage2Node", "FluxKontextProImageNode"}

    ui_nodes, links = [], []
    link_id = 0
    out_links = {}  # (from_id, slot) -> [link ids]
    node_by_id = {n[0]: n for n in NODES}

    # first pass: assign link ids
    link_rows = []
    for nid, ct, _title, _pos, _size, _widgets, conns in NODES:
        for input_name, (src, slot) in conns.items():
            link_id += 1
            src_ct = node_by_id[src][1]
            ltype = output_defs(src_ct)[slot][1]
            link_rows.append([link_id, src, slot, nid, input_name, ltype])
            out_links.setdefault((src, slot), []).append(link_id)

    link_by_target = {(row[3], row[4]): row for row in link_rows}

    order = 0
    for nid, ct, title, pos, size, widgets, conns in NODES:
        winfo = widget_names(ct)
        wvals = []
        for name in winfo:
            if name not in widgets:
                raise SystemExit(f"node {nid} {ct}: missing widget value for '{name}'")
            wvals.append(widgets[name])
            if name == "seed" and ct in seed_controlled:
                wvals.append("fixed")
        if ct == "LoadImage":
            wvals.append("image")  # upload button pseudo-widget

        inputs = []
        for cname, ckind, optional in connection_inputs(ct):
            row = link_by_target.get((nid, cname))
            entry = {"name": cname, "type": ckind, "link": row[0] if row else None}
            if optional:
                entry["shape"] = 7
            inputs.append(entry)

        outputs = [
            {"name": oname, "type": otype, "links": out_links.get((nid, i), [])}
            for i, (oname, otype) in enumerate(output_defs(ct))
        ]

        ui_nodes.append({
            "id": nid, "type": ct, "pos": list(pos), "size": list(size),
            "flags": {}, "order": order, "mode": 0,
            "inputs": inputs, "outputs": outputs,
            "title": title,
            "properties": {"cnr_id": "comfy-core", "ver": "0.25.0", "Node name for S&R": ct},
            "widgets_values": wvals,
        })
        order += 1

    for nid, pos, size, text in NOTES:
        ui_nodes.append({
            "id": nid, "type": "MarkdownNote", "pos": list(pos), "size": list(size),
            "flags": {}, "order": order, "mode": 0, "inputs": [], "outputs": [],
            "properties": {}, "widgets_values": [text],
        })
        order += 1

    ui = {
        "id": "morning-people-spot",
        "revision": 0,
        "last_node_id": max(n["id"] for n in ui_nodes),
        "last_link_id": link_id,
        "nodes": ui_nodes,
        "links": [[r[0], r[1], r[2], r[3],
                   [c[0] for c in connection_inputs(node_by_id[r[3]][1])].index(r[4]), r[5]]
                  for r in link_rows],
        "groups": [
            {"id": i + 1, "title": t, "bounding": list(b), "color": c,
             "font_size": 24, "flags": {}}
            for i, (t, b, c) in enumerate(GROUPS)
        ],
        "config": {},
        "extra": {"frontendVersion": "1.45.15"},
        "version": 0.4,
    }

    api = {}
    for nid, ct, title, _pos, _size, widgets, conns in NODES:
        inputs = dict(widgets)
        for cname, (src, slot) in conns.items():
            inputs[cname] = [str(src), slot]
        api[str(nid)] = {"class_type": ct, "inputs": inputs, "_meta": {"title": title}}

    return ui, api


ui, api = build()
with open(f"{FIXTURES}/morning_people_spot_workflow.json", "w") as f:
    json.dump(ui, f, indent=2, ensure_ascii=False)
    f.write("\n")
with open(f"{FIXTURES}/morning_people_spot_workflow_api.json", "w") as f:
    json.dump(api, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("wrote", len(ui["nodes"]), "UI nodes /", len(api), "API nodes /", ui["last_link_id"], "links")
