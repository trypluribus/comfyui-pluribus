# Local identity analysis

Pluribus can optionally turn graph sources into a producer-reviewable set of
likely people. The analysis is project scoped: it clusters appearances within
the current workflow and never performs an open-world identity search.

## Identity demo install (unreleased `main`)

The people-first identity demo is not included in the supported
`v0.4.0-rc.2` tag. For a controlled demo, start from a clean `main` clone and
install the optional dependencies into the same Python environment that starts
ComfyUI:

```bash
cd /path/to/ComfyUI/custom_nodes
git clone --branch main --depth 1 \
  https://github.com/trypluribus/comfyui-pluribus
/path/to/ComfyUI/.venv/bin/python -m pip install \
  -r /path/to/ComfyUI/custom_nodes/comfyui-pluribus/requirements-identity.txt
```

Restart ComfyUI, open **Pluribus**, and choose **Install local models**. Then
load the demo workflow and video and choose **Find people**. Keep this install
separate from a supported-release rehearsal; a `main` commit is not a versioned
release.

## Privacy and decision boundary

- Full source images, video, and sampled frames stay on the ComfyUI machine.
- After a producer confirms appearances and promotes the person into a
  connected project, the plugin may upload up to five best-ranked, re-encoded
  square JPEG portrait derivatives for that project-person record. Each is no
  larger than 1 MB and can be replaced or retired when the confirmed selection
  changes.
- Face vectors are held only in memory while one job is clustering. They are
  never written to disk, returned by an endpoint, or sent to Pluribus.
- Persisted evidence contains source hashes, frame/time references, real crops,
  deterministic evidence sheets, and the producer's links.
- Full source media, filenames and paths, sampled frames, occurrence and
  candidate IDs, crop artifact names, embeddings, and quality measurements are
  never part of a portrait upload. It contains only sanitized portrait bytes
  and opaque idempotency metadata.
- Occurrence and candidate IDs include the analyzed source-content hash. If a
  file changes in place, the next analysis produces new IDs and prior producer
  decisions do not silently attach to the changed pixels.
- A visual grouping is a suggestion, not proof of identity, consent, rights,
  eligibility, or clearance.
- Deleting the last job referencing a cache removes its crops and sheets.
  Workflow-level confirmations have a separate explicit delete endpoint so a
  routine rescan does not erase producer work.

## Optional dependencies

The plugin imports OpenCV and NumPy only when local identity analysis runs.
The default backend is OpenCV YuNet plus SFace. Model weights are not bundled
and are never downloaded on scan.

`GET /pluribus/identity/capabilities` reports Python dependency and model
status. The returned install action points to
`POST /pluribus/identity/models/install`. Installation requires
`{"modelId":"opencv-yunet-sface-v1","confirm":true}` and downloads two
immutable OpenCV Zoo artifacts over HTTPS. Both are size bounded and checked
against pinned SHA-256 digests before an atomic private install.

## Job API

Start an analysis:

```http
POST /pluribus/identity/analyze
Content-Type: application/json

{
  "workflowRef": "stable-local-workflow-ref",
  "workflowName": "Little Flower",
  "workflowFingerprint": "...",
  "sources": [
    {
      "sourceRef": "1f97e2575c9adcdad7f043aa14db5b14b13b9aec10daff6864a701d239f4c570",
      "sourceKey": "characters/layla.png [input]",
      "displayLabel": "Layla character sheet"
    }
  ]
}
```

`sourceRef` is the workflow-scoped lowercase SHA-256 reference minted by the
local source-binding route. It is opaque to the UI. The server verifies the
reference against the current workflow plus its exact local source slot and
kind; checking only its 64-hex shape is not sufficient.
The analysis request intentionally sends workflow metadata plus resolved
sources, not a duplicate copy of the full ComfyUI graph.

Browser access to analysis jobs, evidence, and producer decisions is restricted
to the ComfyUI origin. State-changing requests require JSON content types, and
identity responses use `Cache-Control: no-store`. The default local resource
envelope allows four pending jobs, samples at most 900 video frames across one
job, keeps at most 256 MiB of crop bytes in memory, and bounds each crop to 512
pixels per side. Reported coverage and issues disclose when a decoder or one of
these limits prevents complete analysis.

