// Pure identity-graph projection helpers. Exact occurrence ownership stays
// local; only a complete, project-person-by-source projection may leave the
// plugin. Keeping this module DOM-free makes retries and browser rendering use
// the same deterministic rules.

function list(value) {
  return Array.isArray(value) ? value : [];
}

function text(value) {
  return value == null ? "" : String(value);
}

function unique(values) {
  return [...new Set(list(values).filter(Boolean).map(String))];
}

function linkCandidateId(link = {}) {
  return text(link.candidateId || link.candidate_id);
}

function linkPersonId(link = {}) {
  return text(link.personId || link.person_id);
}

function linkOccurrenceIds(link = {}) {
  return unique(link.occurrenceIds || link.occurrence_ids);
}

function linkSourceRefs(link = {}) {
  return unique(link.sourceRefs || link.source_refs);
}

function draftId(draft = {}) {
  return text(draft?.draftId || draft?.draft_id);
}

function canonicalId(draft = {}) {
  return text(draft?.canonicalPersonId || draft?.canonical_person_id);
}

function mergedIntoId(draft = {}) {
  return text(draft?.mergedIntoDraftId || draft?.merged_into_draft_id);
}

export function activeIdentityDrafts(drafts = []) {
  return list(drafts).filter((draft) => !mergedIntoId(draft));
}

export function identityAliasMap(drafts = []) {
  const byDraftId = new Map(
    list(drafts)
      .map((draft) => [draftId(draft), draft])
      .filter(([id]) => Boolean(id))
  );
  const aliases = new Map();

  function resolve(rawId, trail = new Set()) {
    const id = text(rawId);
    if (!id || trail.has(id)) return id;
    if (aliases.has(id)) return aliases.get(id);
    const draft = byDraftId.get(id);
    if (!draft) return id;
    const nextTrail = new Set(trail).add(id);
    const merged = mergedIntoId(draft);
    const resolved = merged
      ? resolve(merged, nextTrail)
      : canonicalId(draft) || id;
    aliases.set(id, resolved);
    return resolved;
  }

  for (const draft of list(drafts)) {
    const localId = draftId(draft);
    const projectId = canonicalId(draft);
    if (localId) aliases.set(localId, resolve(localId));
    if (projectId) aliases.set(projectId, projectId);
  }
  return aliases;
}

export function resolveIdentityPersonId(personId = "", drafts = []) {
  const id = text(personId);
  if (!id) return "";
  return identityAliasMap(drafts).get(id) || id;
}

function candidateOccurrenceIds(candidate = {}, occurrencesByCandidate = new Map()) {
  const candidateId = text(candidate.candidateId || candidate.candidate_id);
  const explicit = unique(candidate.occurrenceIds || candidate.occurrence_ids);
  if (explicit.length) return explicit;
  return list(occurrencesByCandidate.get(candidateId)).map((occurrence) =>
    text(occurrence.occurrenceId || occurrence.occurrence_id)
  ).filter(Boolean);
}

function identityIndexes(identity = {}) {
  const candidates = list(identity.candidates);
  const occurrences = list(identity.occurrences);
  const candidateById = new Map(candidates.map((candidate) => [
    text(candidate.candidateId || candidate.candidate_id),
    candidate,
  ]));
  const occurrenceById = new Map(occurrences.map((occurrence) => [
    text(occurrence.occurrenceId || occurrence.occurrence_id),
    occurrence,
  ]));
  const occurrencesByCandidate = new Map();
  for (const occurrence of occurrences) {
    const candidateId = text(occurrence.candidateId || occurrence.candidate_id);
    if (!occurrencesByCandidate.has(candidateId)) occurrencesByCandidate.set(candidateId, []);
    occurrencesByCandidate.get(candidateId).push(occurrence);
  }
  return { candidates, candidateById, occurrenceById, occurrencesByCandidate };
}

function occurrencesForLink(link, indexes) {
  const explicit = linkOccurrenceIds(link);
  if (explicit.length) return explicit;
  const candidate = indexes.candidateById.get(linkCandidateId(link));
  return candidate ? candidateOccurrenceIds(candidate, indexes.occurrencesByCandidate) : [];
}

