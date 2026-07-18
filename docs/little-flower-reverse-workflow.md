# Little Flower reverse-engineered pre-Pluribus workflow

This fixture works backward from the finished `Little Flower V04 Final Delivery
0213.mp4` cut into a plausible advanced ComfyUI production graph. It is a
reconstruction for testing Pluribus, not a claim that the film was originally
made with AI or that these were its historical production files.

The user states that they executive-produced and own the short film. That
statement is recorded as test context in the local reconstruction manifest; it
is not converted into source-level clearance metadata inside the ComfyUI graph.

## Source analysis

- Delivery: 1920 by 1080 H.264, 23.976 fps, stereo AAC
- Runtime: 940.314 seconds (15:40.314)
- Automatic scene threshold: 0.20
- Candidate shots: 147
- Dark-scene correction: manual opening timings plus a lower-threshold pass on
  the second nightmare
- Content sequences: 8, followed by credits

Generated local analysis lives under the ignored directory:

```text
outputs/little-flower-reconstruction/
```

It includes a CSV and JSON shot manifest, individual midpoint keyframes,
complete storyboard sheets, identity-evidence sheets, location and prop bibles,
scene motion proxies, and temp audio segments. Film frames, face crops, and
audio are deliberately not tracked in the public plugin source.

## Identity-aware cast evidence

The rebuild no longer uses timestamp-only, full-frame contact sheets. YuNet
detects faces in curated frames from the actual scenes and SFace compares those
occurrences within this film. A clear curated frame seeds each principal role;
other crops must clear both the local match threshold and a margin from the
other principal anchors. Every tile is labelled with its scene, timecode,
detection confidence, and anchor-similarity score.

The resulting principal evidence inventory is:

| Role label | Credited performer label | Evidence |
|---|---|---|
| Layla | Nisreen Salem | face-cropped occurrences from at least four scenes |
| Dalia | Newsha Sadri | face-cropped occurrences from both Dalia sequences |
| Amo Hassan | Salim Kassam | face-cropped occurrences from market and party |
| Mama | Sawsan Mustafa | face-cropped occurrences from both Mama sequences |
| Nightmare Shadow | Anthony Egwu | labelled full-body/silhouette evidence; face model not used |

These are **suggested role mappings**, not open-world recognition. The model
does not discover a legal name. The role and performer labels come from the
film/credit reconstruction, every mapping remains marked `needs producer
confirmation`, no face embedding is persisted, and no identity suggestion is
converted into consent or clearance.

SC08 is handled separately. After Layla and Amo Hassan occurrences are
excluded, remaining faces are clustered into anonymous assets such as `Party
visual candidate 01`. Every visual candidate gets its own evidence sheet. The
fifteen credited extra names remain in the manifest as a separate credit list;
the builder intentionally does not guess which name belongs to which face.

## Sequence reconstruction

| Scene | Range | Shots | Principal roles |
|---|---:|---:|---|
| SC01 Nightmare and wake-up | 00:00-00:36 | 16 | Layla, Nightmare Shadow |
| SC02 Morning with Mama | 00:36-02:56.843 | 19 | Layla, Mama |
| SC03 Dream book and Dalia | 02:56.843-05:47.681 | 23 | Layla, Dalia |
| SC04 Market with Amo Hassan | 05:47.681-06:53.955 | 12 | Layla, Amo Hassan |
| SC05 Second nightmare | 06:53.955-07:43.838 | 17 | Layla, Nightmare Shadow |
| SC06 Neighborhood walk | 07:43.838-10:40.890 | 11 | Layla, Dalia |
| SC07 Mama's counsel | 10:40.890-13:00.863 | 11 | Layla, Mama |
| SC08 Garden party and fight | 13:00.863-15:08.366 | 37 | Layla, Amo Hassan, featured extras |

## Why the graph resembles a Higgsfield-style workflow

The reconstruction follows the current image-first previsualization pattern in
Higgsfield's official product material:

