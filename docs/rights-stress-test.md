# Advanced creative graph before Pluribus

Load `fixtures/rights_stress_test_workflow.json` in ComfyUI to test Pluribus
against a complex graph that contains no Pluribus-specific nodes, source
markers, roster states, or rights annotations.

## Real local inputs

The workflow only references files present in the local ComfyUI install:

- `sd_xl_base_1.0.safetensors`
- `alhassan_lrx26_sdxl_webcam_v0.safetensors`
- `IMG_0003.jpeg`
- `IMG_2505.jpeg`
- `hero-cast-final_00002_.png` (the derived Understory character sheet)
- `TYLER_FACE_MASTER_A.jpg`
- `TYLER_BODY_GYM_A.jpg`
- `TYLER_TATTOO_CONDITIONING_V02_3840x2160.png`
- `little_flower/lf_fight_s02.mp4`

The LoRA is a valid SDXL self-likeness LoRA with trigger token
`alhassan_lrx26`. The two `IMG_*.jpeg` files are ordinary references for that
same person, and `hero-cast-final_00002_.png` is the character sheet previously
generated from those references in the Understory graph. All three images are
normalized to 1024 by 1024 before `ImageBatch`, so the local SDXL/IPAdapter lane
is executable instead of being canvas-only.

The Tyler files are three separate references for the same second performer:
face, body/pose, and tattoos. Their relationship is implied by how the creative
uses them, not by metadata in the graph.

## What the graph does

1. Loads SDXL and the self-likeness LoRA.
2. Uses two photos plus the derived Understory character sheet of that same
   person as an IPAdapter identity batch.
3. Generates a living-room campaign keyframe locally.
4. Uses Seedream with the keyframe plus Tyler's face/body/tattoo references to
   create a two-person cast composite.
5. Branches that composite into wide, reaction, and over-shoulder shots with
   Flux Kontext.
6. Animates the wide shot with Kling image-to-video.
7. Separately transforms a recorded performance with Runway video-to-video,
   retaining motion, timing, expression, and camera movement.
8. Recombines the three still branches and the transformed source performance
   in a Seedance multi-reference video node with generated reaction audio.

## Why rights tracking is difficult

- One person enters through a LoRA, two photos, and a derived character sheet;
  file-level tracking can mistake those for four identities and the current
  graph does not retain the sheet's original derivation chain.
- Another person enters through face, body, and tattoo files; a face-only
  tracker can miss body and identifying-feature use.
- Cropped shot variants inherit source history even when one performer is
  barely visible.
- Video-to-video can preserve a physical performance after wardrobe, identity,
  or scene appearance changes.
- A single source video may contain multiple people while exposing only one
  file path to the graph.
- The final video combines generated likeness, edited references, physical
  motion, and generated audio.

Pluribus should discover and explain these issues from graph traversal. The
workflow provides no manual source hints.

## Running it

Start ComfyUI from a normal terminal and load the UI-format JSON. The first
lane through `Checkpoint · blended cast keyframe` runs locally. Seedream, Flux
Kontext, Kling, Runway, and Seedance are hosted Partner Nodes and can consume
credits, so queue those branches only when intended.

Regenerate the UI and scanner/API fixtures with:

```bash
python3 comfyui-pluribus/tools/gen_rights_stress_test_workflow.py
```