function projectPersonRecord(personId, drafts, canonicalPeople) {
  const activeDrafts = activeIdentityDrafts(drafts);
  const projectPerson = list(canonicalPeople).find((person) =>
    text(person.id || person.talentRecordId || person.talent_record_id) === personId
  ) || null;
  const draft = activeDrafts.find((candidate) =>
    draftId(candidate) === personId || canonicalId(candidate) === personId
  ) || null;
  const canonicalPersonId = text(
    projectPerson?.id
    || projectPerson?.talentRecordId
    || projectPerson?.talent_record_id
    || canonicalId(draft)
  );
  return {
    personId,
    canonicalPersonId,
    draftId: draftId(draft),
    displayName: text(
      projectPerson?.displayName
      || projectPerson?.name
      || draft?.displayName
      || draft?.display_name
      || "Reviewed person"
    ),
    role: text(projectPerson?.role || draft?.role),
    draft,
    projectPerson,
    scope: canonicalPersonId ? "project" : "workflow",
  };
}

export function logicalIdentityPeople(
  identity = {},
  links = [],
  drafts = [],
  canonicalPeople = []
) {
  const indexes = identityIndexes(identity);
  const aliases = identityAliasMap(drafts);
  const groups = new Map();

  for (const link of list(links)) {
    if (text(link.state || link.status || "confirmed") !== "confirmed") continue;
    const rawPersonId = linkPersonId(link);
    const personId = aliases.get(rawPersonId) || rawPersonId;
    if (!personId) continue;
    const person = groups.get(personId) || {
      ...projectPersonRecord(personId, drafts, canonicalPeople),
      candidateIds: new Set(),
      occurrenceIds: new Set(),
      sourceRefs: new Set(),
      occurrences: [],
    };
    const candidateId = linkCandidateId(link);
    if (candidateId) person.candidateIds.add(candidateId);
    for (const occurrenceId of occurrencesForLink(link, indexes)) {
      if (person.occurrenceIds.has(occurrenceId)) continue;
      person.occurrenceIds.add(occurrenceId);
      const occurrence = indexes.occurrenceById.get(occurrenceId);
      if (!occurrence) continue;
      person.occurrences.push(occurrence);
      const sourceRef = text(occurrence.sourceRef || occurrence.source_ref);
      if (sourceRef) person.sourceRefs.add(sourceRef);
    }
    for (const sourceRef of linkSourceRefs(link)) person.sourceRefs.add(sourceRef);
    groups.set(personId, person);
  }

  for (const person of groups.values()) {
    for (const sourceRef of list(person.draft?.sourceRefs || person.draft?.source_refs)) {
      if (sourceRef) person.sourceRefs.add(String(sourceRef));
    }
  }

  return [...groups.values()]
    .map((person) => ({
      ...person,
      candidateIds: [...person.candidateIds].sort(),
      occurrenceIds: [...person.occurrenceIds].sort(),
      sourceRefs: [...person.sourceRefs].sort(),
      occurrences: [...person.occurrences].sort((left, right) =>
        text(left.occurrenceId || left.occurrence_id)
          .localeCompare(text(right.occurrenceId || right.occurrence_id))
      ),
    }))
    .sort((left, right) =>
      left.displayName.localeCompare(right.displayName, undefined, { sensitivity: "base" })
        || left.personId.localeCompare(right.personId)
    );
}