- [Canvas](https://higgsfield.ai/canvas-intro) puts prompts, references and
  generations on a reusable node board and accepts persistent character assets.
- [Popcorn's storyboard workflow](https://higgsfield.ai/blog/The-AI-Storyboard-Generator-That-Feels-Like-Directing)
  accepts up to four character, location, prop or style references and develops
  connected frames in manual or automatic modes.
- [Shots](https://higgsfield.ai/blog/shots-next-gen-storyboard-generator)
  turns one approved image into a multi-angle reference grid.
- [Cinema Studio motion guidance](https://higgsfield.ai/blog/realistic-ai-human-movement)
  treats cast, locations, physical beat, camera direction, lighting and start
  frame as separate controls.

The ComfyUI graph translates that pattern into nodes already available in the
local install:

1. Four principal face-evidence sheets, one full-body Nightmare Shadow sheet,
   anonymous per-candidate party sheets, and location/prop bibles form the
   reference library.
2. Anonymous party candidates are combined in groups of four, then into an
   SC08 crowd-continuity master. Every candidate remains a distinct graph
   source and none receives a credited name.
3. Each sequence loads its complete reconstructed storyboard, motion proxy and
   temp audio.
4. Seedream combines up to four references into an approved scene master.
5. Flux Kontext derives a wide master, medium performance shot and close
   insert/over-shoulder.
6. Kling animates all three storyboard branches with explicit camera and
   physical-performance direction.
7. Runway Aleph transforms recorded blocking, gestures, expressions and camera
   motion from the scene proxy.
8. Seedance recombines three still references, the transformed performance and
   temp audio into a scene output.
9. The eight scene templates stand in for batched per-shot generation against
   the 147-shot manifest; editorial conform, subtitles, mix and credits occur
   outside the generation graph.

The graph contains zero `PluribusSourceMarker` nodes. Its exact node/link totals
vary with the number of anonymous party candidates and are printed by the
generator rather than hard-coded in this document.

## Why this stresses performance-rights tracking

- One performer reappears through a character sheet, many storyboard frames,
  multiple wardrobe states, a motion proxy, dialogue audio and downstream
  generated branches.
- Amo Hassan appears in both a quiet market scene and the physical bounce-house
  fight; visual context alone can make those look like different people.
- Mama's beige and blue hijab looks create a wardrobe-driven identity split.
- The Nightmare Shadow is mostly silhouette, body movement and prop interaction,
  not a clear face.
- SC08 can contain many visually distinct candidates. Each candidate is a
  separate source, while the credited-name list remains deliberately unmapped.
- Temp audio contains dialogue, voice identity, music and ambience, but current
  graph scanners may focus only on visual inputs.
- Video-to-video preserves timing, gesture, blocking and camera performance even
  if appearance changes.
- Cropped close shots can inherit a performer's source history when that person
  is soft, offscreen or represented only by a hand or eyeline.
- Role labels exist inside evidence-sheet pixels and graph titles rather than
  as confirmed roster identifiers.

This is the baseline Pluribus should encounter. It includes no declared
clearance state, contract terms, canonical person IDs or manual source markers.

### Expected scan inventory

Let `K` be the number of anonymous SC08 visual candidates retained by the local
clusterer (maximum 16). The validated ComfyUI input inventory is `31 + K`:

- 4 principal face-evidence sheets
- 1 Nightmare Shadow full-body sheet
- `K` anonymous party-candidate sheets
- 2 visual bibles
- 8 storyboards
- 8 motion proxies
- 8 temp-audio files

The exact inventory and SHA-256 of every asset are recorded in
`reconstruction_manifest.json`; `--package-only` refuses missing, changed,
duplicate, unsafe, stale-builder, stale-source, or flattening-collision inputs.
The builder fingerprint covers the builder plus the imported identity analyzer,
identity-model contract, and pinned-model installer implementations, so changing
any evidence-producing code invalidates an older package manifest.
The graph scanner should preserve each input as an independent source, discover
the eight audio sources, and propagate only source-specific lineage through
Seedream, Flux, Kling, Runway, and Seedance. Identity analysis then groups face
occurrences into review candidates without silently converting them into people
or rights records.

## Generate and load

Install the plugin's pinned YuNet/SFace model bundle explicitly, then pass that
private model directory to the builder. The builder never downloads models on
its own. For a typical local ComfyUI install, first use Pluribus's **Install
local identity models** action, then pass the plugin's configured private model
directory:

```bash
python3 comfyui-pluribus/tools/build_little_flower_reverse_assets.py \
  --model-dir /absolute/path/to/pluribus-private-data/identity/models
```

`PLURIBUS_IDENTITY_MODEL_DIR` may be used instead of the flag. The manifest
records the analyzer ID, model version, pinned filenames/checksums, thresholds,
clustering method, and the fact that embeddings were not persisted.

If the reconstruction assets already exist, validate and stage the dynamic
graph input inventory without rerunning film analysis or `ffmpeg`:

```bash
python3 comfyui-pluribus/tools/build_little_flower_reverse_assets.py --package-only
```

Generate the UI and API/scanner fixtures from the local reconstruction manifest:

```bash
python3 comfyui-pluribus/tools/gen_little_flower_reverse_workflow.py \
  --manifest outputs/little-flower-reconstruction/reconstruction_manifest.json
```

The repository also tracks
`comfyui-pluribus/fixtures/little_flower_reverse_input_manifest.json`, a
sanitized graph-only manifest with relative loader filenames, scene structure,
and prompts. It contains no source-film path or hash, extracted media, asset
hashes, face embeddings, ownership notes, or local identity-model metadata. A
clean checkout can deterministically validate the committed fixtures without
the private film or generated assets:

```bash
python3 comfyui-pluribus/tools/gen_little_flower_reverse_workflow.py --check
```

Run the same command without `--check` to regenerate the two committed workflow
JSON files from that sanitized manifest.

The installed local paths are:

```text
ComfyUI/input/little_flower_reverse__*
ComfyUI/user/default/workflows/pluribus/little_flower_reverse_workflow.json
```

The input files must be copied directly into `ComfyUI/input`, not a nested
folder. Core `LoadImage`, `LoadVideo`, and `LoadAudio` widgets enumerate only
top-level input files in the current local ComfyUI build.

Open the workflow in ComfyUI and run Pluribus against the current graph. The
Seedream, Flux, Kling, Runway and Seedance nodes are hosted Partner Nodes and can
consume credits; the fixture has been constructed and validated without queuing
those generations.

## Important limitations

- Storyboards, character sheets, motion proxies and temp audio were derived
  from the finished delivery, so they cannot prove the film's actual chain of
  creation.
- Shot detection is a production aid, not an editorial EDL. Rapid flashes,
  dissolves and dark cuts required manual correction.
- Actor credits identify people but do not establish the scope, territory,
  duration or synthetic-media permissions in their agreements.
- Face similarity is probabilistic. Principal role labels and anonymous party
  clusters must be visually confirmed by the producer; neither is legal proof
  of identity, consent, or clearance.
- Nightmare Shadow evidence is based on body/silhouette and scene context, not
  a face embedding.
- The fixture represents a credible advanced graph and batched shot templates;
  it does not spend credits regenerating the complete 15-minute film.
