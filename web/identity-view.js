import { saveLocalPersonDraft, updateProjectPerson } from "./api.js";
import { button, el, metaLabel, pluribusMark, toast } from "./components.js";
import {
  aggregateIdentityIssues,
  candidateOccurrences,
  coverageLabel,
  groupOccurrencesBySource,
  identityLinksWithConfirmedDecision,
  identityManualReviewItems,
  identityPresentationGroups,
  identityLinksWithFalsePositiveDecision,
  identityLinksWithUnresolvedDecision,
  plainLanguageUseSummary,
  visualGroupingLabel,
} from "./identity-contract.js";
import {
  analyzeWorkflowIdentity,
  cancelIdentityAnalysis,
  commitIdentityLinks,
  identityRevisionConflict,
  installLocalIdentityModels,
  refreshIdentityLinks,
} from "./identity-analysis.js";
import { draftPersonCards, loadPersonDrafts } from "./person-drafts.js";
import { sourceDisplayLabel, sourceMedia, sourceRecordsForScan } from "./source-records.js";
import { getState, projectPeople } from "./store.js";

const ONE_OFF_PAGE_SIZE = 24;

export function mergeIdentityDraftSourceRefs(
  existingSourceRefs = [],
  candidateSourceRefs = [],
  selectedSourceRefs = [],
  preserveOutsideCandidate = true
) {
  const candidateScope = new Set((candidateSourceRefs || []).filter(Boolean).map(String));
  const preserved = preserveOutsideCandidate
    ? (existingSourceRefs || []).filter((sourceRef) => !candidateScope.has(String(sourceRef)))
    : [];
  return [...new Set([...preserved, ...(selectedSourceRefs || [])].filter(Boolean).map(String))];
}

export function sourceLinkOverridesForExistingPerson(
  candidateSourceRefs = [],
  selectedSourceRefs = [],
  personId = ""
) {
  const canonicalPersonId = String(personId || "");
  if (!canonicalPersonId) return new Map();
  const selected = new Set((selectedSourceRefs || []).filter(Boolean).map(String));
  return new Map(
    [...new Set((candidateSourceRefs || []).filter(Boolean).map(String))]
      .sort()
      .map((sourceRef) => [sourceRef, selected.has(sourceRef)
        ? { addTalentRecordIds: [canonicalPersonId] }
        : { removeTalentRecordIds: [canonicalPersonId] }])
  );
}

function candidateName(candidate, index = 0) {
  return candidate.suggestedName || `Likely person ${String(index + 1).padStart(2, "0")}`;
}

export function candidateRoleLabel(linkedPeople = [], draft = null, candidate = {}) {
  if (linkedPeople.length > 1) return "Split appearance group";
  return draft?.role || candidate?.suggestedRole || "";
}

export function identitySuggestionProvenance(candidate = {}) {
  const hasSuggestion = Boolean(candidate.suggestedName || candidate.suggestedRole);
  if (!hasSuggestion) {
    return {
      label: "Unresolved person",
      badge: "No working-name suggestion",
      description: "Visual analysis grouped these appearances, but did not infer a name.",
    };
  }
  const source = String(candidate.suggestionSource || candidate.suggestion_source || "").toLowerCase();
  if (source === "source_label") {
    return {
      label: "Source-label suggestion",
      badge: "Name from source label",
      description: "The working name and role came from a filename or project asset label. Visual analysis only grouped the appearances.",
    };
  }
  return {
    label: "Project-metadata suggestion",
    badge: "Name from project metadata",
    description: "The working name or role came from project metadata. Visual analysis only grouped the appearances.",
  };
}

function normalizedRepresentative(person = {}, fallback = {}) {
  const direct = person?.representative;
  if (direct && typeof direct === "object") {
    return {
      role: direct.role || fallback?.role || "manager",
      name: direct.name || fallback?.name || "",
      email: direct.email || fallback?.email || "",
    };
  }
  const name = person?.representativeName || person?.representative_name || fallback?.name || "";
  const email = person?.representativeEmail || person?.representative_email || fallback?.email || "";
  if (!name && !email && !fallback?.role) return undefined;
  return {
    role: person?.representativeRole || person?.representative_role || fallback?.role || "manager",
    name,
    email,
  };
}

function existingPersonChoice(person = {}, draft = null) {
  const canonicalPersonId = String(
    person?.id
    || person?.talentRecordId
    || person?.talent_record_id
    || draft?.canonicalPersonId
    || ""
  );
  const draftId = String(draft?.draftId || "");
  const personId = canonicalPersonId || draftId;
  if (!personId) return null;
  const representative = normalizedRepresentative(
    person,
    draft?.representative || {}
  );
  return {
    choiceId: `${canonicalPersonId ? "project" : "local"}:${personId}`,
    personId,
    canonicalPersonId,
    draftId,
    displayName: person?.displayName || person?.name || draft?.displayName || "Unnamed person",
    role: person?.role || draft?.role || "",
    talentEmail: person?.talentEmail || person?.talent_email || draft?.talentEmail || "",
    representative,
    notes: draft?.notes || "",
    sourceRefs: [...(draft?.sourceRefs || [])],
    draft,
    scope: canonicalPersonId ? "project" : "workflow",
  };
}

export function existingIdentityChoices(personDrafts = [], canonicalPeople = []) {
  const drafts = Array.isArray(personDrafts) ? personDrafts : [];
  const people = Array.isArray(canonicalPeople) ? canonicalPeople : [];
  const draftByCanonicalId = new Map(
    drafts
      .filter((draft) => draft?.canonicalPersonId)
      .map((draft) => [String(draft.canonicalPersonId), draft])
  );
  const consumedDraftIds = new Set();
  const seenPersonIds = new Set();
  const choices = [];

  for (const person of people) {
    const canonicalPersonId = String(
      person?.id || person?.talentRecordId || person?.talent_record_id || ""
    );
    if (!canonicalPersonId || seenPersonIds.has(canonicalPersonId)) continue;
    const draft = draftByCanonicalId.get(canonicalPersonId) || null;
    const choice = existingPersonChoice(person, draft);
    if (!choice) continue;
    choices.push(choice);
    seenPersonIds.add(choice.personId);
    if (draft?.draftId) consumedDraftIds.add(String(draft.draftId));
  }

  for (const draft of drafts) {
    const draftId = String(draft?.draftId || "");
    if (!draftId || consumedDraftIds.has(draftId)) continue;
    const choice = existingPersonChoice({}, draft);
    if (!choice || seenPersonIds.has(choice.personId)) continue;
    choices.push(choice);
    seenPersonIds.add(choice.personId);
  }

  return choices.sort((left, right) =>
    left.displayName.localeCompare(right.displayName, undefined, { sensitivity: "base" })
      || left.personId.localeCompare(right.personId)
  );
}

export function existingIdentityChoiceForId(choices = [], personId = "") {
  const wanted = String(personId || "");
  if (!wanted) return null;
  return (choices || []).find((choice) =>
    [choice.personId, choice.canonicalPersonId, choice.draftId]
      .filter(Boolean)
      .map(String)
      .includes(wanted)
  ) || null;
}

export function representativeOccurrences(occurrences = [], limit = 3) {
  const values = Array.isArray(occurrences) ? occurrences : [];
  const maximum = Math.max(0, Math.floor(Number(limit) || 0));
  if (!maximum || !values.length) return [];
  if (values.length <= maximum) return [...values];
  if (maximum === 1) return [values[0]];
  const indexes = new Set();
  for (let index = 0; index < maximum; index += 1) {
    indexes.add(Math.round((index * (values.length - 1)) / (maximum - 1)));
  }
  return [...indexes].map((index) => values[index]);
}

export function filmstripColumns(count, layout = "card") {
  const total = Math.max(1, Math.floor(Number(count) || 1));
  if (total === 1) return "minmax(0, 1fr)";
  if (total === 2) return "1.25fr minmax(0, 1fr)";
  if (layout === "review") return "1.5fr repeat(2, minmax(0, 0.75fr))";
  if (total === 3) return "1.3fr repeat(2, minmax(0, 1fr))";
  if (total === 4) return "1.3fr repeat(3, minmax(0, 1fr))";
  return "1.3fr repeat(4, minmax(0, 1fr))";
}

function sourcesForState(state = getState()) {
  return sourceRecordsForScan(state.scan?.persons || [], state.sourceRefs);
}

function sourceForRef(sourceRef, state = getState()) {
  return sourcesForState(state).find((source) => source.sourceRef === sourceRef) || null;
}

export function identityLinksForCandidate(candidate, state = getState()) {
  return (state.identityLinks || []).filter((link) =>
    (link.candidateId || link.candidate_id) === candidate.candidateId
  );
}

export function identityLinkForCandidate(candidate, state = getState(), personId = "") {
  const links = identityLinksForCandidate(candidate, state);
  if (!personId) return links[0] || null;
  return links.find((link) =>
    (link.personId || link.person_id) === personId
  ) || null;
}

function linkedOccurrenceIds(link) {
  const values = link?.occurrenceIds || link?.occurrence_ids;
  return new Set(Array.isArray(values) ? values.map(String) : []);
}

function confirmedOccurrenceIds(candidate, state = getState(), exceptPersonId = "") {
  const selected = new Set();
  for (const link of identityLinksForCandidate(candidate, state)) {
    const personId = String(link.personId || link.person_id || "");
    if (link.state !== "confirmed" || (exceptPersonId && personId === exceptPersonId)) continue;
    for (const occurrenceId of linkedOccurrenceIds(link)) selected.add(occurrenceId);
  }
  return selected;
}

export function candidateDismissedOccurrenceIds(candidate, identity, state = getState()) {
  const occurrences = candidateOccurrences(candidate, identity);
  const allOccurrenceIds = new Set(occurrences.map((occurrence) => String(occurrence.occurrenceId)));
  const dismissed = new Set();
  for (const link of identityLinksForCandidate(candidate, state)) {
    const personId = String(link.personId || link.person_id || "");
    if (link.state !== "rejected" || personId) continue;
    const selected = linkedOccurrenceIds(link);
    if (!selected.size) return allOccurrenceIds;
    for (const occurrenceId of selected) {
      if (allOccurrenceIds.has(occurrenceId)) dismissed.add(occurrenceId);
    }
  }
  return dismissed;
}

