import { personLocalKey } from "./manifest.js";

const VIDEO_EXTENSIONS = /\.(?:mp4|mov|m4v|webm|avi|mkv)(?:\s*\[(?:input|output|temp)\])?$/i;
const IMAGE_EXTENSIONS = /\.(?:png|jpe?g|webp|gif|bmp|tiff?)(?:\s*\[(?:input|output|temp)\])?$/i;

function unique(values) {
  return [...new Set((values || []).filter(Boolean).map(String))];
}

function operationKey(operation) {
  return [
    operation?.node_id || operation?.nodeId || "",
    operation?.class_type || operation?.classType || "",
    operation?.source_role || operation?.sourceRole || "",
  ].join("|");
}

function occurrenceKey(occurrence) {
  return `${occurrence?.output_node_id || ""}|${occurrence?.source_node_id || ""}`;
}

export function sourceRefForPerson(person, sourceRefs = {}) {
  return sourceRefs?.[personLocalKey(person)] || person?.sourceRef || person?.source_ref || "";
}

export function sourceIdentity(person, sourceRefs = {}) {
  const sourceRef = sourceRefForPerson(person, sourceRefs);
  if (sourceRef) return `ref:${sourceRef}`;
  const kind = String(person?.source_kind || "unknown");
  const key = String(person?.source_key || "");
  if (key) return `${kind}|key:${key}`;
  return `${kind}|node:${person?.source_node_id || person?.output_node_id || ""}`;
}

// New scanners already return one row per source. This defensive collapse also
// keeps older ComfyUI installs from showing the same input once per output.
export function sourceRecordsForScan(persons = [], sourceRefs = {}) {
  const records = new Map();
  for (const person of persons || []) {
    const identity = sourceIdentity(person, sourceRefs);
    const sourceRef = sourceRefForPerson(person, sourceRefs);
    const existing = records.get(identity);
    if (!existing) {
      records.set(identity, {
        ...person,
        sourceRef,
        output_node_ids: unique(person.output_node_ids || [person.output_node_id]),
        source_node_ids: unique(person.source_node_ids || [person.source_node_id]),
        occurrences: [...(person.occurrences || [])],
        ops: [...(person.ops || [])],
        provenance: unique(person.provenance),
      });
      continue;
    }
    existing.output_node_ids = unique([
      ...existing.output_node_ids,
      ...(person.output_node_ids || [person.output_node_id]),
    ]);
    existing.source_node_ids = unique([
      ...existing.source_node_ids,
      ...(person.source_node_ids || [person.source_node_id]),
    ]);
    existing.provenance = unique([...existing.provenance, ...(person.provenance || [])]);

    const seenOperations = new Set(existing.ops.map(operationKey));
    for (const operation of person.ops || []) {
      if (!seenOperations.has(operationKey(operation))) {
        seenOperations.add(operationKey(operation));
        existing.ops.push(operation);
      }
    }
    const seenOccurrences = new Set(existing.occurrences.map(occurrenceKey));
    for (const occurrence of person.occurrences || []) {
      if (!seenOccurrences.has(occurrenceKey(occurrence))) {
        seenOccurrences.add(occurrenceKey(occurrence));
        existing.occurrences.push(occurrence);
      }
    }
  }
  return [...records.values()];
}

export function sourceVariantCount(person) {
  const outputs = Array.isArray(person?.output_node_ids)
    ? unique(person.output_node_ids)
    : unique([person?.output_node_id]);
  if (outputs.length) return outputs.length;
  return unique((person?.occurrences || []).map((occurrence) => occurrence?.output_node_id)).length;
}

export function draftsForSource(sourceRef, drafts = []) {
  if (!sourceRef) return [];
  return (drafts || []).filter((draft) => (draft.sourceRefs || []).includes(sourceRef));
}

export function sourceMedia(person) {
  const sourceKey = String(person?.source_key || "").trim();
  const provenance = person?.provenance || [];
  const isVideo = provenance.includes("LoadVideo") || VIDEO_EXTENSIONS.test(sourceKey);
  const isImage = provenance.includes("LoadImage") || IMAGE_EXTENSIONS.test(sourceKey);
  if (!sourceKey || (!isVideo && !isImage)) return null;

  const annotatedType = sourceKey.match(/\s*\[(input|output|temp)\]\s*$/i)?.[1]?.toLowerCase();
  const cleanKey = sourceKey
    .replace(/\s*\[(?:input|output|temp)\]\s*$/i, "")
    .replaceAll("\\", "/");
  const parts = cleanKey.split("/").filter(Boolean);
  const filename = parts.pop() || cleanKey;
  const params = new URLSearchParams({ filename, type: annotatedType || "input" });
  if (parts.length) params.set("subfolder", parts.join("/"));
  if (isImage) params.set("preview", "webp;80");
  return { kind: isVideo ? "video" : "image", url: `/api/view?${params}` };
}

export function sourceSupportsNoPersonReview(person) {
  const media = sourceMedia(person);
  return media?.kind === "image" || media?.kind === "video";
}

export function sourceDisplayLabel(person, index = 0) {
  const key = String(person?.source_key || "").trim();
  if (key) {
    const clean = key.replace(/\s*\[(?:input|output|temp)\]\s*$/i, "");
    const basename = clean.split(/[\\/]/).pop();
    if (basename) return basename;
  }
  const kind = String(person?.source_kind || "source").replaceAll("_", " ");
  return `${kind.charAt(0).toUpperCase()}${kind.slice(1)} ${index + 1}`;
}