// Derive the complete fail-closed source projection. A source is linked only
// when every analyzed occurrence is resolved and every assigned identity has a
// canonical project-person id. The workspace schema intentionally cannot claim
// partial occurrence-level ownership.
export function projectIdentitySources(
  identity = {},
  links = [],
  drafts = [],
  canonicalPeople = []
) {
  const indexes = identityIndexes(identity);
  const aliases = identityAliasMap(drafts);
  const confirmedBySource = new Map();
  const localBySource = new Set();
  const resolvedOccurrenceIds = new Set();
  const dismissedOccurrenceIds = new Set();
  const sourceOccurrenceIds = new Map();
  const emptyCandidateIdsBySource = new Map();
  const dismissedEmptyCandidateSources = new Set();
  const unresolvedEmptyCandidateSources = new Set();
  const emptyCandidateSourceKey = (candidateId, sourceRef) => `${candidateId}\u0000${sourceRef}`;

  for (const occurrence of indexes.occurrenceById.values()) {
    const sourceRef = text(occurrence.sourceRef || occurrence.source_ref);
    const occurrenceId = text(occurrence.occurrenceId || occurrence.occurrence_id);
    if (!sourceRef || !occurrenceId) continue;
    if (!sourceOccurrenceIds.has(sourceRef)) sourceOccurrenceIds.set(sourceRef, new Set());
    sourceOccurrenceIds.get(sourceRef).add(occurrenceId);
  }

  for (const link of list(links)) {
    const state = text(link.state || link.status || "confirmed");
    const rawPersonId = linkPersonId(link);
    const personId = aliases.get(rawPersonId) || rawPersonId;
    const occurrenceIds = occurrencesForLink(link, indexes);
    if (state === "confirmed" && personId) {
      const person = projectPersonRecord(personId, drafts, canonicalPeople);
      for (const occurrenceId of occurrenceIds) {
        resolvedOccurrenceIds.add(occurrenceId);
        const occurrence = indexes.occurrenceById.get(occurrenceId);
        const sourceRef = text(occurrence?.sourceRef || occurrence?.source_ref);
        if (!sourceRef) continue;
        if (person.canonicalPersonId) {
          if (!confirmedBySource.has(sourceRef)) confirmedBySource.set(sourceRef, new Set());
          confirmedBySource.get(sourceRef).add(person.canonicalPersonId);
        } else {
          localBySource.add(sourceRef);
        }
      }
    } else if (state === "rejected" && !personId) {
      for (const occurrenceId of occurrenceIds) {
        resolvedOccurrenceIds.add(occurrenceId);
        dismissedOccurrenceIds.add(occurrenceId);
      }
    }
  }

  // Some manual/body/silhouette review groups legitimately have source-level
  // evidence but no face occurrence. Keep those groups in the same fail-closed
  // projector instead of allowing a previously linked source to survive an
  // unresolved source-only identity.
  for (const candidate of indexes.candidates) {
    const candidateId = text(candidate.candidateId || candidate.candidate_id);
    const occurrenceIds = candidateOccurrenceIds(candidate, indexes.occurrencesByCandidate);
    if (!candidateId || occurrenceIds.length) continue;
    const sourceRefs = unique(candidate.sourceRefs || candidate.source_refs);
    if (!sourceRefs.length) continue;
    for (const sourceRef of sourceRefs) {
      if (!sourceOccurrenceIds.has(sourceRef)) sourceOccurrenceIds.set(sourceRef, new Set());
      if (!emptyCandidateIdsBySource.has(sourceRef)) emptyCandidateIdsBySource.set(sourceRef, new Set());
      emptyCandidateIdsBySource.get(sourceRef).add(candidateId);
    }

    const candidateLinks = list(links).filter((link) => linkCandidateId(link) === candidateId);
    for (const sourceRef of sourceRefs) {
      const sourceLinks = candidateLinks.filter((link) => {
        const selectedSourceRefs = linkSourceRefs(link);
        // Missing sourceRefs is the legacy candidate-wide representation.
        return !selectedSourceRefs.length || selectedSourceRefs.includes(sourceRef);
      });
      const confirmedLinks = sourceLinks.filter((link) =>
        text(link.state || link.status || "confirmed") === "confirmed" && linkPersonId(link)
      );
      if (confirmedLinks.length) {
        for (const link of confirmedLinks) {
          const rawPersonId = linkPersonId(link);
          const personId = aliases.get(rawPersonId) || rawPersonId;
          const person = projectPersonRecord(personId, drafts, canonicalPeople);
          if (person.canonicalPersonId) {
            if (!confirmedBySource.has(sourceRef)) confirmedBySource.set(sourceRef, new Set());
            confirmedBySource.get(sourceRef).add(person.canonicalPersonId);
          } else {
            localBySource.add(sourceRef);
          }
        }
        continue;
      }
      const key = emptyCandidateSourceKey(candidateId, sourceRef);
      if (sourceLinks.some((link) =>
        text(link.state || link.status || "confirmed") === "rejected" && !linkPersonId(link)
      )) {
        dismissedEmptyCandidateSources.add(key);
      } else {
        unresolvedEmptyCandidateSources.add(key);
      }
    }
  }

  // Source-only/manual assignments are still part of the full person graph.
  // Superseded aliases never contribute a second person.
  for (const draft of activeIdentityDrafts(drafts)) {
    const personId = resolveIdentityPersonId(draftId(draft), drafts);
    const person = projectPersonRecord(personId, drafts, canonicalPeople);
    for (const rawSourceRef of list(draft.sourceRefs || draft.source_refs)) {
      const sourceRef = text(rawSourceRef);
      if (!sourceRef) continue;
      if (person.canonicalPersonId) {
        if (!confirmedBySource.has(sourceRef)) confirmedBySource.set(sourceRef, new Set());
        confirmedBySource.get(sourceRef).add(person.canonicalPersonId);
      } else {
        localBySource.add(sourceRef);
      }
    }
  }

  const sourceRefs = new Set([
    ...sourceOccurrenceIds.keys(),
    ...confirmedBySource.keys(),
    ...localBySource,
  ]);
  const projection = new Map();
  for (const sourceRef of [...sourceRefs].sort()) {
    const occurrenceIds = sourceOccurrenceIds.get(sourceRef) || new Set();
    const unresolved = [...occurrenceIds].some((occurrenceId) =>
      !resolvedOccurrenceIds.has(occurrenceId)
    ) || [...(emptyCandidateIdsBySource.get(sourceRef) || [])].some((candidateId) =>
      unresolvedEmptyCandidateSources.has(emptyCandidateSourceKey(candidateId, sourceRef))
    );
    const hasLocalPerson = localBySource.has(sourceRef);
    const canonicalPersonIds = [...(confirmedBySource.get(sourceRef) || [])].sort();
    if (unresolved || hasLocalPerson) {
      projection.set(sourceRef, {
        disposition: "review_required",
        talentRecordIds: [],
      });
      continue;
    }
    if (canonicalPersonIds.length) {
      projection.set(sourceRef, {
        disposition: "linked",
        talentRecordIds: canonicalPersonIds,
      });
      continue;
    }
    const emptyCandidateIds = emptyCandidateIdsBySource.get(sourceRef) || new Set();
    const hasReviewedEvidence = occurrenceIds.size > 0 || emptyCandidateIds.size > 0;
    const allDismissed = hasReviewedEvidence
      && [...occurrenceIds].every((occurrenceId) => dismissedOccurrenceIds.has(occurrenceId))
      && [...emptyCandidateIds].every((candidateId) =>
        dismissedEmptyCandidateSources.has(emptyCandidateSourceKey(candidateId, sourceRef))
      );
    projection.set(sourceRef, {
      disposition: allDismissed ? "not_person" : "review_required",
      talentRecordIds: [],
    });
  }
  return projection;
}

function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) =>
      `${JSON.stringify(key)}:${stableJson(value[key])}`
    ).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function canonicalIdentityReview(identity = {}, links = [], drafts = []) {
  const indexes = identityIndexes(identity);
  const aliases = identityAliasMap(drafts);
  return list(links)
    .map((link) => ({
      candidateId: linkCandidateId(link),
      personId: aliases.get(linkPersonId(link)) || linkPersonId(link) || undefined,
      state: text(link.state || link.status || "confirmed"),
      occurrenceIds: occurrencesForLink(link, indexes).sort(),
      sourceRefs: linkSourceRefs(link).sort(),
    }))
    .sort((left, right) => stableJson(left).localeCompare(stableJson(right)));
}

export async function identityReviewHash(identity = {}, links = [], drafts = []) {
  const bytes = new TextEncoder().encode(
    stableJson(canonicalIdentityReview(identity, links, drafts))
  );
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}