export function draftForCandidate(candidate, state = getState(), personId = "") {
  const links = identityLinksForCandidate(candidate, state);
  const explicit = personId
    ? identityLinkForCandidate(candidate, state, personId)
    : links.length === 1
      ? links[0]
      : null;
  if (explicit?.state === "confirmed") {
    const personId = explicit.personId || explicit.person_id;
    return (state.personDrafts || []).find((draft) => draft.draftId === personId || draft.canonicalPersonId === personId) || null;
  }
  if (explicit) return null;
  // Any candidate-specific link, including a rejected tombstone, means the
  // producer has used the new visual-review flow. Never revive a legacy
  // source-overlap guess after an explicit correction.
  if (links.length) return null;

  // Backward compatibility for drafts created before candidate links existed.
  // A source-only match is safe only when no other candidate shares that source.
  const wanted = new Set(candidate.sourceRefs || []);
  const shared = (state.identityPayload?.candidates || []).some((other) =>
    other.candidateId !== candidate.candidateId && (other.sourceRefs || []).some((sourceRef) => wanted.has(sourceRef))
  );
  if (shared) return null;
  let best = null;
  let bestOverlap = 0;
  for (const draft of state.personDrafts || []) {
    const overlap = (draft.sourceRefs || []).filter((sourceRef) => wanted.has(sourceRef)).length;
    if (overlap > bestOverlap) {
      best = draft;
      bestOverlap = overlap;
    }
  }
  return best;
}

export function candidateIsFullyConfirmed(candidate, identity, state = getState()) {
  const explicit = identityLinksForCandidate(candidate, state).filter((link) => link.state === "confirmed");
  if (!explicit.length) {
    // A source-level draft can predate visual review. It may supply a name, but
    // it must never silently confirm every face found in that source.
    return candidateOccurrences(candidate, identity).length === 0 && Boolean(draftForCandidate(candidate, state));
  }
  const occurrences = candidateOccurrences(candidate, identity);
  if (!occurrences.length) return true;
  const selected = confirmedOccurrenceIds(candidate, state);
  if (!selected.size) return false;
  return occurrences.every((occurrence) => selected.has(String(occurrence.occurrenceId)));
}

export function candidateIsResolved(candidate, identity, state = getState()) {
  const occurrences = candidateOccurrences(candidate, identity);
  if (!occurrences.length) {
    return candidateIsFullyConfirmed(candidate, identity, state)
      || identityLinksForCandidate(candidate, state).some((link) =>
        link.state === "rejected" && !(link.personId || link.person_id)
      );
  }
  const resolved = confirmedOccurrenceIds(candidate, state);
  for (const occurrenceId of candidateDismissedOccurrenceIds(candidate, identity, state)) {
    resolved.add(occurrenceId);
  }
  return occurrences.every((occurrence) => resolved.has(String(occurrence.occurrenceId)));
}

export function candidateHasActiveOccurrencesForSource(
  candidate,
  identity,
  sourceRef,
  state = getState()
) {
  const occurrences = candidateOccurrences(candidate, identity)
    .filter((occurrence) => occurrence.sourceRef === sourceRef);
  const dismissed = candidateDismissedOccurrenceIds(candidate, identity, state);
  if (occurrences.length) {
    return occurrences.some((occurrence) => !dismissed.has(String(occurrence.occurrenceId)));
  }
  return !identityLinksForCandidate(candidate, state).some((link) =>
    link.state === "rejected"
    && !(link.personId || link.person_id)
    && !(link.occurrenceIds || link.occurrence_ids)?.length
  );
}

export function candidateUnresolvedCount(candidate, identity, state = getState()) {
  const occurrences = candidateOccurrences(candidate, identity);
  if (!occurrences.length || candidateIsResolved(candidate, identity, state)) return 0;
  const resolved = confirmedOccurrenceIds(candidate, state);
  for (const occurrenceId of candidateDismissedOccurrenceIds(candidate, identity, state)) {
    resolved.add(occurrenceId);
  }
  return occurrences.filter((occurrence) => !resolved.has(String(occurrence.occurrenceId))).length;
}

function candidateIssues(candidate, identity) {
  return (identity?.issues || []).filter((issue) => issue.candidateId === candidate.candidateId);
}

function occurrenceAlt(occurrence, name) {
  const context = [occurrence.sceneLabel, occurrence.timecode].filter(Boolean).join(", ");
  return context ? `${name} appearance from ${context}` : `${name} appearance`;
}

function occurrenceCrop(occurrence, name, size = "standard") {
  const frame = el("div", { class: `plb-occurrence-crop plb-occurrence-crop--${size}` });
  const url = occurrence?.cropUrl || occurrence?.frameUrl;
  if (url) {
    const image = el("img", {
      src: url,
      alt: occurrenceAlt(occurrence, name),
      loading: "lazy",
      decoding: "async",
    });
    image.addEventListener("error", () => image.remove());
    frame.append(image);
  } else {
    const source = sourceForRef(occurrence?.sourceRef);
    const media = sourceMedia(source);
    if (media?.kind === "image") {
      const image = el("img", {
        src: media.url,
        alt: occurrenceAlt(occurrence || {}, name),
        loading: "lazy",
      });
      image.addEventListener("error", () => image.remove());
      frame.append(image);
    }
  }
  frame.append(el("span", { class: "plb-occurrence-placeholder", text: "Portrait pending" }));
  return frame;
}

function filmstrip(candidate, identity, limit = 5, layout = "card") {
  const name = candidateName(candidate);
  const occurrences = candidateOccurrences(candidate, identity).slice(0, limit);
  if (!occurrences.length) {
    return el(
      "div",
      { class: "plb-filmstrip plb-filmstrip--empty" },
      el("span", { text: "Portrait crops will appear when local analysis finishes." })
    );
  }
  const strip = el(
    "div",
    { class: "plb-filmstrip", "aria-label": `${name} appearance filmstrip`, "data-count": String(occurrences.length) },
    occurrences.map((occurrence, index) => occurrenceCrop(occurrence, name, index === 0 ? "hero" : "standard"))
  );
  strip.style.gridTemplateColumns = filmstripColumns(occurrences.length, layout);
  return strip;
}

function evidenceSheets(candidate, name) {
  if (!candidate?.evidenceImages?.length) return null;
  return el(
    "details",
    { class: "plb-evidence-sheets" },
    el("summary", { text: `${candidate.evidenceImages.length} supporting evidence ${candidate.evidenceImages.length === 1 ? "sheet" : "sheets"}` }),
    el(
      "div",
      { class: "plb-evidence-sheet-grid" },
      candidate.evidenceImages.map((url, index) => {
        const image = el("img", { src: url, alt: `${name} supporting evidence sheet ${index + 1}`, loading: "lazy" });
        image.addEventListener("error", () => image.remove());
        return image;
      })
    )
  );
}

function retryAnalysis() {
  const state = getState();
  if (!state.scan || !state.workflow || !state.workflowBinding) return;
  void analyzeWorkflowIdentity({
    workflowName: state.scan.workflow_name || "",
    workflowFingerprint: state.scan.workflow_fingerprint || "",
    workflowBinding: state.workflowBinding,
    scan: state.scan,
  });
}

export function progressValue(job) {
  const progress = job?.progress;
  if (progress && typeof progress === "object") {
    const completed = Number(progress.completed || 0);
    const total = Number(progress.total || 0);
    const sampledFrames = Number(progress.sampledFrames || 0);
    const sampledFrameTotal = Number(progress.sampledFrameTotal || 0);
    const frameFraction = sampledFrameTotal > 0
      ? Math.max(0, Math.min(1, sampledFrames / sampledFrameTotal))
      : 0;
    return total > 0
      ? Math.max(0, Math.min(100, ((completed + frameFraction) / total) * 100))
      : 0;
  }
  const value = Number(progress ?? job?.percent ?? job?.progressPercent ?? job?.progress_percent ?? 0);
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value <= 1 ? value * 100 : value));
}

export function progressDetailLabel(job) {
  const progress = job?.progress;
  if (!progress || typeof progress !== "object") return "";
  const sampledFrames = Number(progress.sampledFrames || 0);
  const sampledFrameTotal = Number(progress.sampledFrameTotal || 0);
  if (sampledFrameTotal > 0) {
    return `Sampled frame ${Math.min(sampledFrames, sampledFrameTotal)} of ${sampledFrameTotal}`;
  }
  const completed = Number(progress.completed || 0);
  const total = Number(progress.total || 0);
  return total > 0 ? `${Math.min(completed, total)} of ${total} sources` : "";
}

export function progressPhaseLabel(job) {
  const phase = String(job?.progress?.phase || job?.phase || "").toLowerCase();
  if (phase === "reading_media") return "Reading media";
  if (phase === "grouping_people") return "Grouping likely people";
  if (phase === "building_evidence") return "Building visual evidence";
  if (phase === "queued") return "Preparing local analysis";
  return "Analyzing locally";
}

