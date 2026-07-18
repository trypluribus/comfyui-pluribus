// Normalization and presentation helpers for the optional local identity pass.
// Keep this module DOM-free so the contract can be tested in Node and so older
// ComfyUI installs can safely ignore identity fields they do not understand.

import { operationActions } from "./operation-registry.js";

function list(value) {
  return Array.isArray(value) ? value : [];
}

function text(value, fallback = "") {
  return value == null ? fallback : String(value);
}

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function revision(value) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

function unique(values) {
  return [...new Set(values.filter(Boolean).map(String))];
}

function evidenceImageRef(entry) {
  const value = typeof entry === "string"
    ? entry
    : entry?.url || entry?.imageUrl || entry?.image_url || entry?.cropUrl || entry?.crop_url || entry?.previewUrl || entry?.preview_url;
  const candidate = text(value).trim();
  if (!candidate) return "";
  if (/^(?:data:image\/|blob:|https?:\/\/|\/)/i.test(candidate)) return candidate;
  return /\.(?:png|jpe?g|webp|gif|bmp|tiff?)(?:[?#].*)?$/i.test(candidate) ? candidate : "";
}

function idFor(value, index, prefix) {
  return text(value?.id || value?.candidateId || value?.candidate_id || value?.occurrenceId || value?.occurrence_id)
    || `${prefix}-${index + 1}`;
}

function normalizeOccurrence(raw, index) {
  const bounds = raw?.bounds || raw?.bbox || raw?.box || null;
  return {
    ...raw,
    occurrenceId: text(raw?.occurrenceId || raw?.occurrence_id || raw?.id) || `occurrence-${index + 1}`,
    candidateId: text(raw?.candidateId || raw?.candidate_id || raw?.clusterId || raw?.cluster_id),
    sourceRef: text(raw?.sourceRef || raw?.source_ref),
    sourceLabel: text(raw?.sourceLabel || raw?.source_label || raw?.filename || raw?.sourceKey || raw?.source_key),
    sceneLabel: text(raw?.sceneLabel || raw?.scene_label || raw?.scene || raw?.shotLabel || raw?.shot_label),
    timecode: text(raw?.timecode || raw?.timestampLabel || raw?.timestamp_label),
    timestampSeconds: number(raw?.timestampSeconds ?? raw?.timestamp_seconds ?? raw?.timestamp, -1),
    confidence: number(raw?.confidence ?? raw?.similarity, 0),
    cropUrl: text(raw?.cropUrl || raw?.crop_url || raw?.previewUrl || raw?.preview_url || raw?.thumbnailUrl || raw?.thumbnail_url),
    frameUrl: text(raw?.frameUrl || raw?.frame_url),
    bounds,
    ambiguous: Boolean(raw?.ambiguous || raw?.needsReview || raw?.needs_review),
  };
}

function normalizeCandidate(raw, index) {
  const rawEvidence = list(raw?.evidence);
  return {
    ...raw,
    candidateId: idFor(raw, index, "candidate"),
    suggestedName: text(raw?.suggestedName || raw?.suggested_name || raw?.displayName || raw?.display_name || raw?.name),
    suggestedRole: text(raw?.suggestedRole || raw?.suggested_role || raw?.role),
    confidence: number(raw?.confidence ?? raw?.identityConfidence ?? raw?.identity_confidence, 0),
    groupingBand: text(raw?.groupingBand || raw?.grouping_band || raw?.confidenceBand || raw?.confidence_band || raw?.groupingLabel || raw?.grouping_label).toLowerCase(),
    occurrenceIds: unique(list(raw?.occurrenceIds || raw?.occurrence_ids).map(String)),
    sourceRefs: unique(list(raw?.sourceRefs || raw?.source_refs).map(String)),
    evidenceImages: unique(rawEvidence.map(evidenceImageRef)),
    evidence: [
      ...rawEvidence.filter((entry) => !evidenceImageRef(entry)).map((entry) =>
        typeof entry === "string" ? entry : text(entry?.label || entry?.description || entry?.reason)
      ),
      ...list(raw?.reasons).map((entry) => typeof entry === "string" ? entry : text(entry?.label || entry?.description || entry?.reason)),
      ...list(raw?.signals).map((entry) => typeof entry === "string" ? entry : text(entry?.label || entry?.description || entry?.reason)),
    ].filter(Boolean),
    state: text(raw?.state || raw?.status, "review_required"),
    needsReview: Boolean(raw?.needsReview ?? raw?.needs_review ?? raw?.ambiguous),
  };
}

function normalizeLink(raw) {
  return {
    ...raw,
    candidateId: text(raw?.candidateId || raw?.candidate_id),
    personId: text(raw?.personId || raw?.person_id),
    displayName: text(raw?.displayName || raw?.display_name),
    state: text(raw?.state || raw?.status, "confirmed"),
    occurrenceIds: unique(list(raw?.occurrenceIds || raw?.occurrence_ids).map(String)),
  };
}

function normalizeCoverage(raw = {}) {
  const total = number(raw.totalSources ?? raw.total_sources ?? raw.total ?? raw.mediaTotal ?? raw.media_total, 0);
  const analyzed = number(raw.analyzedSources ?? raw.analyzed_sources ?? raw.analyzed ?? raw.mediaAnalyzed ?? raw.media_analyzed, 0);
  const manualReviewValue = raw.manualReviewSources ?? raw.manual_review_sources;
  return {
    ...raw,
    total,
    analyzed,
    skipped: number(raw.skippedSources ?? raw.skipped_sources ?? raw.skipped, Math.max(0, total - analyzed)),
    imageCount: number(raw.imageCount ?? raw.image_count ?? raw.images, 0),
    videoCount: number(raw.videoCount ?? raw.video_count ?? raw.videos, 0),
    audioCount: number(raw.audioCount ?? raw.audio_count ?? raw.audio, 0),
    manualReviewSourceCount: Array.isArray(manualReviewValue)
      ? manualReviewValue.length
      : number(manualReviewValue, 0),
  };
}

const MANUAL_SOURCE_REVIEW_CODES = new Set([
  "analysis_incomplete",
  "crop_memory_limit_reached",
  "evidence_artifact_limit_reached",
  "evidence_omitted_source",
  "evidence_source_truncated",
  "faces_per_frame_limited",
  "frame_pixels_limited",
  "image_decode_failed",
  "no_face_detected",
  "occurrence_limit_reached",
  "source_file_too_large",
  "source_image_pixels_exceeded",
  "source_total_bytes_exceeded",
  "source_unavailable",
  "source_unsupported",
  "video_decode_failed",
  "video_frame_limit_reached",
]);

export function sourceIssueNeedsManualReview(issue = {}) {
  const explicit = issue.manualReviewRequired
    ?? issue.manual_review_required
    ?? issue.requiresManualReview
    ?? issue.requires_manual_review
    ?? issue.manualReview
    ?? issue.manual_review;
  if (explicit === true) return true;
  const code = text(issue.code || issue.issueCode || issue.issue_code).toLowerCase();
  if (MANUAL_SOURCE_REVIEW_CODES.has(code)) return true;
  return /(?:decode|unavailable|unreadable|resolver|skipp|incomplete|truncat|limit|exceed|omitt|partial|budget)/.test(code);
}

export function identityManualReviewItems(identity = {}) {
  const bySourceRef = new Map();
  for (const issue of list(identity?.issues)) {
    const sourceRef = text(issue?.sourceRef || issue?.source_ref);
    if (!sourceRef || !sourceIssueNeedsManualReview(issue)) continue;
    const prior = bySourceRef.get(sourceRef);
    bySourceRef.set(sourceRef, {
      ...(prior || {}),
      ...issue,
      sourceRef,
      sourceHash: text(issue?.sourceHash || issue?.source_hash || prior?.sourceHash),
      issueCodes: unique([
        ...(prior?.issueCodes || []),
        ...list(issue?.issueCodes || issue?.issue_codes),
        issue?.code,
      ]),
      manualReviewRequired: true,
    });
  }
  for (const item of list(identity?.manualReviewSources || identity?.manual_review_sources)) {
    const sourceRef = text(item?.sourceRef || item?.source_ref);
    if (!sourceRef) continue;
    const prior = bySourceRef.get(sourceRef);
    bySourceRef.set(sourceRef, {
      code: "analysis_incomplete",
      title: "Source needs manual person review",
      description: "Local identity analysis did not fully cover this source.",
      ...(prior || {}),
      ...item,
      sourceRef,
      sourceHash: text(item?.sourceHash || item?.source_hash || prior?.sourceHash),
      issueCodes: unique([
        ...(prior?.issueCodes || []),
        ...list(item?.issueCodes || item?.issue_codes),
      ]),
      manualReviewRequired: true,
    });
  }
  const items = [...bySourceRef.values()];
  const expectedCount = number(
    identity?.coverage?.manualReviewSourceCount
    ?? identity?.coverage?.manualReviewSources
    ?? identity?.coverage?.manual_review_sources,
    0
  );
  const missingCount = Math.max(0, expectedCount - items.length);
  for (let index = 0; index < missingCount; index += 1) {
    items.push({
      issueId: `manual-review-source-${index + 1}`,
      code: "analysis_incomplete",
      title: "Source needs manual person review",
      description: "Identity analysis reported incomplete source coverage without a source reference.",
      sourceRef: "",
      manualReviewRequired: true,
    });
  }
  if (identity?.manualReviewRequired && !items.length) {
    items.push({
      issueId: "manual-review-required",
      code: "analysis_incomplete",
      title: "Manual person review required",
      description: "Identity analysis did not fully cover every source.",
      sourceRef: "",
      manualReviewRequired: true,
    });
  }
  return items;
}

export function normalizeIdentityPayload(payload = {}) {
  const raw = payload?.result || payload?.payload || payload;
  const occurrences = list(raw?.occurrences).map(normalizeOccurrence);
  const candidates = list(raw?.candidates || raw?.clusters).map(normalizeCandidate);
  const occurrenceMap = new Map(occurrences.map((occurrence) => [occurrence.occurrenceId, occurrence]));

  for (const candidate of candidates) {
    const attached = occurrences.filter((occurrence) => occurrence.candidateId === candidate.candidateId);
    candidate.occurrenceIds = unique([
      ...candidate.occurrenceIds,
      ...attached.map((occurrence) => occurrence.occurrenceId),
    ]);
    candidate.occurrences = candidate.occurrenceIds.map((id) => occurrenceMap.get(id)).filter(Boolean);
    candidate.sourceRefs = unique([
      ...candidate.sourceRefs,
      ...candidate.occurrences.map((occurrence) => occurrence.sourceRef),
    ]);
  }

  return {
    coverage: normalizeCoverage(raw?.coverage),
    sourceHashes: list(raw?.sourceHashes || raw?.source_hashes).map((entry) => ({
      sourceRef: text(entry?.sourceRef || entry?.source_ref),
      sourceHash: text(entry?.sourceHash || entry?.source_hash),
    })).filter((entry) => entry.sourceRef && entry.sourceHash),
    candidates,
    occurrences,
    links: list(raw?.links ?? payload?.links).map(normalizeLink).filter((link) => link.candidateId),
    linksRevision: revision(
      raw?.linksRevision
      ?? raw?.links_revision
      ?? payload?.linksRevision
      ?? payload?.links_revision
    ),
    manualReviewRequired: Boolean(raw?.manualReviewRequired ?? raw?.manual_review_required),
    manualReviewSources: list(raw?.manualReviewSources || raw?.manual_review_sources).map((item) => ({
      ...item,
      sourceRef: text(item?.sourceRef || item?.source_ref),
      sourceHash: text(item?.sourceHash || item?.source_hash),
      issueCodes: unique(list(item?.issueCodes || item?.issue_codes).map(String)),
    })).filter((item) => item.sourceRef),
    issues: list(raw?.issues).map((issue, index) => ({
      ...issue,
      issueId: idFor(issue, index, "issue"),
      code: text(issue?.code || issue?.issueCode || issue?.issue_code, "analysis_note"),
      candidateId: text(issue?.candidateId || issue?.candidate_id || issue?.clusterId || issue?.cluster_id),
      sourceRef: text(issue?.sourceRef || issue?.source_ref),
      sourceHash: text(issue?.sourceHash || issue?.source_hash),
      manualReviewRequired: Boolean(
        issue?.manualReviewRequired
        ?? issue?.manual_review_required
        ?? issue?.requiresManualReview
        ?? issue?.requires_manual_review
        ?? issue?.manualReview
        ?? issue?.manual_review
      ),
      severity: text(issue?.severity, "review"),
      title: text(issue?.title || issue?.label || issue?.code, "Review identity"),
      description: text(issue?.description || issue?.message || issue?.detail),
    })),
  };
}

export function identityResultFromJob(job = {}) {
  const state = text(job?.state || job?.status).toLowerCase();
  if (state !== "completed" && state !== "complete" && state !== "succeeded") return null;
  return normalizeIdentityPayload(job);
}

export function identityLinksAfterPersonRemoval(links = [], personIds = []) {
  const removedPersonIds = new Set(list(personIds).filter(Boolean).map(String));
  if (!removedPersonIds.size) return [...list(links)];

  const retained = [];
  const unresolvedByCandidate = new Map();
  for (const link of list(links)) {
    const personId = text(link?.personId || link?.person_id);
    if (!removedPersonIds.has(personId)) {
      retained.push(link);
      continue;
    }
    if (!["confirmed", "unsure"].includes(text(link?.state, "confirmed"))) continue;
    const candidateId = text(link?.candidateId || link?.candidate_id);
    if (!candidateId) continue;
    const occurrenceIds = list(link?.occurrenceIds || link?.occurrence_ids).map(String);
    const entry = unresolvedByCandidate.get(candidateId) || {
      candidateId,
      occurrenceIds: new Set(),
      candidateWide: false,
    };
    if (occurrenceIds.length) occurrenceIds.forEach((occurrenceId) => entry.occurrenceIds.add(occurrenceId));
    else entry.candidateWide = true;
    unresolvedByCandidate.set(candidateId, entry);
  }

  for (const entry of unresolvedByCandidate.values()) {
    const existingIndex = retained.findIndex((link) =>
      text(link?.candidateId || link?.candidate_id) === entry.candidateId
      && text(link?.state) === "unsure"
      && !text(link?.personId || link?.person_id)
    );
    if (existingIndex >= 0) {
      const existing = retained[existingIndex];
      const existingOccurrences = list(existing?.occurrenceIds || existing?.occurrence_ids).map(String);
      const merged = { ...existing };
      if (entry.candidateWide || !existingOccurrences.length) {
        delete merged.occurrenceIds;
        delete merged.occurrence_ids;
      } else {
        merged.occurrenceIds = unique([...existingOccurrences, ...entry.occurrenceIds]);
        delete merged.occurrence_ids;
      }
      retained[existingIndex] = merged;
      continue;
    }
    retained.push({
      candidateId: entry.candidateId,
      state: "unsure",
      displayName: "Person removed; review again",
      ...(!entry.candidateWide && entry.occurrenceIds.size
        ? { occurrenceIds: [...entry.occurrenceIds] }
        : {}),
    });
  }
  return retained;
}

export function identityLinksWithFalsePositiveDecision(
  links = [],
  candidateId = "",
  occurrenceIds = [],
  { priorPersonId = "", replaceExistingDismissal = false } = {}
) {
  const normalizedCandidateId = text(candidateId);
  if (!normalizedCandidateId) return [...list(links)];
  const selected = new Set(list(occurrenceIds).filter(Boolean).map(String));
  let candidateWide = selected.size === 0;
  const retained = [];
  let priorWasConfirmed = false;
  let priorConfirmedOccurrencesRemain = false;

  for (const link of list(links)) {
    if (text(link?.candidateId || link?.candidate_id) !== normalizedCandidateId) {
      retained.push(link);
      continue;
    }
    const personId = text(link?.personId || link?.person_id);
    const state = text(link?.state, "confirmed");
    if (priorPersonId && personId === String(priorPersonId)) {
      if (state === "confirmed") {
        priorWasConfirmed = true;
        const priorOccurrences = list(link?.occurrenceIds || link?.occurrence_ids).map(String);
        const remainingOccurrences = priorOccurrences.filter((occurrenceId) => !selected.has(occurrenceId));
        if (remainingOccurrences.length) {
          priorConfirmedOccurrencesRemain = true;
          const retainedLink = {
            ...link,
            occurrenceIds: remainingOccurrences,
          };
          delete retainedLink.occurrence_ids;
          retained.push(retainedLink);
        }
      }
      continue;
    }
    if (!personId && state === "unsure") continue;
    if (!personId && state === "rejected") {
      if (!replaceExistingDismissal) {
        const priorOccurrences = list(link?.occurrenceIds || link?.occurrence_ids).map(String);
        if (!priorOccurrences.length) candidateWide = true;
        else priorOccurrences.forEach((occurrenceId) => selected.add(occurrenceId));
      }
      continue;
    }
    retained.push(link);
  }

  if (priorPersonId && priorWasConfirmed && !priorConfirmedOccurrencesRemain) {
    retained.push({
      candidateId: normalizedCandidateId,
      personId: String(priorPersonId),
      state: "rejected",
    });
  }
  retained.push({
    candidateId: normalizedCandidateId,
    state: "rejected",
    displayName: "False detection",
    ...(!candidateWide && selected.size ? { occurrenceIds: [...selected] } : {}),
  });
  return retained;
}

function linkWithOccurrenceIds(link, occurrenceIds) {
  const result = { ...link, occurrenceIds: [...occurrenceIds] };
  delete result.occurrence_ids;
  return result;
}

function subtractOccurrenceIds(link, selected, candidateOccurrenceIds = []) {
  const linked = list(link?.occurrenceIds || link?.occurrence_ids).map(String);
  const scoped = linked.length ? linked : list(candidateOccurrenceIds).map(String);
  return scoped.filter((occurrenceId) => !selected.has(occurrenceId));
}

export function identityLinksWithConfirmedDecision(
  links = [],
  candidateId = "",
  {
    personId = "",
    displayName = "",
    occurrenceIds = [],
    priorPersonId = "",
    preservePriorUnselected = false,
    candidateOccurrenceIds = [],
  } = {}
) {
  const normalizedCandidateId = text(candidateId);
  const normalizedPersonId = text(personId);
  if (!normalizedCandidateId || !normalizedPersonId) return [...list(links)];
  const selected = new Set(list(occurrenceIds).filter(Boolean).map(String));
  const retained = [];
  let priorWasConfirmed = false;
  let priorConfirmedOccurrencesRemain = false;

  for (const link of list(links)) {
    if (text(link?.candidateId || link?.candidate_id) !== normalizedCandidateId) {
      retained.push(link);
      continue;
    }
    const linkedPersonId = text(link?.personId || link?.person_id);
    const state = text(link?.state, "confirmed");
    if (linkedPersonId === normalizedPersonId) continue;
    if (priorPersonId && linkedPersonId === String(priorPersonId)) {
      if (preservePriorUnselected && state === "confirmed") {
        priorWasConfirmed = true;
        const remaining = subtractOccurrenceIds(link, selected, candidateOccurrenceIds);
        if (remaining.length) {
          priorConfirmedOccurrencesRemain = true;
          retained.push(linkWithOccurrenceIds(link, remaining));
        }
      } else if (preservePriorUnselected && state === "unsure") {
        const remaining = subtractOccurrenceIds(link, selected, candidateOccurrenceIds);
        if (remaining.length) retained.push(linkWithOccurrenceIds(link, remaining));
      }
      continue;
    }
    if (!linkedPersonId && state === "unsure") continue;
    if (!linkedPersonId && state === "rejected") {
      const remaining = subtractOccurrenceIds(link, selected, candidateOccurrenceIds);
      if (remaining.length) retained.push(linkWithOccurrenceIds(link, remaining));
      continue;
    }
    retained.push(link);
  }

  if (
    preservePriorUnselected
    && priorPersonId
    && priorWasConfirmed
    && !priorConfirmedOccurrencesRemain
  ) {
    retained.push({
      candidateId: normalizedCandidateId,
      personId: String(priorPersonId),
      state: "rejected",
    });
  }
  retained.push({
    candidateId: normalizedCandidateId,
    personId: normalizedPersonId,
    state: "confirmed",
    displayName: text(displayName),
    ...(selected.size ? { occurrenceIds: [...selected] } : {}),
  });
  return retained;
}

export function identityLinksWithUnresolvedDecision(
  links = [],
  candidateId = "",
  occurrenceIds = [],
  {
    priorPersonId = "",
    displayName = "",
    candidateOccurrenceIds = [],
  } = {}
) {
  const normalizedCandidateId = text(candidateId);
  if (!normalizedCandidateId) return [...list(links)];
  const selected = new Set(list(occurrenceIds).filter(Boolean).map(String));
  const retained = [];
  let candidateWideUnresolved = selected.size === 0;

  for (const link of list(links)) {
    if (text(link?.candidateId || link?.candidate_id) !== normalizedCandidateId) {
      retained.push(link);
      continue;
    }
    const linkedPersonId = text(link?.personId || link?.person_id);
    const state = text(link?.state, "confirmed");
    if (priorPersonId && linkedPersonId === String(priorPersonId)) {
      if (state === "confirmed") {
        const remaining = subtractOccurrenceIds(link, selected, candidateOccurrenceIds);
        if (remaining.length) retained.push(linkWithOccurrenceIds(link, remaining));
      } else if (state === "unsure") {
        const existing = list(link?.occurrenceIds || link?.occurrence_ids).map(String);
        if (!existing.length) candidateWideUnresolved = true;
        else existing.forEach((occurrenceId) => selected.add(occurrenceId));
      }
      continue;
    }
    if (!linkedPersonId && state === "unsure") {
      if (priorPersonId) {
        retained.push(link);
      } else {
        const existing = list(link?.occurrenceIds || link?.occurrence_ids).map(String);
        if (!existing.length) candidateWideUnresolved = true;
        else existing.forEach((occurrenceId) => selected.add(occurrenceId));
      }
      continue;
    }
    if (!linkedPersonId && state === "rejected") {
      const remaining = subtractOccurrenceIds(link, selected, candidateOccurrenceIds);
      if (remaining.length) retained.push(linkWithOccurrenceIds(link, remaining));
      continue;
    }
    retained.push(link);
  }

  retained.push({
    candidateId: normalizedCandidateId,
    ...(priorPersonId ? { personId: String(priorPersonId) } : {}),
    displayName: text(displayName),
    state: "unsure",
    ...(!candidateWideUnresolved && selected.size ? { occurrenceIds: [...selected] } : {}),
  });
  return retained;
}

export function candidateOccurrences(candidate, identity) {
  if (Array.isArray(candidate?.occurrences)) return candidate.occurrences;
  const wanted = new Set(candidate?.occurrenceIds || []);
  return list(identity?.occurrences).filter((occurrence) =>
    occurrence.candidateId === candidate?.candidateId || wanted.has(occurrence.occurrenceId)
  );
}

export function identityPresentationGroups(identity = {}) {
  const ranked = [...list(identity?.candidates)].sort((left, right) => {
    const occurrenceDelta = candidateOccurrences(right, identity).length - candidateOccurrences(left, identity).length;
    if (occurrenceDelta) return occurrenceDelta;
    return text(left?.candidateId).localeCompare(text(right?.candidateId));
  });
  const recurring = [];
  const supporting = [];
  const oneOff = [];
  for (const candidate of ranked) {
    const count = candidateOccurrences(candidate, identity).length;
    if (count >= 4) recurring.push(candidate);
    else if (count === 3) supporting.push(candidate);
    else oneOff.push(candidate);
  }
  return {
    recurring,
    supporting,
    oneOff,
    primary: [...recurring, ...supporting],
  };
}

export function aggregateIdentityIssues(issues = []) {
  const groups = new Map();
  for (const issue of list(issues)) {
    const code = text(issue?.code || issue?.issueCode || issue?.issue_code, "analysis_note");
    const title = text(issue?.title || issue?.label || issue?.message, "Review identity");
    const key = `${code}\u0000${title}`;
    if (!groups.has(key)) {
      groups.set(key, {
        code,
        title,
        description: text(issue?.description || issue?.message || issue?.detail),
        count: 0,
      });
    }
    groups.get(key).count += 1;
  }
  return [...groups.values()].map((group) => {
    if (group.code === "ambiguous_identity") {
      return {
        ...group,
        title: `${group.count} ${group.count === 1 ? "appearance needs" : "appearances need"} comparison`,
        description: "These appearances were close to more than one likely person. Review them on the relevant person card.",
      };
    }
    if (group.code === "no_face_detected") {
      return {
        ...group,
        title: `${group.count} visual ${group.count === 1 ? "source needs" : "sources need"} manual person review`,
        description: "No clear face was found, but body, silhouette, masked, distant, or other performance may still require rights review.",
      };
    }
    if (group.count === 1) return group;
    return { ...group, title: `${group.count} × ${group.title}` };
  });
}

export function groupOccurrencesBySource(candidate, identity) {
  const groups = new Map();
  for (const occurrence of candidateOccurrences(candidate, identity)) {
    const key = occurrence.sourceRef || occurrence.sourceLabel || "unresolved-source";
    if (!groups.has(key)) groups.set(key, {
      sourceRef: occurrence.sourceRef,
      label: occurrence.sourceLabel || "Source",
      occurrences: [],
    });
    groups.get(key).occurrences.push(occurrence);
  }
  return [...groups.values()];
}

export function visualGroupingLabel(candidate = {}, identity = {}) {
  const band = text(candidate?.groupingBand || candidate?.grouping_band).toLowerCase();
  if (/^strong/.test(band)) return "Strong visual grouping";
  if (/^likely|^moderate/.test(band)) return "Likely visual grouping";
  if (/^mixed|^weak|^review|^uncertain/.test(band)) return "Mixed visual grouping";
  const hasAmbiguity = candidateOccurrences(candidate, identity).some((occurrence) => occurrence.ambiguous)
    || list(identity?.issues).some((issue) => (issue.candidateId || issue.candidate_id) === candidate.candidateId);
  if (hasAmbiguity) return "Mixed visual grouping";
  // Raw face-model cosine scores are not calibrated probabilities. Until the
  // backend supplies a calibrated band, never invent precision from the float.
  return "Mixed visual grouping";
}

export function candidateNeedsReview(candidate, identity) {
  if (candidate?.needsReview) return true;
  if (["needs_review", "review_required", "ambiguous", "unresolved"].includes(candidate?.state)) return true;
  if (candidateOccurrences(candidate, identity).some((occurrence) => occurrence.ambiguous)) return true;
  return list(identity?.issues).some((issue) => issue.candidateId === candidate?.candidateId);
}

const USE_COPY = new Map([
  ["digital_replica:generate", "generate from an identity model"],
  ["face:generate", "guide or edit imagery with their likeness"],
  ["face:edit", "guide or edit imagery with their likeness"],
  ["biometric_input:process", "guide or edit imagery with their likeness"],
  ["synthetic_performance:render", "animate their likeness into video"],
  ["synthetic_performance:edit", "restyle their recorded performance"],
  ["full_body_performance:process", "use or transform their recorded performance"],
  ["full_body_performance:edit", "use or transform their recorded performance"],
  ["voice:process", "use or transform their voice or performance audio"],
  ["nil:generate", "generate a prompted likeness"],
]);

export function plainLanguageUses(candidate, sources = []) {
  const sourceRefs = new Set(candidate?.sourceRefs || []);
  const operations = sources
    .filter((source) => !sourceRefs.size || sourceRefs.has(source.sourceRef || source.source_ref))
    .flatMap((source) => source.ops || []);
  const uses = operations.flatMap((operation) => {
    const classType = operation.class_type || operation.classType || "";
    const sourceRole = operation.source_role || operation.sourceRole || "";
    return operationActions(classType, sourceRole)
      .map((action) => USE_COPY.get(`${action.modality}:${action.action}`))
      .filter(Boolean);
  });
  return unique(uses);
}

export function plainLanguageUseSummary(candidate, sources = []) {
  const uses = plainLanguageUses(candidate, sources);
  if (!uses.length) return "Appears in AI-assisted media in this workflow.";
  if (uses.length === 1) return `This workflow may ${uses[0]}.`;
  return `This workflow may ${uses.slice(0, -1).join(", ")}, and ${uses.at(-1)}.`;
}

export function coverageLabel(coverage = {}) {
  if (!coverage.total) return "Media analysis coverage is not available yet.";
  const noun = coverage.total === 1 ? "source" : "sources";
  return `${coverage.analyzed} of ${coverage.total} ${noun} analyzed${coverage.skipped ? ` · ${coverage.skipped} skipped` : ""}`;
}
