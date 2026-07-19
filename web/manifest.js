// The rights manifest is deliberately smaller than a ComfyUI graph. Canvas
// layout, sampler settings, preview nodes, prompts, filenames, and media never
// leave localhost and never invalidate a confirmation. Only source/person
// relationships and operations that materially affect a person's likeness do.

import { RIGHTS_RELEVANT_OPERATIONS } from "./operation-registry.js";

export function personLocalKey(person) {
  return [person.source_kind || "unknown", person.source_key || "", person.source_node_id || ""].join("|");
}

export function normalizedOperations(person) {
  const values = new Map();
  for (const operation of person.ops || []) {
    const classType = String(operation.class_type || operation.classType || "");
    const sourceRole = String(operation.source_role || operation.sourceRole || "");
    if (RIGHTS_RELEVANT_OPERATIONS.has(classType)) {
      values.set(`${classType}|${sourceRole}`, {
        classType,
        ...(sourceRole ? { sourceRole } : {}),
      });
    }
  }
  return [...values.values()].sort((left, right) =>
    `${left.classType}|${left.sourceRole || ""}`.localeCompare(
      `${right.classType}|${right.sourceRole || ""}`
    )
  );
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
    const existingIds =
      existing.talentRecordIds ||
      existing.talent_record_ids ||
      [existing.talentRecordId || existing.talent_record_id].filter(Boolean);
    const hasExplicitIds = Array.isArray(override.talentRecordIds);
    const talentRecordIds = new Set(
      (hasExplicitIds ? override.talentRecordIds : existingIds || [])
        .filter(Boolean)
        .map(String)
    );
    for (const personId of override.removeTalentRecordIds || []) {
      talentRecordIds.delete(String(personId));
    }
    for (const personId of override.addTalentRecordIds || []) {
      if (personId) talentRecordIds.add(String(personId));
    }
    const hasPersonDelta = Array.isArray(override.addTalentRecordIds)
      || Array.isArray(override.removeTalentRecordIds);
    const disposition = String(
      talentRecordIds.size
        ? "linked"
        : override.disposition
          || (hasPersonDelta ? "review_required" : existing.disposition)
          || "review_required"
    );
    const prior = currentByRef.get(sourceRef);
    const operations = normalizedOperations(person);
    if (prior) {
      const merged = new Map();
      for (const operation of [...prior.operations, ...operations]) {
        merged.set(`${operation.classType}|${operation.sourceRole || ""}`, operation);
      }
      prior.operations = [...merged.values()].sort((left, right) =>
        `${left.classType}|${left.sourceRole || ""}`.localeCompare(
          `${right.classType}|${right.sourceRole || ""}`
        )
      );
      continue;
    }
    currentByRef.set(sourceRef, {
      sourceRef,
      sourceKind: person.source_kind || "unknown",
      disposition,
      talentRecordIds: disposition === "linked" ? [...talentRecordIds].sort() : [],
      operations,
    });
  }
  return [...currentByRef.values()]
    .sort((left, right) => left.sourceRef.localeCompare(right.sourceRef));
}

// Combine source-link deltas from overlapping review actions. Add/remove
// operations are order-sensitive per person: the newest operation wins while
// unrelated people and unrelated sources remain in the pending batch.
export function mergeManifestOverrideMaps(previous = new Map(), incoming = new Map()) {
  const asMap = (value) => value instanceof Map
    ? value
    : new Map(Object.entries(value || {}));
  const merged = new Map(
    [...asMap(previous)].map(([sourceRef, value]) => [String(sourceRef), { ...(value || {}) }])
  );
  for (const [rawSourceRef, rawIncoming] of asMap(incoming)) {
    const sourceRef = String(rawSourceRef);
    const prior = merged.get(sourceRef) || {};
    const next = rawIncoming || {};
    const value = { ...prior, ...next };
    const hasExplicitIds = Object.prototype.hasOwnProperty.call(next, "talentRecordIds");
    const additions = new Set(hasExplicitIds ? [] : prior.addTalentRecordIds || []);
    const removals = new Set(hasExplicitIds ? [] : prior.removeTalentRecordIds || []);
    for (const personId of next.removeTalentRecordIds || []) {
      const id = String(personId || "");
      if (!id) continue;
      additions.delete(id);
      removals.add(id);
    }
    for (const personId of next.addTalentRecordIds || []) {
      const id = String(personId || "");
      if (!id) continue;
      removals.delete(id);
      additions.add(id);
    }
    if (additions.size) value.addTalentRecordIds = [...additions].sort();
    else delete value.addTalentRecordIds;
    if (removals.size) value.removeTalentRecordIds = [...removals].sort();
    else delete value.removeTalentRecordIds;
    merged.set(sourceRef, value);
  }
  return merged;
}

export function manifestOverridesForLocalReviews(
  sourceReviews,
  existingSources,
  overrides = new Map(),
  sourceHashes = [],
  personDrafts = []
) {
  const merged = new Map();
  const existingByRef = new Map(
    (existingSources || []).map((source) => [
      String(source.sourceRef || source.source_ref || ""),
      source,
    ])
  );
  const currentHashes = new Map(
    (sourceHashes || []).map((entry) => [entry.sourceRef, entry.sourceHash])
  );
  for (const [sourceRef, review] of Object.entries(sourceReviews || {})) {
    if (!["not_person", "review_required"].includes(review?.state)) continue;
    if (existingByRef.get(sourceRef)?.disposition === "linked") continue;
    const currentHash = currentHashes.get(sourceRef);
    if (!currentHash) continue;
    merged.set(sourceRef, {
      disposition: currentHash === review.sourceHash
        ? review.state
        : "review_required",
    });
  }
  for (const draft of personDrafts || []) {
    for (const sourceRef of draft.sourceRefs || []) {
      if (existingByRef.get(sourceRef)?.disposition === "linked") continue;
      merged.set(sourceRef, { disposition: "review_required" });
    }
  }
  const explicit = overrides instanceof Map
    ? overrides
    : new Map(Object.entries(overrides || {}));
  for (const [sourceRef, value] of explicit) merged.set(sourceRef, value);
  return merged;
}

export function canonicalRightsManifest(workflowRef, sources, workflowKind = "other") {
  const normalized = (sources || [])
    .map((source) => ({
      sourceRef: String(source.sourceRef || ""),
      sourceKind: String(source.sourceKind || "unknown"),
      disposition: String(source.disposition || "review_required"),
      talentRecordIds: [...new Set(source.talentRecordIds || [])].sort(),
      operations: [...new Map((source.operations || []).map((operation) => {
        const classType = String(operation.classType || operation.class_type || "");
        const sourceRole = String(operation.sourceRole || operation.source_role || "");
        return [
          `${classType}|${sourceRole}`,
          { classType, ...(sourceRole ? { sourceRole } : {}) },
        ];
      }).filter(([, operation]) => Boolean(operation.classType))).values()].sort(
        (left, right) => `${left.classType}|${left.sourceRole || ""}`.localeCompare(
          `${right.classType}|${right.sourceRole || ""}`
        )
      ),
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