function capabilityCard(state) {
  const capabilities = state.identityCapabilities;
  if (!capabilities && state.identityCapabilitiesLoading) {
    return el(
      "section",
      { class: "plb-analysis-progress", role: "status", "aria-live": "polite" },
      el("div", { class: "plb-analysis-orbit", "aria-hidden": "true" }, pluribusMark(20)),
      el("div", { class: "plb-analysis-copy" }, el("strong", { text: "Checking local people intelligence" }), el("span", { text: "Nothing is uploaded." }))
    );
  }
  if (!capabilities) return null;
  if (capabilities.state === "ready" && capabilities.mediaRootsConfigured !== false) return null;

  const bundle = capabilities.modelBundle || {};
  const analyzer = capabilities.analyzer || {};
  const dependencyIssue = (analyzer.issues || []).find((issue) => issue.code === "dependency_unavailable");
  if (dependencyIssue) {
    const packages = dependencyIssue.action?.packages || ["opencv-python-headless>=4.8,<5", "numpy>=1.24,<3"];
    const installCommand = `python -m pip install ${packages.map((name) => `'${String(name).replaceAll("'", "'\\''")}'`).join(" ")}`;
    return el(
      "section",
      { class: "plb-soft-alert", role: "status" },
      el(
        "div",
        {},
        el("strong", { text: "Local Python setup required" }),
        el("p", { text: "Install the identity runtime in the same Python environment that launches ComfyUI, then restart ComfyUI. Pluribus will offer the verified portrait models afterward." }),
        el("code", { class: "plb-setup-command", text: installCommand })
      )
    );
  }
  if (!bundle.installed) {
    const maximumBytes = (bundle.files || []).reduce((total, file) => total + Number(file.downloadBytesMaximum || 0), 0);
    const install = button(state.identityModelsInstalling ? "Installing…" : "Install local models", "primary", async () => {
      install.disabled = true;
      try {
        const updated = await installLocalIdentityModels();
        if (updated?.state === "ready") retryAnalysis();
        else toast("Models installed, but the local analyzer still needs attention.");
      } catch (error) {
        toast(error.message || "Could not install the local identity models.");
      }
    });
    install.disabled = state.identityModelsInstalling;
    return el(
      "section",
      { class: "plb-capability-card", role: "status" },
      el(
        "div",
        { class: "plb-capability-icon", "aria-hidden": "true" },
        el("span", { text: "AI" })
      ),
      el(
        "div",
        { class: "plb-capability-copy" },
        metaLabel("Optional local intelligence", true),
        el("strong", { text: "Turn sources into likely people" }),
        el("p", { text: "Install the verified portrait models to create face crops and group recurring appearances. Media and embeddings stay on this machine." }),
        el("small", { text: `${maximumBytes ? `Up to ${Math.ceil(maximumBytes / 1_000_000)} MB · ` : ""}Images and video · producer confirmation required` })
      ),
      install
    );
  }

  const message = capabilities.mediaRootsConfigured === false
    ? "ComfyUI media roots are not configured for local analysis. Source lineage remains available."
    : analyzer.issues?.[0]?.description || analyzer.issues?.[0]?.message || "The local analyzer is unavailable. Source lineage remains available.";
  return el(
    "section",
    { class: "plb-soft-alert", role: "status" },
    el("div", {}, el("strong", { text: "Visual identity analysis unavailable" }), el("p", { text: message }))
  );
}

function analysisProgress(state) {
  const progress = progressValue(state.identityJob);
  const progressBar = el(
    "div",
    {
      class: "plb-analysis-progressbar",
      role: "progressbar",
      "aria-label": "Local identity analysis progress",
      "aria-valuemin": "0",
      "aria-valuemax": "100",
      "aria-valuenow": String(Math.round(progress)),
    },
    el("span", {})
  );
  progressBar.firstElementChild.style.width = `${progress || 8}%`;
  return el(
    "section",
    { class: "plb-analysis-progress", "aria-live": "polite" },
    el("div", { class: "plb-analysis-orbit", "aria-hidden": "true" }, pluribusMark(20)),
    el(
      "div",
      { class: "plb-analysis-copy" },
      el("strong", { text: progressPhaseLabel(state.identityJob) }),
      el("span", {
        text: progressDetailLabel(state.identityJob)
          || "Portrait crops and recurring appearances stay on this machine. You can keep working.",
      }),
      progressBar
    ),
    button("Cancel", "ghost", () => void cancelIdentityAnalysis({ remove: true }))
  );
}

export function renderIdentityOverview(container, { openPeople, openSources, openUse, rescan } = {}) {
  const state = getState();
  if (!state.scan) {
    container.replaceChildren(
      el(
        "div",
        { class: "plb-empty", role: state.error ? "alert" : "status", "aria-live": "polite" },
        pluribusMark(20),
        el("div", {
          text: state.error
            ? state.error.message || "The workflow scan could not finish."
            : state.scanning
              ? "Reading rights-relevant sources in this workflow…"
              : "Scan this workflow to find people and rights-relevant media.",
        }),
        !state.scanning && rescan ? button(state.error ? "Try again" : "Scan workflow", "primary", rescan) : null
      )
    );
    return;
  }

  const identity = state.identityPayload;
  const body = el("div", { class: "plb-overview-scroll" });
  const capability = capabilityCard(state);
  if (capability) body.append(capability);
  if (state.identityAnalyzing) body.append(analysisProgress(state));
  if (state.identityError) {
    body.append(
      el(
        "section",
        { class: "plb-soft-alert", role: "status" },
        el("div", {}, el("strong", { text: "Visual analysis needs attention" }), el("p", { text: state.identityError.message })),
        button("Try again", "secondary", retryAnalysis)
      )
    );
  }

  if (!identity) {
    const sourceCount = sourcesForState(state).length;
    body.append(
      el(
        "section",
        { class: "plb-overview-hero" },
        metaLabel("Workflow review", true),
        el("h2", { text: state.identityAnalyzing ? "Building a people-first view" : `${sourceCount} candidate ${sourceCount === 1 ? "source" : "sources"} found` }),
        el("p", {
          text: state.identityAnalyzing
            ? "Pluribus is turning graph inputs into portraits, likely-person groups, and a short review queue."
            : "Source lineage is ready. Run local visual analysis to group recurring people and replace the source checklist with a guided review.",
        }),
        state.identityAnalyzing ? null : button("Analyze people", "primary", retryAnalysis)
      ),
      overviewAuditCard(sourceCount, openSources)
    );
    container.replaceChildren(body);
    return;
  }

  const candidates = identity.candidates || [];
  const presentation = identityPresentationGroups(identity);
  const confirmedPersonIds = new Set(
    (state.identityLinks || [])
      .filter((link) => link.state === "confirmed")
      .map((link) => String(link.personId || link.person_id || ""))
      .filter(Boolean)
  );
  for (const draft of manualIdentityDrafts(identity, state)) {
    if (draft.draftId) confirmedPersonIds.add(String(draft.draftId));
  }
  const sourceCount = sourcesForState(state).length;
  const { review, manualSourceIssues, isClear } = identityReviewSummary(identity, state);

  body.append(
    el(
      "section",
      { class: "plb-overview-hero" },
      el(
        "div",
        { class: "plb-overview-title-row" },
        el("div", {}, metaLabel("People in this workflow", true), el("h2", { text: `${candidates.length} visual ${candidates.length === 1 ? "appearance group" : "appearance groups"}` })),
        el("span", { class: "plb-coverage-pill", text: coverageLabel(identity.coverage) })
      ),
      el("p", { text: "Pluribus grouped recurring appearances for review. One visual group can contain more than one person; identity confidence never means rights clearance." }),
      el(
        "div",
        { class: "plb-overview-metrics" },
        metric(presentation.recurring.length, "Recurring groups"),
        metric(presentation.supporting.length, "Supporting groups"),
        metric(presentation.oneOff.length, "One-off groups"),
        metric(confirmedPersonIds.size, "Identified people"),
        metric(review.length, "Groups need review", review.length ? "warn" : "ok")
      )
    )
  );

  if (manualSourceIssues.length) {
    body.append(
      el(
        "section",
        { class: "plb-soft-alert", role: "status" },
        el(
          "div",
          {},
          el("strong", { text: `${manualSourceIssues.length} ${manualSourceIssues.length === 1 ? "source needs" : "sources need"} manual person review` }),
          el("p", { text: "Identity analysis could not fully cover these inputs, or found no clear face. Review skipped, partial, body, silhouette, masked, distant, or storage-limited evidence before moving to rights." })
        ),
        button("Review sources", "secondary", openSources)
      )
    );
  }

  if (!candidates.length && manualSourceIssues.length) {
    body.append(
      el(
        "section",
        { class: "plb-soft-alert", role: "status" },
        el("div", {}, el("strong", { text: "No complete visual groups found yet" }), el("p", { text: "Review the highlighted skipped, partial, body, silhouette, masked, or distant sources before marking the workflow clear." })),
        button("Review sources", "secondary", openSources)
      )
    );
  } else if (review.length) {
    body.append(
      el(
        "section",
        { class: "plb-review-queue", "aria-labelledby": "plb-review-queue-title" },
        el(
          "div",
          { class: "plb-section-heading" },
          el("div", {}, metaLabel("Review next", true), el("h3", { id: "plb-review-queue-title", text: `${review.length} visual ${review.length === 1 ? "group" : "groups"} need review` })),
          button("See all", "ghost", openPeople)
        ),
        review.slice(0, 3).map((candidate) => reviewQueueRow(candidate, identity))
      )
    );
  } else if (isClear) {
    body.append(
      el(
        "section",
        { class: "plb-complete-banner" },
        el("span", { class: "plb-complete-check", text: "✓", "aria-hidden": "true" }),
        el("div", {}, el("strong", { text: "Identity review is clear" }), el("span", { text: "Move to use and rights when you are ready to define permission." })),
        button("Use & rights", "secondary", openUse)
      )
    );
  }

  const topCandidate = candidates[0];
  if (topCandidate) {
    body.append(
      el(
        "section",
        { class: "plb-overview-use" },
        metaLabel("What the graph is doing", true),
        el("strong", { text: "Plain-language use summary" }),
        el("p", { text: plainLanguageUseSummary({ sourceRefs: sourcesForState(state).map((source) => source.sourceRef) }, sourcesForState(state)) }),
        button("Review use & rights", "secondary", openUse)
      )
    );
  }

  if (identity.issues.length) {
    const groupedIssues = aggregateIdentityIssues(identity.issues);
    body.append(
      el(
        "details",
        { class: "plb-coverage-details" },
        el("summary", { text: `${identity.issues.length} analysis ${identity.issues.length === 1 ? "finding" : "findings"} · ${groupedIssues.length} ${groupedIssues.length === 1 ? "topic" : "topics"}` }),
        el("ul", {}, groupedIssues.map((issue) => el("li", {}, el("strong", { text: issue.title }), issue.description ? el("span", { text: issue.description }) : null)))
      )
    );
  }
  body.append(overviewAuditCard(sourceCount, openSources));
  container.replaceChildren(body);
}

export function unresolvedManualSourceIssues(identity, state = getState()) {
  const drafts = state.personDrafts || [];
  const canonicalSources = state.projectContext?.sourceLinks || state.projectContext?.sources || [];
  return identityManualReviewItems(identity).filter((issue) => {
    if (!issue.sourceRef) return true;
    const sourceRef = issue.sourceRef;
    const review = state.sourceReviews?.[sourceRef];
    const currentHash = (identity?.sourceHashes || []).find(
      (entry) => entry.sourceRef === sourceRef
    )?.sourceHash || issue.sourceHash;
    if (review?.state === "not_person" && currentHash && review.sourceHash === currentHash) return false;
    if (drafts.some((draft) => (draft.sourceRefs || []).includes(sourceRef))) return false;
    if (canonicalSources.some((source) =>
      (source.sourceRef || source.source_ref) === sourceRef
      && source.disposition === "linked"
    )) return false;
    return true;
  });
}