The initial response contains `jobId`, `state`, `cacheHit`, and `progress`.
Poll `GET /pluribus/identity/jobs/{jobId}`. Progress phases are
`queued`, `reading_media`, `grouping_people`, `building_evidence`, and
`complete`.

A completed job returns:

- `coverage`: total, analyzed, skipped, and image/video/audio inventory counts;
- `manualReviewRequired` and `manualReviewSources`: an authoritative incomplete-
  coverage flag plus source-scoped `{ sourceRef, sourceHash, issueCodes }`
  records. `sourceHash` is `null` only when the resolver could not safely open
  and hash the bytes;
- `candidates`: occurrence-count-ranked likely people with a heuristic grouping
  band, review state, and evidence-sheet URL;
- `occurrences`: exact source, frame, timecode, bounding box, and crop URL;
- `issues`: missing-model, unsupported-source, ambiguity, and coverage details;
- `evidence.manifestUrl`: the local audit manifest.

The `confidence` field is a robust similarity heuristic, not a probability.
UI should prefer `groupingBand` and `groupingLabel`.
Each candidate evidence sheet contains at most sixteen deterministic review
crops; every occurrence remains individually available through its `cropUrl`.

## Conservative grouping

The deterministic clusterer uses normalized-centroid SFace similarity with a
0.38 default threshold and a top-two ambiguity margin. Faces in the same real
frame are a hard cannot-link. Character-sheet, storyboard, contact-sheet,
lookbook, and grid labels activate montage handling: spatial tiles become
pseudo-frames, with the row count inferred from canvas aspect ratio. This lets
the same performer repeat across panels while keeping co-performers in one
panel separate.

Candidates are sorted by occurrence count, then stable candidate ID. Generic
scene/storyboard names never become person suggestions. Explicit labels such as
`layla_character_sheet.png` or `Nisreen_Salem_headshot.jpg` may supply a cautious
working name and role; a multi-person sheet supplies its name only to a uniquely
dominant repeated candidate. `suggestionSource` records `source_label`.

## Producer links and deletion

`PUT /pluribus/identity/jobs/{jobId}/links` saves confirmed, rejected, or unsure
links. A confirmed link requires an opaque `personId` and an `occurrenceIds`
list, which can select only some appearances in a mixed cluster. Every ID must
have been minted for that candidate and job.

The People review UI exposes the same occurrence-level contract. A producer can
expand every source group, deselect individual frames, and leave the unselected
appearances unresolved. They can then review the remaining appearances as a
different person, assign them to a saved workflow or project person, or leave
them in the review queue.

Saved-person assignment is always explicit. The producer chooses an existing
person and confirms that assignment; the plugin never merges identities
automatically. When a local draft already maps to a canonical project person,
the picker presents one saved identity and preserves that canonical mapping
instead of creating another person record.

Link writes use an optimistic revision so two open panels cannot silently
overwrite one another:

```http
PUT /pluribus/identity/jobs/{jobId}/links
Content-Type: application/json

{
  "baseRevision": 3,
  "links": [
    {
      "candidateId": "candidate-id",
      "personId": "person-id",
      "state": "confirmed",
      "occurrenceIds": ["occurrence-id"]
    }
  ]
}
```

Read and write responses contain `{ "jobId", "links", "revision" }`. A client
must reload before retrying an HTTP 409 revision conflict. Clearing all links is
also revision guarded:

```http
DELETE /pluribus/identity/jobs/{jobId}/links
Content-Type: application/json

{ "baseRevision": 4 }
```

Links are keyed by the stable `workflowRef` and candidate ID, so cache-hit
rescans preserve decisions only while the candidate and selected occurrences
still exist. They never create clearance.

- `GET /pluribus/identity/jobs/{jobId}/links` reads applicable links and the
  current revision.
- `DELETE /pluribus/identity/jobs/{jobId}/links` clears workflow links only when
  `baseRevision` still matches; a stale clear returns HTTP 409.
- `POST /pluribus/identity/jobs/{jobId}/cancel` requests cooperative cancel.
- `DELETE /pluribus/identity/jobs/{jobId}` cancels and deletes job evidence.

Evidence bytes are served only through minted artifact IDs under
`/pluribus/identity/jobs/{jobId}/evidence/{artifactId}`. Raw filesystem paths
and face vectors never appear in the contract.
