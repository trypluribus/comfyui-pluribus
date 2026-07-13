// The rights manifest is deliberately smaller than a ComfyUI graph. Canvas
// layout, sampler settings, preview nodes, prompts, filenames, and media never
// leave localhost and never invalidate a confirmation. Only source/person
// relationships and operations that materially affect a person's likeness do.

const RIGHTS_RELEVANT_OPERATIONS = new Set([
  "ReActorFaceSwap",
  "IPAdapter",
  "IPAdapterAdvanced",
  "IPAdapterApply",
  "LoraLoader",
  "LoraLoaderModelOnly",
  "LoadImage",
  "GeminiImage2Node",
  "FluxKontextProImageNode",
  "KlingImage2VideoNode",
  "CLIPTextEncode",
]);

export function personLocalKey(person) {
  return [person.source_kind || "unknown", person.source_key || "", person.source_node_id || ""].join("|");
}

export function normalizedOperations(person) {
  const values = [];
  for (const operation of person.ops || []) {
    const classType = String(operation.class_type || operation.classType || "");
    if (RIGHTS_RELEVANT_OPERATIONS.has(classType) && !values.includes(classType)) {
      values.push(classType);
    }
  }
  for (const classType of person.provenance || []) {
    if (RIGHTS_RELEVANT_OPERATIONS.has(classType) && !values.includes(classType)) {
      values.push(classType);
    }
  }
  return values.sort().map((classType) => ({ classType }));
}

// Rebuild the complete current manifest from a fresh local scan. Existing
// links are preserved by opaque sourceRef, new sources start in review, and
// sources no longer present in the graph are deliberately omitted so the
// server can reconcile them as stale. Graph-local labels and node ids never
// enter this return value.
export function manifestSourcesForScan(persons, sourceRefs, existingSources, overrides = {}) {
  const existingByRef = new Map(
    (existingSources || []).map((source) => [
      String(source.sourceRef || source.source_ref || ""),
      source,
    ])
  );
  const overrideByRef = overrides instanceof Map
    ? overrides
    : new Map(Object.entries(overrides || {}));

  const currentByRef = new Map();
  for (const person of persons || []) {
    const sourceRef = sourceRefs?.[personLocalKey(person)];
    if (!/^[a-f0-9]{64}$/.test(sourceRef || "")) continue;
    const existing = existingByRef.get(sourceRef) || {};
    const override = overrideByRef.get(sourceRef) || {};
    const disposition = String(
      override.disposition || existing.disposition || "review_required"
    );
    const existingIds =
      existing.talentRecordIds ||
      existing.talent_record_ids ||
      [existing.talentRecordId || existing.talent_record_id].filter(Boolean);
    const talentRecordIds = disposition === "linked"
      ? [...new Set(override.talentRecordIds || existingIds || [])]
      : [];
    const prior = currentByRef.get(sourceRef);
    const operations = normalizedOperations(person);
    if (prior) {
      prior.operations = [...new Set([
        ...prior.operations.map((operation) => operation.classType),
        ...operations.map((operation) => operation.classType),
      ])].sort().map((classType) => ({ classType }));
      continue;
    }
    currentByRef.set(sourceRef, {
      sourceRef,
      sourceKind: person.source_kind || "unknown",
      disposition,
      talentRecordIds,
      operations,
    });
  }
  return [...currentByRef.values()]
    .sort((left, right) => left.sourceRef.localeCompare(right.sourceRef));
}

export function canonicalRightsManifest(workflowRef, sources, workflowKind = "other") {
  const normalized = (sources || [])
    .map((source) => ({
      sourceRef: String(source.sourceRef || ""),
      sourceKind: String(source.sourceKind || "unknown"),
      disposition: String(source.disposition || "review_required"),
      talentRecordIds: [...new Set(source.talentRecordIds || [])].sort(),
      operations: [...new Set((source.operations || []).map((operation) =>
        String(operation.classType || operation.class_type || "")
      ).filter(Boolean))].sort().map((classType) => ({ classType })),
    }))
    .filter((source) => /^[a-f0-9]{64}$/.test(source.sourceRef))
    .sort((left, right) =>
      `${left.sourceRef}|${left.sourceKind}`.localeCompare(`${right.sourceRef}|${right.sourceKind}`)
    );
  return stableJson({
    workflowRef: String(workflowRef || ""),
    workflowKind: String(workflowKind || "other"),
    sources: normalized,
  });
}

export async function rightsManifestHash(workflowRef, sources, workflowKind = "other") {
  const bytes = new TextEncoder().encode(
    canonicalRightsManifest(workflowRef, sources, workflowKind)
  );
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}