export function identityReviewSummary(identity, state = getState()) {
  const review = [...(identity?.candidates || [])]
    .filter((candidate) => !candidateIsResolved(candidate, identity, state))
    .sort((left, right) => candidateOccurrences(right, identity).length - candidateOccurrences(left, identity).length);
  const manualSourceIssues = unresolvedManualSourceIssues(identity, state);
  return {
    review,
    manualSourceIssues,
    isClear: review.length === 0 && manualSourceIssues.length === 0,
  };
}

export function manualIdentityDrafts(identity, state = getState()) {
  const manualSourceRefs = new Set(
    identityManualReviewItems(identity)
      .filter((issue) => issue.sourceRef)
      .map((issue) => issue.sourceRef)
  );
  const visuallyLinkedIds = new Set(
    (state.identityLinks || [])
      .filter((link) => link.state === "confirmed")
      .map((link) => String(link.personId || link.person_id || ""))
      .filter(Boolean)
  );
  return (state.personDrafts || []).filter((draft) =>
    !visuallyLinkedIds.has(String(draft.draftId || draft.canonicalPersonId || ""))
    && (draft.sourceRefs || []).some((sourceRef) => manualSourceRefs.has(sourceRef))
  );
}

function metric(value, label, tone = "") {
  return el("div", { class: `plb-overview-metric${tone ? ` plb-overview-metric--${tone}` : ""}` }, el("strong", { text: String(value) }), el("span", { text: label }));
}

function overviewAuditCard(sourceCount, openSources) {
  return el(
    "section",
    { class: "plb-audit-entry" },
    el("div", {}, metaLabel("Source audit"), el("strong", { text: `${sourceCount} graph inputs and lineage paths` }), el("span", { text: "Technical filenames, node operations, and exact source provenance remain available when you need them." })),
    button("Open sources", "secondary", openSources)
  );
}

function reviewQueueRow(candidate, identity) {
  const name = candidateName(candidate);
  const issues = candidateIssues(candidate, identity);
  const summary = issues[0]?.title || (
    visualGroupingLabel(candidate, identity) === "Mixed visual grouping"
      ? "Compare this person with nearby appearances"
      : "Confirm suggested identity"
  );
  return el(
    "button",
    {
      type: "button",
      class: "plb-review-row",
      onclick: () => openIdentityReviewDialog(candidate),
      "aria-label": `Review ${name}: ${summary}`,
    },
    occurrenceCrop(candidateOccurrences(candidate, identity)[0], name, "queue"),
    el("div", { class: "plb-review-row-copy" }, el("strong", { text: name }), el("span", { text: summary })),
    el("span", { class: "plb-grouping-label", text: visualGroupingLabel(candidate, identity) }),
    el("span", { class: "plb-row-arrow", text: "→", "aria-hidden": "true" })
  );
}

export function renderIdentityPeople(container) {
  const state = getState();
  const identity = state.identityPayload;
  if (!identity && state.identityAnalyzing) {
    container.replaceChildren(
      el("div", { class: "plb-overview-scroll" }, analysisProgress(state), el("p", { class: "plb-stage-copy", text: "Candidate portraits will appear here as soon as the local worker finishes." }))
    );
    return true;
  }
  if (!identity) return false;
  const sources = sourcesForState(state);
  const presentation = identityPresentationGroups(identity);
  const manualDrafts = manualIdentityDrafts(identity, state);
  const list = el(
    "div",
    { class: "plb-people-view" },
    el(
      "div",
      { class: "plb-people-intro" },
      el("div", {}, metaLabel("Likely people", true), el("h2", { text: "Review people, not filenames" }), el("p", { text: "Portrait groups are suggestions from local project media. Confirm merges and splits before requesting rights." })),
      el("span", { class: "plb-coverage-pill", text: coverageLabel(identity.coverage) })
    )
  );
  if (presentation.recurring.length) {
    list.append(peopleTierSection(
      "Recurring people",
      "Appearance groups seen four or more times",
      presentation.recurring,
      0,
      identity,
      sources,
      state
    ));
  }
  if (presentation.supporting.length) {
    list.append(peopleTierSection(
      "Supporting people",
      "Appearance groups seen three times",
      presentation.supporting,
      presentation.recurring.length,
      identity,
      sources,
      state
    ));
  }
  if (manualDrafts.length) {
    list.append(
      el(
        "section",
        { class: "plb-manual-people-section" },
        el(
          "div",
          { class: "plb-section-heading" },
          el("div", {}, metaLabel("Manually identified", true), el("h3", { text: "Body, silhouette, or masked performers" })),
          el("span", { class: "plb-one-off-count", text: String(manualDrafts.length) })
        ),
        el("div", { class: "plb-list" }, draftPersonCards(manualDrafts))
      )
    );
  }
  if (presentation.oneOff.length) {
    list.append(oneOffSection(presentation, identity, sources, state));
  }
  if (!identity.candidates.length) {
    list.append(el("div", { class: "plb-empty" }, pluribusMark(20), el("div", { text: "No likely people were found in the analyzed media. Check Sources for coverage and exclusions." })));
  }
  container.replaceChildren(list);
  return true;
}

function oneOffSection(presentation, identity, sources, state) {
  const candidates = presentation.oneOff || [];
  const content = el("div", { class: "plb-one-off-list" });
  let rendered = 0;

  function appendNextPage() {
    const end = Math.min(candidates.length, rendered + ONE_OFF_PAGE_SIZE);
    const cards = candidates.slice(rendered, end).map((candidate, index) =>
      candidateCard(candidate, presentation.primary.length + rendered + index, identity, sources, state)
    );
    rendered = end;
    content.querySelector(".plb-one-off-load-more")?.remove();
    content.append(...cards);
    if (rendered < candidates.length) {
      const remaining = candidates.length - rendered;
      const loadMore = button(
        `Load ${Math.min(ONE_OFF_PAGE_SIZE, remaining)} more`,
        "secondary",
        appendNextPage
      );
      loadMore.classList.add("plb-one-off-load-more");
      content.append(loadMore);
    }
  }

  const section = el(
    "details",
    { class: "plb-one-off-section" },
    el(
      "summary",
      {},
      el("span", {}, el("strong", { text: "Other / one-off appearances" }), el("small", { text: "People seen once or twice · open to review every crop" })),
      el("span", { class: "plb-one-off-count", text: String(candidates.length) })
    ),
    content
  );
  section.addEventListener("toggle", () => {
    if (section.open && rendered === 0) appendNextPage();
  });
  return section;
}

function peopleTierSection(title, description, candidates, indexOffset, identity, sources, state) {
  return el(
    "section",
    { class: "plb-people-tier" },
    el(
      "div",
      { class: "plb-section-heading" },
      el("div", {}, el("h3", { text: title }), el("small", { class: "plb-tier-description", text: description })),
      el("span", { class: "plb-one-off-count", text: String(candidates.length) })
    ),
    el(
      "div",
      { class: "plb-candidate-list" },
      candidates.map((candidate, index) => candidateCard(candidate, indexOffset + index, identity, sources, state))
    )
  );
}

function candidateCard(candidate, index, identity, sources, state) {
  const links = identityLinksForCandidate(candidate, state).filter((link) => link.state === "confirmed");
  const linkedPeople = links.map((link) => {
    const personId = String(link.personId || link.person_id || "");
    const draft = draftForCandidate(candidate, state, personId);
    return {
      link,
      personId,
      draft,
      name: draft?.displayName || link.displayName || link.display_name || "Reviewed person",
    };
  });
  const draft = linkedPeople.length === 1
    ? linkedPeople[0].draft
    : linkedPeople.length
      ? null
      : draftForCandidate(candidate, state);
  const link = linkedPeople.length === 1 ? linkedPeople[0].link : null;
  const name = linkedPeople.length > 1
    ? `${linkedPeople.length} reviewed people in this visual group`
    : draft?.displayName || link?.displayName || link?.display_name || candidateName(candidate, index);
  const role = candidateRoleLabel(linkedPeople, draft, candidate);
  const suggestionProvenance = identitySuggestionProvenance(candidate);
  const showingProjectSuggestion = !linkedPeople.length && !draft && Boolean(candidate.suggestedName || candidate.suggestedRole);
  const occurrences = candidateOccurrences(candidate, identity);
  const fullyConfirmed = candidateIsFullyConfirmed(candidate, identity, state);
  const dismissedCount = candidateDismissedOccurrenceIds(candidate, identity, state).size;
  const hasFalsePositiveDecision = identityLinksForCandidate(candidate, state).some((candidateLink) =>
    candidateLink.state === "rejected" && !(candidateLink.personId || candidateLink.person_id)
  );
  const resolved = candidateIsResolved(candidate, identity, state);
  const fullyDismissed = resolved
    && hasFalsePositiveDecision
    && (!occurrences.length || dismissedCount === occurrences.length);
  const unresolved = candidateUnresolvedCount(candidate, identity, state);
  const partial = !resolved && (linkedPeople.length > 0 || Boolean(draft) || dismissedCount > 0);
  const review = !resolved;
  const issues = candidateIssues(candidate, identity);
  const ambiguousCount = issues.filter((issue) => issue.code === "ambiguous_identity").length;
  const headingId = `plb-candidate-${candidate.candidateId.replace(/[^a-z0-9_-]/gi, "-")}`;
  return el(
    "article",
    { class: `plb-candidate-card${resolved ? " confirmed" : review ? " review" : ""}`, "aria-labelledby": headingId },
    filmstrip(candidate, identity),
    el(
      "div",
      { class: "plb-candidate-body" },
      el(
        "div",
        { class: "plb-candidate-heading" },
        el("div", {}, el("h3", { id: headingId, text: name }), role ? el("p", { text: role }) : null),
        el("span", {
          class: `plb-status-pill${resolved ? " plb-status-pill--ok" : review ? " plb-status-pill--warn" : ""}`,
          text: fullyDismissed ? "Not a person" : resolved && dismissedCount ? "Reviewed" : fullyConfirmed ? "Confirmed" : partial ? "Partial review" : review ? "Review" : "Suggested",
        })
      ),
      el(
        "div",
        { class: "plb-candidate-facts" },
        el("span", { text: `${occurrences.length} ${occurrences.length === 1 ? "appearance" : "appearances"}` }),
        el("span", { text: `${candidate.sourceRefs.length} ${candidate.sourceRefs.length === 1 ? "source" : "sources"}` }),
        el("span", { text: visualGroupingLabel(candidate, identity) }),
        showingProjectSuggestion
          ? el("span", { class: "plb-provenance-badge", text: suggestionProvenance.badge })
          : null
      ),
      el("p", { class: "plb-use-sentence", text: plainLanguageUseSummary(candidate, sources) }),
      linkedPeople.length > 1
        ? el("p", { class: "plb-linked-people-line", text: linkedPeople.map((person) => person.name).join(" · ") })
        : null,
      candidate.evidence.length
        ? el("p", { class: "plb-evidence-line", text: candidate.evidence.slice(0, 2).join(" · ") })
        : null,
      issues.length
        ? el("p", {
            class: "plb-candidate-issue",
            text: ambiguousCount
              ? `${ambiguousCount} ${ambiguousCount === 1 ? "appearance needs" : "appearances need"} comparison in this group.`
              : issues[0].title,
          })
        : null,
      el(
        "div",
        { class: "plb-actions" },
        linkedPeople.length
          ? linkedPeople.map((person) =>
              button(`Edit ${person.name}`, "secondary", () =>
                openIdentityReviewDialog(candidate, person.personId)
              )
            )
          : button(resolved || draft ? "Edit review" : "Review identity", "primary", () => openIdentityReviewDialog(candidate, draft?.draftId)),
        linkedPeople.length && unresolved
          ? button("Add person from remaining", "primary", () =>
              openIdentityReviewDialog(candidate, "", { newPerson: true })
            )
          : null,
        resolved
          ? el("span", {
              class: "plb-saved-note",
              text: fullyDismissed
                ? "All detector mistakes dismissed for this analysis"
                : dismissedCount
                  ? `${dismissedCount} false ${dismissedCount === 1 ? "detection" : "detections"} dismissed`
                  : "All appearances confirmed",
            })
          : partial
            ? el("span", { class: "plb-saved-note plb-saved-note--warn", text: `${unresolved} ${unresolved === 1 ? "appearance remains" : "appearances remain"} unresolved` })
            : null
      )
    )
  );
}

export function openIdentityReviewDialog(candidate, initialDraftId = "", options = {}) {
  const state = getState();
  const identity = state.identityPayload;
  const workflowRef = state.workflowBinding?.workflowRef;
  const openedScanEpoch = state.scanEpoch;
  const openedScan = state.scan;
  const openedJobId = String(state.identityJob?.jobId || state.identityJob?.job_id || "");
  if (!identity || !workflowRef) {
    toast("Run identity analysis again before reviewing this person.");
    return;
  }

  const newPerson = Boolean(options.newPerson);
  const candidateLinks = identityLinksForCandidate(candidate, state);
  const explicitLink = newPerson
    ? null
    : initialDraftId
      ? identityLinkForCandidate(candidate, state, initialDraftId)
      : candidateLinks.length === 1
        ? candidateLinks[0]
        : null;
  const explicitPersonId = String(explicitLink?.personId || explicitLink?.person_id || initialDraftId || "");
  const existing = newPerson
    ? null
    : (state.personDrafts || []).find((draft) =>
        draft.draftId === explicitPersonId || draft.canonicalPersonId === explicitPersonId
      ) || draftForCandidate(candidate, state, explicitPersonId);
  const savedPeople = existingIdentityChoices(state.personDrafts, projectPeople());
  const initialSavedPerson = newPerson
    ? null
    : existingIdentityChoiceForId(
        savedPeople,
        explicitPersonId || existing?.canonicalPersonId || existing?.draftId || ""
      );
  let selectedSavedPerson = initialSavedPerson;
  const allOccurrences = candidateOccurrences(candidate, identity);
  const lockedOccurrenceIds = confirmedOccurrenceIds(candidate, state, explicitPersonId);
  const lockedOccurrenceOwners = new Map();
  for (const link of candidateLinks) {
    const personId = String(link.personId || link.person_id || "");
    if (link.state !== "confirmed" || personId === explicitPersonId) continue;
    const personDraft = draftForCandidate(candidate, state, personId);
    const owner = personDraft?.displayName || link.displayName || link.display_name || "another person";
    for (const occurrenceId of linkedOccurrenceIds(link)) lockedOccurrenceOwners.set(occurrenceId, owner);
  }
  const groups = groupOccurrencesBySource(candidate, identity);
  const representedRefs = new Set(groups.map((group) => group.sourceRef).filter(Boolean));
  for (const sourceRef of candidate.sourceRefs || []) {
    if (!representedRefs.has(sourceRef)) {
      const source = sourceForRef(sourceRef, state);
      groups.push({ sourceRef, label: source ? sourceDisplayLabel(source) : "Source", occurrences: [] });
    }
  }
  const candidateSourceRefs = [...new Set([
    ...(candidate.sourceRefs || []),
    ...groups.map((group) => group.sourceRef),
  ].filter(Boolean).map(String))];
  const pendingNewDraftId = globalThis.crypto.randomUUID();

  let stage = 1;
  const editingFalsePositive = Boolean(
    explicitLink?.state === "rejected"
    && !(explicitLink?.personId || explicitLink?.person_id)
  );
  let decision = explicitLink?.state === "unsure"
    ? "unsure"
    : editingFalsePositive
      ? "false_positive"
      : "same";
  const storedOccurrenceValues = explicitLink?.occurrenceIds ?? explicitLink?.occurrence_ids;
  const initialPerson = initialSavedPerson || existing;
  const existingSourceRefs = new Set(initialPerson?.sourceRefs || []);
  const occurrenceIds = Array.isArray(storedOccurrenceValues)
    ? new Set(storedOccurrenceValues.map(String).filter((value) => !lockedOccurrenceIds.has(value)))
    : new Set(allOccurrences.filter((occurrence) =>
        !lockedOccurrenceIds.has(String(occurrence.occurrenceId))
          && (existingSourceRefs.size ? existingSourceRefs.has(occurrence.sourceRef) : !occurrence.ambiguous)
      ).map((occurrence) => String(occurrence.occurrenceId)));
  const form = {
    name: initialPerson?.displayName || (newPerson ? "" : candidate.suggestedName) || "",
    role: initialPerson?.role || (newPerson ? "" : candidate.suggestedRole) || "",
    talentEmail: initialPerson?.talentEmail || "",
    repRole: initialPerson?.representative?.role || "manager",
    repName: initialPerson?.representative?.name || "",
    repEmail: initialPerson?.representative?.email || "",
    notes: initialPerson?.notes || "",
    occurrenceIds,
    sourceRefs: new Set(groups.filter((group) =>
      group.occurrences.length === 0 && (!existing || existingSourceRefs.has(group.sourceRef))
    ).map((group) => group.sourceRef).filter(Boolean)),
  };
  const initialPersonFields = {
    name: form.name,
    role: form.role,
    talentEmail: form.talentEmail,
    repRole: form.repRole,
    repName: form.repName,
    repEmail: form.repEmail,
    notes: form.notes,
  };

  function resetPersonFieldsForDifferentPerson() {
    selectedSavedPerson = null;
    Object.assign(form, {
      name: "",
      role: "",
      talentEmail: "",
      repRole: "manager",
      repName: "",
      repEmail: "",
      notes: "",
    });
  }

  function restoreInitialPersonFields() {
    selectedSavedPerson = initialSavedPerson;
    Object.assign(form, initialPersonFields);
  }

  function fillPersonFields(person) {
    Object.assign(form, {
      name: person?.displayName || "",
      role: person?.role || "",
      talentEmail: person?.talentEmail || "",
      repRole: person?.representative?.role || "manager",
      repName: person?.representative?.name || "",
      repEmail: person?.representative?.email || "",
      notes: person?.notes || "",
    });
  }

  function syncSourceRefsFromOccurrences() {
    for (const group of groups) {
      if (!group.sourceRef || !group.occurrences.length) continue;
      const selected = group.occurrences.some((occurrence) => form.occurrenceIds.has(String(occurrence.occurrenceId)));
      if (selected) form.sourceRefs.add(group.sourceRef);
      else form.sourceRefs.delete(group.sourceRef);
    }
  }
  syncSourceRefsFromOccurrences();

  const titleId = `plb-identity-dialog-${candidate.candidateId.replace(/[^a-z0-9_-]/gi, "-")}`;
  const previousFocus = document.activeElement;
  const overlay = el("div", { class: "plb-overlay plb-root" });
  const reviewContextIsCurrent = () => {
    const current = getState();
    const currentJobId = String(current.identityJob?.jobId || current.identityJob?.job_id || "");
    return current.scanEpoch === openedScanEpoch
      && current.workflowBinding?.workflowRef === workflowRef
      && currentJobId === openedJobId;
  };
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    previousFocus?.focus?.();
  };
  async function ensureReviewContextCurrent() {
    if (!reviewContextIsCurrent()) {
      close();
      toast("The workflow changed. Reopen this person from the current People view.");
      return false;
    }
    try {
      const { scanMatchesCurrentWorkflow } = await import("./canvas.js");
      if (!(await scanMatchesCurrentWorkflow(openedScan)) || !reviewContextIsCurrent()) {
        close();
        toast("The graph changed after identity analysis. Find people again before saving this review.");
        return false;
      }
    } catch (error) {
      toast(error.message || "Could not verify the current graph. Try again when ComfyUI is ready.");
      return false;
    }
    return true;
  }
  const onKey = (event) => {
    if (event.key === "Escape") close();
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    )].filter((node) => node.getClientRects().length > 0);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });

  const dialog = el(
    "div",
    {
      class: "plb-dialog plb-identity-dialog",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
    }
  );
  overlay.append(dialog);
  document.body.append(overlay);

  function updateFromInput(key) {
    return (event) => {
      form[key] = event.target.value;
    };
  }

  function stepper() {
    const steps = [[1, "Confirm identity"], [2, "Review appearances"], [3, "Rights contact"]];
    return el(
      "ol",
      { class: "plb-stepper", "aria-label": "Person review progress" },
      steps.map(([number, label]) => el(
        "li",
        { class: `${stage === number ? "active" : stage > number ? "complete" : ""}`, "aria-current": stage === number ? "step" : null },
        el("span", { text: stage > number ? "✓" : String(number) }),
        el("strong", { text: label })
      ))
    );
  }

  function renderDialog() {
    const name = candidateName(candidate);
    const header = el(
      "div",
      { class: "plb-dialog-header" },
      el("div", {}, el("div", { class: "plb-dialog-title", id: titleId }, pluribusMark(13), el("span", { text: "Review person" })), el("div", { class: "plb-dialog-sub", text: "Local project identity · never a clearance decision" })),
      el("button", { class: "plb-x", type: "button", text: "×", onclick: close, "aria-label": "Close person review" })
    );
    const content = stage === 1 ? identityStage(name) : stage === 2 ? appearancesStage(name) : contactStage(name);
    dialog.replaceChildren(header, stepper(), content);
    const autofocus = dialog.querySelector("[data-autofocus], .plb-decision-option.active input, button");
    setTimeout(() => autofocus?.focus(), 0);
  }

  function identityStage(name) {
    const nameInput = input(form.name, "Full name or character name", "text", updateFromInput("name"));
    nameInput.setAttribute("data-autofocus", "true");
    const roleInput = input(form.role, "Role in this project", "text", updateFromInput("role"));
    const savedPersonPicker = el(
      "select",
      {
        class: "plb-input",
        "aria-label": "Use an existing project person",
        onchange: (event) => {
          selectedSavedPerson = savedPeople.find((person) => person.choiceId === event.target.value) || null;
          if (selectedSavedPerson) fillPersonFields(selectedSavedPerson);
          else if (decision === "different") resetPersonFieldsForDifferentPerson();
          else restoreInitialPersonFields();
          renderDialog();
        },
      },
      el("option", {
        value: "",
        text: initialSavedPerson && decision !== "different"
          ? "Keep the current person"
          : "Create or identify a new person",
      }),
      savedPeople.map((person) => el("option", {
        value: person.choiceId,
        text: `${person.displayName}${person.role ? ` · ${person.role}` : ""}`,
      }))
    );
    savedPersonPicker.value = selectedSavedPerson?.choiceId || "";
    const provenance = identitySuggestionProvenance(candidate);
    const usesProjectSuggestion = !newPerson
      && !selectedSavedPerson
      && decision !== "different"
      && decision !== "false_positive"
      && Boolean(candidate.suggestedName || candidate.suggestedRole)
      && (!candidate.suggestedName || form.name === candidate.suggestedName);
    const workingHeading = decision === "false_positive"
      ? "Correct false detections"
      : selectedSavedPerson?.displayName
      || form.name
      || (usesProjectSuggestion ? candidate.suggestedName : "")
      || (newPerson ? "New person from remaining appearances" : "Unresolved person");
    const evidence = candidate.evidence.length ? candidate.evidence : [
      `${candidateOccurrences(candidate, identity).length} recurring visual appearances`,
      "Compared only within this project",
    ];
    return el(
      "div",
      { class: "plb-identity-stage" },
      el("div", { class: "plb-identity-visual" }, filmstrip(candidate, identity, 6, "review"), el("p", { text: visualGroupingLabel(candidate, identity) }), evidenceSheets(candidate, name)),
      el(
        "div",
        { class: "plb-identity-form" },
        metaLabel(decision === "false_positive" ? "Detector correction" : selectedSavedPerson ? "Saved project person" : usesProjectSuggestion ? provenance.label : existing ? "Reviewed project person" : "Unresolved person", true),
        el("h2", { text: workingHeading }),
        el("p", { class: "plb-stage-copy", text: "Confirm whether these visual appearances belong together. Grouping is project-scoped and does not establish identity or permission." }),
        el(
          "fieldset",
          { class: "plb-decision-set" },
          el("legend", { text: "Identity decision" }),
          decisionOption("same", "Same person", "Keep this appearance group together"),
          decisionOption("different", "Different person", "Enter the correct person or character"),
          decisionOption("unsure", "Not sure", "Leave this group in the review queue"),
          decisionOption("false_positive", "Not a person", "Dismiss only detector mistakes in this group")
        ),
        decision === "false_positive"
          ? el(
              "div",
              { class: "plb-privacy-note" },
              el("strong", { text: "Scoped detector correction" }),
              el("span", { text: "Choose only crops that do not depict a real person. This does not mark the whole source as person-free, and changed media will reopen review." })
            )
          : savedPeople.length
            ? formField(
                "Use a saved person",
                savedPersonPicker,
                "Choose explicitly when you recognize this person. Pluribus never merges identities automatically."
              )
            : null,
        decision !== "false_positive" && selectedSavedPerson
          ? el(
              "div",
              { class: "plb-provenance-note plb-existing-person-note" },
              metaLabel("Existing identity selected"),
              el("strong", { text: selectedSavedPerson.displayName }),
              el("span", { text: "Saving will assign only the appearances you select to this saved person. It will not create a duplicate identity." })
            )
          : null,
        decision === "false_positive" ? null : formField("Name", nameInput, usesProjectSuggestion && candidate.suggestedName ? provenance.badge : "Add a working name; contact details can wait"),
        decision === "false_positive" ? null : formField("Role", roleInput),
        usesProjectSuggestion
          ? el("div", { class: "plb-provenance-note" }, metaLabel("Working-name provenance"), el("strong", { text: provenance.label }), el("span", { text: provenance.description }))
          : null,
        el("div", { class: "plb-evidence-box" }, metaLabel("Why these visuals were grouped"), evidence.slice(0, 4).map((item) => el("span", { text: item }))),
        el("div", { class: "plb-dialog-actions" }, button("Cancel", "secondary", close), button(decision === "unsure" ? "Review appearances" : "Next · appearances", "primary", () => { stage = 2; renderDialog(); }))
      )
    );
  }

  function decisionOption(value, label, help) {
    const radio = el("input", { type: "radio", name: "identity-decision", value });
    radio.checked = decision === value;
    radio.addEventListener("change", () => {
      const previousDecision = decision;
      decision = value;
      if (value === "different" && previousDecision !== "different") {
        resetPersonFieldsForDifferentPerson();
      } else if (previousDecision === "different" && value !== "different") {
        restoreInitialPersonFields();
      }
      if (value === "false_positive" && !explicitPersonId && !editingFalsePositive) {
        form.occurrenceIds = new Set(allOccurrences
          .filter((occurrence) => !lockedOccurrenceIds.has(String(occurrence.occurrenceId)))
          .map((occurrence) => String(occurrence.occurrenceId)));
        syncSourceRefsFromOccurrences();
      }
      renderDialog();
    });
    return el("label", { class: `plb-decision-option${decision === value ? " active" : ""}` }, radio, el("span", {}, el("strong", { text: label }), el("small", { text: help })));
  }

  function appearancesStage(name) {
    const selectedCount = allOccurrences.filter((occurrence) =>
      form.occurrenceIds.has(String(occurrence.occurrenceId))
    ).length;
    return el(
      "div",
      { class: "plb-appearance-stage" },
      el(
        "div",
        { class: "plb-stage-heading" },
        metaLabel("Step 2 of 3", true),
        el("h2", { text: decision === "false_positive" ? "Dismiss false detections" : "Review appearances" }),
        el("p", {
          text: decision === "false_positive"
            ? "Select only crops that do not depict a real person. Other appearances stay in review, and no whole source is marked person-free."
            : "Likely matches are preselected and grouped by source. Check each appearance that belongs to this person; the source checkbox selects the whole group.",
        }),
        allOccurrences.length
          ? el("p", {
              class: "plb-selection-summary",
              role: "status",
              "aria-live": "polite",
              "data-appearance-summary": "true",
              text: appearanceSelectionSummary(selectedCount),
            })
          : null
      ),
      el(
        "div",
        { class: "plb-appearance-groups" },
        groups.map((group, index) => appearanceGroup(group, index, name))
      ),
      el(
        "div",
        { class: "plb-dialog-actions plb-dialog-actions--sticky" },
        button("Back", "secondary", () => { stage = 1; renderDialog(); }),
        decision === "unsure"
          ? button("Leave for review", "primary", () => void persistUnresolvedDecision())
          : decision === "false_positive"
            ? button("Dismiss selected detections", "primary", () => void persistFalsePositiveDecision())
          : button("Next · rights contact", "primary", () => {
              if (allOccurrences.length && !form.occurrenceIds.size) {
                toast("Select at least one appearance for this person.");
                return;
              }
              if (!allOccurrences.length && !form.sourceRefs.size) {
                toast("Keep at least one source for this person.");
                return;
              }
              stage = 3;
              renderDialog();
            })
      )
    );
  }

  function appearanceSelectionSummary(selectedCount = form.occurrenceIds.size) {
    const available = Math.max(0, allOccurrences.length - lockedOccurrenceIds.size);
    const unresolved = Math.max(0, available - selectedCount);
    const assigned = lockedOccurrenceIds.size ? ` · ${lockedOccurrenceIds.size} already assigned` : "";
    if (decision === "false_positive") {
      return `${selectedCount} of ${available} available ${available === 1 ? "detection" : "detections"} marked not a person${unresolved ? ` · ${unresolved} remain in review` : " · all reviewed"}${assigned}`;
    }
    return `${selectedCount} of ${available} available ${available === 1 ? "appearance" : "appearances"} selected${unresolved ? ` · ${unresolved} unresolved` : " · all reviewed"}${assigned}`;
  }

  function updateAppearanceSelectionSummary() {
    const selectedCount = allOccurrences.filter((occurrence) =>
      form.occurrenceIds.has(String(occurrence.occurrenceId))
    ).length;
    const summary = dialog.querySelector("[data-appearance-summary]");
    if (summary) summary.textContent = appearanceSelectionSummary(selectedCount);
  }

  function appearanceGroup(group, index, name) {
    const sourceRef = group.sourceRef;
    const sourceLabel = group.label || `source ${index + 1}`;
    const allGroupOccurrenceIds = group.occurrences.map((occurrence) => String(occurrence.occurrenceId));
    const occurrenceIds = allGroupOccurrenceIds.filter((occurrenceId) => !lockedOccurrenceIds.has(occurrenceId));
    const checkbox = el("input", {
      type: "checkbox",
      "aria-label": group.occurrences.length
        ? `${decision === "false_positive" ? "Dismiss all detections" : "Select all appearances"} in ${sourceLabel}`
        : `${decision === "false_positive" ? "Dismiss" : "Include"} ${sourceLabel}`,
    });
    const count = el("small");
    const occurrenceControls = [];
    const source = sourceForRef(sourceRef);
    const representativeItems = representativeOccurrences(group.occurrences).map((occurrence) => {
      const occurrenceId = String(occurrence.occurrenceId);
      const lockedOwner = lockedOccurrenceOwners.get(occurrenceId);
      const status = el("span", { class: "plb-appearance-item-status" });
      const item = el(
        "div",
        { class: `plb-appearance-representative${lockedOwner ? " locked" : ""}` },
        occurrenceCrop(occurrence, name),
        el("span", { class: "plb-appearance-representative-copy" }, status)
      );
      return { item, occurrenceId, status, lockedOwner };
    });
    const occurrenceList = el("div", { class: "plb-appearance-strip" });
    let occurrenceControlsBuilt = false;

    function buildOccurrenceControls() {
      if (occurrenceControlsBuilt) return;
      occurrenceControlsBuilt = true;
      const items = group.occurrences.map((occurrence) => {
        const occurrenceId = String(occurrence.occurrenceId);
        const lockedOwner = lockedOccurrenceOwners.get(occurrenceId);
        const control = el("input", {
          type: "checkbox",
          "aria-label": `${decision === "false_positive" ? "Dismiss" : "Include"} ${occurrenceAlt(occurrence, name)}`,
          disabled: Boolean(lockedOwner),
        });
        const status = el("span", { class: "plb-appearance-item-status" });
        const item = el(
          "label",
          { class: `plb-appearance-item${lockedOwner ? " locked" : ""}` },
          control,
          occurrenceCrop(occurrence, name),
          el(
            "span",
            { class: "plb-appearance-item-copy" },
            el("span", { text: [occurrence.sceneLabel, occurrence.timecode].filter(Boolean).join(" · ") || "Detected frame" }),
            status
          )
        );
        occurrenceControls.push({ control, item, occurrenceId, status, lockedOwner });
        control.addEventListener("change", () => {
          if (lockedOwner) return;
          if (control.checked) form.occurrenceIds.add(occurrenceId);
          else form.occurrenceIds.delete(occurrenceId);
          syncSourceRefsFromOccurrences();
          updateGroupState();
          updateAppearanceSelectionSummary();
        });
        return item;
      });
      occurrenceList.replaceChildren(...items);
      updateGroupState();
    }

    const occurrenceDetails = group.occurrences.length
      ? el(
          "details",
          { class: "plb-appearance-details" },
          el(
            "summary",
            {},
            el("span", {}, el("strong", { text: `Review ${group.occurrences.length} ${group.occurrences.length === 1 ? "appearance" : "appearances"}` }), el("small", { text: "Expand for individual selection" }))
          ),
          occurrenceList
        )
      : null;
    occurrenceDetails?.addEventListener("toggle", () => {
      if (occurrenceDetails.open) buildOccurrenceControls();
    });
    const section = el(
      "section",
      { class: "plb-appearance-group" },
      el(
        "label",
        { class: "plb-appearance-group-header" },
        checkbox,
        el("span", {}, el("strong", { text: group.label || (source ? sourceDisplayLabel(source, index) : `Source ${index + 1}`) }), count)
      ),
      group.occurrences.length
        ? el("div", { class: "plb-appearance-representatives", "aria-label": `Representative appearances from ${sourceLabel}` }, representativeItems.map(({ item }) => item))
        : el("div", { class: "plb-source-preview-large" }, sourcePreview(source, name)),
      occurrenceDetails
    );

    function updateGroupState() {
      const selectedCount = occurrenceIds.filter((occurrenceId) => form.occurrenceIds.has(occurrenceId)).length;
      const selected = group.occurrences.length ? selectedCount > 0 : Boolean(sourceRef && form.sourceRefs.has(sourceRef));
      checkbox.checked = group.occurrences.length
        ? occurrenceIds.length > 0 && selectedCount === occurrenceIds.length
        : selected;
      checkbox.indeterminate = group.occurrences.length && selectedCount > 0 && selectedCount < occurrenceIds.length;
      checkbox.disabled = group.occurrences.length > 0 && occurrenceIds.length === 0;
      section.classList.toggle("selected", selected);
      section.classList.toggle("partial", checkbox.indeterminate);
      count.textContent = group.occurrences.length
        ? `${selectedCount} of ${occurrenceIds.length} available ${decision === "false_positive" ? "dismissed" : "selected"}${allGroupOccurrenceIds.length > occurrenceIds.length ? ` · ${allGroupOccurrenceIds.length - occurrenceIds.length} assigned` : ""}`
        : selected ? "Source included" : "Source unresolved";
      for (const item of representativeItems) {
        if (item.lockedOwner) {
          item.item.classList.remove("selected", "unresolved");
          item.item.classList.add("locked");
          item.status.textContent = `Assigned to ${item.lockedOwner}`;
          continue;
        }
        const included = form.occurrenceIds.has(item.occurrenceId);
        item.item.classList.toggle("selected", included);
        item.item.classList.toggle("unresolved", !included);
        item.status.textContent = included
          ? decision === "false_positive" ? "Dismissed" : "Included"
          : "Unresolved";
      }
      for (const item of occurrenceControls) {
        if (item.lockedOwner) {
          item.control.checked = false;
          item.item.classList.remove("selected", "unresolved");
          item.status.textContent = `Assigned to ${item.lockedOwner}`;
          continue;
        }
        const included = form.occurrenceIds.has(item.occurrenceId);
        item.control.checked = included;
        item.item.classList.toggle("selected", included);
        item.item.classList.toggle("unresolved", !included);
        item.status.textContent = included
          ? decision === "false_positive" ? "Dismissed" : "Included"
          : "Unresolved";
      }
    }

    checkbox.addEventListener("change", () => {
      if (group.occurrences.length) {
        for (const occurrenceId of occurrenceIds) {
          if (checkbox.checked) form.occurrenceIds.add(occurrenceId);
          else form.occurrenceIds.delete(occurrenceId);
        }
        syncSourceRefsFromOccurrences();
      } else if (sourceRef) {
        if (checkbox.checked) form.sourceRefs.add(sourceRef);
        else form.sourceRefs.delete(sourceRef);
      }
      updateGroupState();
      updateAppearanceSelectionSummary();
    });
    updateGroupState();
    return section;
  }

  function contactStage(name) {
    const selectedCount = allOccurrences.filter((occurrence) =>
      form.occurrenceIds.has(String(occurrence.occurrenceId))
    ).length;
    const availableCount = Math.max(0, allOccurrences.length - lockedOccurrenceIds.size);
    const unresolvedCount = Math.max(0, availableCount - selectedCount);
    const talentEmail = input(form.talentEmail, "Optional", "email", updateFromInput("talentEmail"));
    const repName = input(form.repName, "Optional", "text", updateFromInput("repName"));
    const repEmail = input(form.repEmail, "Optional", "email", updateFromInput("repEmail"));
    const repRole = selectControl([
      ["manager", "Manager"], ["agent", "Agent"], ["attorney", "Attorney"],
      ["guardian", "Parent or guardian"], ["talent", "Talent directly"],
      ["rights_holder", "Rights holder"], ["other", "Other"],
    ], form.repRole, (event) => { form.repRole = event.target.value; });
    const notes = el("textarea", { class: "plb-textarea", maxlength: "3000", placeholder: "Optional production context" });
    notes.value = form.notes;
    notes.addEventListener("input", updateFromInput("notes"));
    return el(
      "div",
      { class: "plb-contact-stage" },
      el(
        "div",
        { class: "plb-contact-summary" },
        filmstrip(candidate, identity, 4, "review"),
        metaLabel("Identity ready", true),
        el("h2", { text: form.name || name }),
        form.role ? el("p", { text: form.role }) : null,
        el("p", {
          text: allOccurrences.length
            ? `${selectedCount} of ${availableCount} available ${availableCount === 1 ? "appearance" : "appearances"} selected · ${unresolvedCount} unresolved${lockedOccurrenceIds.size ? ` · ${lockedOccurrenceIds.size} assigned to other people` : ""}`
            : `${form.sourceRefs.size} ${form.sourceRefs.size === 1 ? "source" : "sources"} selected`,
        }),
        el("div", { class: "plb-privacy-note" }, el("strong", { text: "Identity is not permission" }), el("span", { text: "Saving this person does not mark them cleared. Connect only when you are ready to request rights." }))
      ),
      el(
        "div",
        { class: "plb-contact-form" },
        metaLabel("Step 3 of 3", true),
        el("h2", { text: "Rights contact" }),
        el("p", { class: "plb-stage-copy", text: "This information is optional now. Pluribus will prefill it from a connected talent record when available." }),
        formField("Talent email", talentEmail),
        el("details", { class: "plb-contact-details" }, el("summary", { text: "Add representative details" }), formField("Representative role", repRole), formField("Representative name", repName), formField("Representative email", repEmail)),
        formField("Producer notes", notes),
        el(
          "div",
          { class: "plb-dialog-actions" },
          removeAssignmentButton(),
          button("Back", "secondary", () => { stage = 2; renderDialog(); }),
          saveButton()
        )
      )
    );
  }

  let unresolvedDecisionSaving = false;
  let falsePositiveSaving = false;

  function handleIdentityMutationError(error, fallback) {
    if (identityRevisionConflict(error)) {
      close();
      toast("Visual review changed in another window. Reopen this person and try again.");
      return;
    }
    toast(error.message || fallback);
  }

  async function freshLinkSnapshot() {
    if (!(await ensureReviewContextCurrent())) return null;
    const snapshot = await refreshIdentityLinks(openedJobId);
    if (
      !snapshot
      || !Array.isArray(snapshot.links)
      || !Number.isInteger(snapshot.revision)
    ) {
      throw new Error("Could not load the current visual identity links and revision.");
    }
    if (!(await ensureReviewContextCurrent())) return null;
    return snapshot;
  }

  async function persistUnresolvedDecision() {
    if (unresolvedDecisionSaving) return;
    if (!openedJobId) {
      toast("Run identity analysis again before leaving this group for review.");
      return;
    }
    unresolvedDecisionSaving = true;
    try {
      const snapshot = await freshLinkSnapshot();
      if (!snapshot) {
        unresolvedDecisionSaving = false;
        return;
      }
      const priorPersonId = explicitPersonId || existing?.draftId || "";
      const remaining = identityLinksWithUnresolvedDecision(
        snapshot.links,
        candidate.candidateId,
        [...form.occurrenceIds],
        {
          priorPersonId,
          displayName: existing?.displayName || form.name || "",
          candidateOccurrenceIds: allOccurrences.map((occurrence) => String(occurrence.occurrenceId)),
        }
      );
      await commitIdentityLinks(openedJobId, remaining, snapshot.revision);
      close();
      toast("Identity left unresolved for producer review.");
    } catch (error) {
      unresolvedDecisionSaving = false;
      handleIdentityMutationError(error, "Could not leave this identity unresolved.");
    }
  }

  async function persistFalsePositiveDecision() {
    if (falsePositiveSaving) return;
    if (!openedJobId) {
      toast("Run identity analysis again before correcting false detections.");
      return;
    }
    if (allOccurrences.length && !form.occurrenceIds.size) {
      toast("Select at least one detector mistake to dismiss.");
      return;
    }
    falsePositiveSaving = true;
    try {
      const snapshot = await freshLinkSnapshot();
      if (!snapshot) {
        falsePositiveSaving = false;
        return;
      }
      const priorPersonId = explicitPersonId || existing?.draftId || "";
      const links = identityLinksWithFalsePositiveDecision(
        snapshot.links,
        candidate.candidateId,
        [...form.occurrenceIds],
        { priorPersonId, replaceExistingDismissal: editingFalsePositive }
      );
      await commitIdentityLinks(openedJobId, links, snapshot.revision);
      close();
      toast("Selected detector mistakes dismissed for this analysis. The source itself was not marked person-free.");
    } catch (error) {
      falsePositiveSaving = false;
      handleIdentityMutationError(error, "Could not dismiss these detector mistakes.");
    }
  }

  function removeAssignmentButton() {
    if (!explicitLink || !explicitPersonId) return null;
    return button("Remove visual assignment", "ghost", async () => {
      if (!globalThis.confirm("Remove this person's appearances from the visual group? The person draft will be kept.")) return;
      try {
        const snapshot = await freshLinkSnapshot();
        if (!snapshot) return;
        const remaining = snapshot.links.filter((link) =>
          (link.candidateId || link.candidate_id) !== candidate.candidateId
            || (link.personId || link.person_id) !== explicitPersonId
        );
        remaining.push({
          candidateId: candidate.candidateId,
          personId: explicitPersonId,
          displayName: existing?.displayName || form.name || "",
          state: "rejected",
        });
        await commitIdentityLinks(openedJobId, remaining, snapshot.revision);
        close();
        toast("Visual assignment removed. The person draft was kept.");
      } catch (error) {
        handleIdentityMutationError(error, "Could not remove the visual assignment.");
      }
    });
  }

  function saveButton() {
    const targetDraft = selectedSavedPerson?.draft || (decision === "different" ? null : existing);
    const draftId = targetDraft?.draftId || pendingNewDraftId;
    const canonicalPersonId = selectedSavedPerson?.canonicalPersonId || targetDraft?.canonicalPersonId || "";
    const targetPersonId = selectedSavedPerson?.personId || canonicalPersonId || draftId;
    const initialPersonIds = new Set([
      explicitPersonId,
      existing?.draftId,
      existing?.canonicalPersonId,
      initialSavedPerson?.personId,
      initialSavedPerson?.draftId,
      initialSavedPerson?.canonicalPersonId,
    ].filter(Boolean).map(String));
    const targetPersonIds = new Set([
      targetPersonId,
      targetDraft?.draftId,
      targetDraft?.canonicalPersonId,
      selectedSavedPerson?.personId,
      selectedSavedPerson?.draftId,
      selectedSavedPerson?.canonicalPersonId,
    ].filter(Boolean).map(String));
    const retainsInitialIdentity = [...initialPersonIds].some((personId) => targetPersonIds.has(personId));
    const replacingExistingPerson = initialPersonIds.size > 0 && !retainsInitialIdentity;
    const confirmingSavedPerson = Boolean(
      selectedSavedPerson
      && selectedSavedPerson.choiceId !== initialSavedPerson?.choiceId
    );
    const save = button(
      confirmingSavedPerson
        ? "Confirm existing person"
        : replacingExistingPerson
          ? "Confirm different person"
          : existing
            ? "Save person"
            : "Confirm person",
      "primary",
      async () => {
        if (!form.name.trim()) {
          toast("Add a working name or character label for this person.");
          return;
        }
        if (allOccurrences.length && !form.occurrenceIds.size) {
          toast("Choose at least one appearance for this person.");
          return;
        }
        if (!allOccurrences.length && !form.sourceRefs.size) {
          toast("Choose at least one source for this person.");
          return;
        }
        if (
          confirmingSavedPerson
          && !globalThis.confirm(
            `Assign the selected appearances to ${selectedSavedPerson.displayName}? This will use the existing identity instead of creating a duplicate.`
          )
        ) return;
        if (!(await ensureReviewContextCurrent())) return;
        save.disabled = true;
        try {
          const representative = form.repName.trim() || form.repEmail.trim()
            ? { role: form.repRole, name: form.repName.trim() || undefined, email: form.repEmail.trim() || undefined }
            : undefined;
          const sourceRefs = mergeIdentityDraftSourceRefs(
            targetDraft?.sourceRefs || [],
            candidateSourceRefs,
            [...form.sourceRefs],
            Boolean(targetDraft)
          );
          const currentState = getState();
          const hostedPersonId = canonicalPersonId && projectPeople().some((person) =>
            String(person?.id || person?.talentRecordId || person?.talent_record_id || "")
              === String(canonicalPersonId)
          ) ? String(canonicalPersonId) : "";
          const hostedProjectId = hostedPersonId
            && currentState.activeProjectId
            && currentState.workflowBinding?.projectId === currentState.activeProjectId
            ? currentState.activeProjectId
            : "";
          if (hostedProjectId) {
            await updateProjectPerson(hostedProjectId, hostedPersonId, {
              displayName: form.name.trim(),
              role: form.role.trim() || undefined,
              representative,
            });
          }
          await saveLocalPersonDraft(workflowRef, {
            draftId,
            displayName: form.name.trim(),
            role: form.role.trim() || undefined,
            talentEmail: form.talentEmail.trim() || undefined,
            representative,
            notes: form.notes.trim() || undefined,
            sourceRefs,
            canonicalPersonId: canonicalPersonId || undefined,
          });
          await loadPersonDrafts(workflowRef, openedScanEpoch);
          if (!reviewContextIsCurrent()) {
            close();
            toast("The workflow changed. The person draft was saved, but no visual link was changed.");
            return;
          }
          const jobId = openedJobId;
          if (jobId) {
            try {
              const snapshot = await freshLinkSnapshot();
              if (!snapshot) {
                save.disabled = false;
                return;
              }
              const priorPersonId = explicitPersonId || existing?.canonicalPersonId || existing?.draftId || "";
              const links = identityLinksWithConfirmedDecision(
                snapshot.links,
                candidate.candidateId,
                {
                  personId: targetPersonId,
                  displayName: form.name.trim(),
                  occurrenceIds: [...form.occurrenceIds],
                  priorPersonId,
                  preservePriorUnselected: replacingExistingPerson,
                  preserveTargetExisting: confirmingSavedPerson,
                  targetPersonIds: [...targetPersonIds],
                  candidateOccurrenceIds: allOccurrences.map((occurrence) => String(occurrence.occurrenceId)),
                }
              );
              await commitIdentityLinks(jobId, links, snapshot.revision);
            } catch (error) {
              close();
              toast(
                identityRevisionConflict(error)
                  ? "Person saved, but visual review changed in another window. Reopen this person to confirm the appearances."
                  : "Person saved, but the visual cluster link needs to be confirmed again."
              );
              return;
            }
          }
          if (hostedProjectId) {
            try {
              const { syncCurrentRightsManifest } = await import("./sync-manifest.js");
              await syncCurrentRightsManifest(
                sourceLinkOverridesForExistingPerson(
                  candidateSourceRefs,
                  [...form.sourceRefs],
                  hostedPersonId
                )
              );
            } catch {
              close();
              toast("Person and appearances saved. The project source link is pending; reopen the person to retry.");
              return;
            }
          }
          close();
          toast(
            confirmingSavedPerson
              ? `Appearances assigned to ${selectedSavedPerson.displayName}. No duplicate person was created.`
              : replacingExistingPerson
              ? "Different person confirmed. The original person record was kept."
              : existing
                ? "Person updated."
                : "Person confirmed. Rights status is still pending."
          );
        } catch (error) {
          toast(error.message || "Could not save this person.");
          save.disabled = false;
        }
      }
    );
    return save;
  }

  renderDialog();
}

function sourcePreview(source, name) {
  const media = sourceMedia(source);
  if (!media?.url) return el("span", { text: "No visual preview" });
  if (media.kind === "video") {
    const video = el("video", { src: media.url, muted: "", playsinline: "", preload: "metadata", "aria-label": `${name} source video` });
    video.muted = true;
    return video;
  }
  return el("img", { src: media.url, alt: `${name} source preview`, loading: "lazy" });
}

function input(value, placeholder, type = "text", oninput = null) {
  const control = el("input", { class: "plb-input", value, placeholder, type });
  if (oninput) control.addEventListener("input", oninput);
  return control;
}

function formField(label, control, hint = "") {
  return el("label", { class: "plb-field" }, metaLabel(label), control, hint ? el("small", { class: "plb-field-hint", text: hint }) : null);
}

function selectControl(options, selected, onchange) {
  const control = el("select", { class: "plb-input" });
  for (const [value, label] of options) {
    const option = el("option", { value, text: label });
    option.selected = value === selected;
    control.append(option);
  }
  control.addEventListener("change", onchange);
  return control;
}
