import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

globalThis.crypto ??= webcrypto;

import { canonicalGraphJson, workflowFingerprint } from "../fingerprint.js";
import {
  emailAttemptDisposition,
  ensureClientRequestId,
  nextClientRequestId,
  shouldAdvanceDraftQueue,
} from "../invite-contract.js";
import {
  getState,
  invalidateScan,
  isWorkflowContextReady,
  markInvited,
  setState,
  wasInvited,
} from "../store.js";
import {
  canonicalRightsManifest,
  manifestOverridesForLocalReviews,
  manifestSourcesForScan,
  normalizedOperations,
  rightsManifestHash,
} from "../manifest.js";
import {
  aiActionRowsForLinks,
  hasRevocationPath,
  revocationPathRequired,
} from "../use-brief-contract.js";
import {
  appearanceSourcesForDisclosure,
  ensurePersonDraftId,
  identityJobSupportsAuthoritativeLinkScrub,
  linkedCanonicalPersonIds,
  personDraftPromotionPayload,
  visiblePersonDrafts,
} from "../person-drafts.js";
import {
  sourceMedia,
  sourceRecordsForScan,
  sourceSupportsNoPersonReview,
  sourceVariantCount,
} from "../source-records.js";
import {
  aggregateIdentityIssues,
  candidateNeedsReview,
  coverageLabel,
  groupOccurrencesBySource,
  identityLinksAfterPersonRemoval,
  identityLinksWithConfirmedDecision,
  identityLinksWithFalsePositiveDecision,
  identityLinksWithUnresolvedDecision,
  identityManualReviewItems,
  identityPresentationGroups,
  identityResultFromJob,
  normalizeIdentityPayload,
  plainLanguageUseSummary,
  sourceIssueNeedsManualReview,
  visualGroupingLabel,
} from "../identity-contract.js";
import {
  candidateDismissedOccurrenceIds,
  candidateHasActiveOccurrencesForSource,
  candidateRoleLabel,
  candidateIsFullyConfirmed,
  candidateIsResolved,
  candidateUnresolvedCount,
  draftForCandidate,
  filmstripColumns,
  identityReviewSummary,
  identitySuggestionProvenance,
  existingIdentityChoiceForId,
  existingIdentityChoices,
  manualIdentityDrafts,
  mergeIdentityDraftSourceRefs,
  progressDetailLabel,
  progressPhaseLabel,
  progressValue,
  representativeOccurrences,
  unresolvedManualSourceIssues,
} from "../identity-view.js";
import {
  analyzeWorkflowIdentity,
  commitIdentityLinks,
  identityRevisionConflict,
} from "../identity-analysis.js";

const GRAPH = {
  "2": {
    inputs: {
      strength: 1.0,
      ratio: 0.5,
      label: "é",
      flags: [true, false, null],
      nested: { z: 2.0, a: "x" },
    },
    class_type: "X",
  },
  "1": { inputs: {}, class_type: "Y" },
};

test("canonical graph fingerprint matches the cross-language fixture", async () => {
  assert.equal(
    canonicalGraphJson(GRAPH),
    '{"1":{"class_type":"Y","inputs":{}},"2":{"class_type":"X","inputs":{"flags":[true,false,null],"label":"é","nested":{"a":"x","z":2},"ratio":0.5,"strength":1}}}'
  );
  assert.equal(
    await workflowFingerprint(GRAPH),
    "f1eafde7905ff8c0cebf3a84d7ba45651441afd6e2f8411e877e3ad4414561ff"
  );
});

test("in-session invite state is scoped by workflow fingerprint", () => {
  const base = {
    workflow_name: "Morning People",
    source_kind: "reference",
    source_key: "marcus_ref.png",
    scope_statements: ["Use of their likeness"],
  };
  const first = { ...base, workflow_fingerprint: "a".repeat(64) };
  const changed = { ...base, workflow_fingerprint: "b".repeat(64) };

  markInvited(first);

  assert.equal(wasInvited(first), true);
  assert.equal(wasInvited(changed), false);
});

test("disconnected bulk drafting advances, connected failures do not", () => {
  const draft = { status: "draft", draft_reason: "disconnected" };
  assert.equal(shouldAdvanceDraftQueue(false, draft), true);
  assert.equal(shouldAdvanceDraftQueue(true, draft), false);
  assert.equal(
    shouldAdvanceDraftQueue(false, { status: "draft", draft_reason: "unconfirmed" }),
    false
  );
});

test("retry carries the first response request id", () => {
  const requestId = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4";
  assert.equal(nextClientRequestId("", { client_request_id: requestId }), requestId);
  assert.equal(nextClientRequestId(requestId, {}), requestId);
});

test("a thrown first attempt retains the pre-minted request id", async () => {
  const requestId = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4";
  let current = "";
  const fakeCrypto = {
    randomUUID: () => requestId,
  };

  try {
    current = ensureClientRequestId(current, fakeCrypto);
    throw new Error("local response lost");
  } catch {
    // The dialog stays open and retries with the captured value.
  }

  assert.equal(ensureClientRequestId(current, { randomUUID: () => "different" }), requestId);
});

test("email attempt state distinguishes retry from manual reconciliation", () => {
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "failed",
      email_attempt_state: "ambiguous",
    }),
    "retry_same_invite"
  );
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "failed",
      email_attempt_state: "in_flight",
    }),
    "retry_same_invite"
  );
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "failed",
      email_attempt_state: "manual_reconciliation",
      email_reconciliation_required: true,
    }),
    "manual_reconciliation"
  );
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "sent",
      email_attempt_state: "sent",
    }),
    "sent"
  );
});

test("extension registers graph-load invalidation through afterConfigureGraph", async () => {
  const source = await readFile(new URL("../pluribus.js", import.meta.url), "utf8");
  assert.match(source, /async afterConfigureGraph\(\)/);
  assert.match(source, /clearReticles\(\)/);
  assert.match(source, /invalidateScan\(\)/);
});

test("graph-load invalidation clears the previous workflow action context", () => {
  setState({
    scan: { workflow_name: "Old", workflow_fingerprint: "a".repeat(64), persons: [] },
    workflow: { old: true },
    workflowBinding: {
      workflowRef: "11111111-1111-4111-8111-111111111111",
      projectId: "project-old",
    },
    activeProjectId: "project-old",
    projectContext: {
      workflow: { workflowRef: "11111111-1111-4111-8111-111111111111" },
      people: [{ id: "person-old" }],
    },
    sourceRefs: { old: "b".repeat(64) },
    manifestSynced: true,
  });
  assert.equal(isWorkflowContextReady(), true);

  invalidateScan();

  assert.equal(isWorkflowContextReady(), false);
  assert.equal(getState().workflowBinding, null);
  assert.equal(getState().projectContext, null);
  assert.equal(getState().activeProjectId, null);
  assert.deepEqual(getState().sourceRefs, {});
});

test("identity capability failures leave analysis recoverable", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => {
    throw new Error("local capability route unavailable");
  };
  try {
    await analyzeWorkflowIdentity({
      workflow: {},
      workflowName: "Failure contract",
      workflowFingerprint: "a".repeat(64),
      workflowBinding: null,
      scan: { persons: [] },
    });
    assert.equal(getState().identityAnalyzing, false);
    assert.match(getState().identityError?.message || "", /capability route unavailable/);
  } finally {
    globalThis.fetch = originalFetch;
    invalidateScan();
  }
});

test("completed identity jobs replace stale links with their normalized payload links", async () => {
  const originalFetch = globalThis.fetch;
  const jobId = "11111111-1111-4111-8111-111111111111";
  const personId = "22222222-2222-4222-8222-222222222222";
  const paths = [];
  globalThis.fetch = async (path) => {
    paths.push(String(path));
    const payload = path === "/pluribus/identity/capabilities"
      ? { state: "ready" }
      : {
          job_id: jobId,
          state: "completed",
          links_revision: 7,
          result: {
            candidates: [{ candidate_id: "candidate-a", occurrence_ids: ["occurrence-a"] }],
            occurrences: [{ occurrence_id: "occurrence-a", candidate_id: "candidate-a" }],
          },
          links: [{
            candidate_id: "candidate-a",
            person_id: personId,
            display_name: "Layla",
            state: "confirmed",
            occurrence_ids: ["occurrence-a"],
          }],
        };
    return { ok: true, status: 200, json: async () => payload };
  };
  setState({
    identityJob: null,
    identityLinks: [{ candidateId: "stale-candidate", personId: "stale-person", state: "confirmed" }],
    identityLinksRevision: 99,
  });
  try {
    await analyzeWorkflowIdentity({
      workflowName: "Atomic link contract",
      workflowFingerprint: "a".repeat(64),
      workflowBinding: { workflowRef: "33333333-3333-4333-8333-333333333333" },
      scan: { persons: [] },
    });
    assert.equal(getState().identityAnalyzing, false);
    assert.equal(getState().identityPayload?.candidates[0].candidateId, "candidate-a");
    assert.equal(getState().identityLinksRevision, 7);
    assert.deepEqual(getState().identityLinks, [{
      candidate_id: "candidate-a",
      person_id: personId,
      display_name: "Layla",
      state: "confirmed",
      occurrence_ids: ["occurrence-a"],
      candidateId: "candidate-a",
      personId,
      displayName: "Layla",
      occurrenceIds: ["occurrence-a"],
    }]);
    assert.deepEqual(paths, [
      "/pluribus/identity/capabilities",
      "/pluribus/identity/analyze",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
    invalidateScan();
  }
});

test("identity link writes compare revisions and reject a stale second writer", async () => {
  const originalFetch = globalThis.fetch;
  const jobId = "11111111-1111-4111-8111-111111111111";
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path: String(path), body: JSON.parse(init.body) });
    if (requests.length === 1) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ jobId, revision: 4, links: init ? JSON.parse(init.body).links : [] }),
      };
    }
    return {
      ok: false,
      status: 409,
      json: async () => ({ message: "Identity links revision conflict; refresh and retry." }),
    };
  };
  setState({ identityJob: { jobId, state: "completed" }, identityLinks: [], identityLinksRevision: 3 });
  const links = [{ candidateId: "candidate-a", state: "unsure" }];
  try {
    const first = await commitIdentityLinks(jobId, links, 3);
    assert.equal(first.revision, 4);
    assert.equal(getState().identityLinksRevision, 4);
    assert.deepEqual(requests[0].body, { baseRevision: 3, links });
    await assert.rejects(
      () => commitIdentityLinks(jobId, [{ candidateId: "candidate-b", state: "unsure" }], 3),
      (error) => {
        assert.equal(identityRevisionConflict(error), true);
        return true;
      }
    );
    assert.equal(getState().identityLinksRevision, 4, "a stale writer cannot replace the accepted revision");
  } finally {
    globalThis.fetch = originalFetch;
    invalidateScan();
  }
});

test("native sidebar rerenders reuse one panel mount and one subscription", async () => {
  const source = await readFile(new URL("../panel.js", import.meta.url), "utf8");

  assert.match(
    source,
    /mountedContainer === container && root && container\.contains\(root\)/
  );
  assert.match(source, /unsubscribePanel = subscribe\(/);
  assert.match(source, /export function unmountPanel\(\)/);
  assert.match(source, /unsubscribePanel\?\.\(\)/);
  assert.ok(source.indexOf("unmountPanel();") < source.indexOf('root = el("div", { class: "plb-root" })'));
  assert.match(source, /identityOnly.*activeTab === "overview".*activeTab === "people"/s);
});

test("source editing routes split identity groups to the People view", async () => {
  const source = await readFile(new URL("../panel.js", import.meta.url), "utf8");

  assert.match(source, /identityLinksForCandidate/);
  assert.match(source, /confirmedPersonIds\.length > 1/);
  assert.match(
    source,
    /confirmedPersonIds\.length > 1[\s\S]*setActiveTab\("people"\)[\s\S]*visual group is split across/
  );
  assert.match(
    source,
    /confirmedPersonIds\[0\] \|\| draft\?\.canonicalPersonId \|\| draft\?\.draftId/
  );
});

test("source review cannot mark active identity evidence as no person", async () => {
  const source = await readFile(new URL("../panel.js", import.meta.url), "utf8");

  assert.match(source, /const noPersonBlocked = Boolean\(/);
  assert.match(source, /button\("Return source to review"/);
  assert.match(source, /currentSourceHash && !manualIssue && !noPersonBlocked/);
  assert.match(source, /noPersonConflict[\s\S]*"Review conflict"/);
  assert.match(source, /prior no-person classification conflicts with detected or assigned people/);
  assert.match(
    source,
    /if \(reviewState === "not_person"\)[\s\S]*identityCandidatesForSource[\s\S]*activeIdentityLinks[\s\S]*linkedPeopleForSource[\s\S]*localDraftsForPerson/
  );
  assert.match(source, /Review those people before classifying the source as containing no person/);
  assert.match(source, /sourceSupportsNoPersonReview/);
  assert.match(source, /Audio and other performance sources require their own rights review/);
});

test("workflow switches and rescans cancel stale identity jobs", async () => {
  const extension = await readFile(new URL("../pluribus.js", import.meta.url), "utf8");
  const analysis = await readFile(new URL("../identity-analysis.js", import.meta.url), "utf8");
  assert.match(extension, /cancelIdentityAnalysis\(\{ remove: true \}\)/);
  assert.match(analysis, /priorJobId && !finished\(priorJobState\)/);
  assert.match(analysis, /deleteIdentityAnalysisJob\(priorJobId\)/);
});

test("only the newest overlapping scan can update panel state", async () => {
  const source = await readFile(new URL("../panel.js", import.meta.url), "utf8");
  assert.match(source, /const requestId = \+\+scanRequestId/);
  assert.match(source, /requestId === scanRequestId && getState\(\)\.scanEpoch === scanEpoch/);
  assert.match(source, /await priorScan/);
  assert.match(source, /releaseScan\(\)/);
  assert.match(source, /if \(!isCurrent\(\)\) return/);
});

test("legacy output rows collapse into one exact source with variant count", () => {
  const rows = sourceRecordsForScan([
    {
      source_kind: "reference",
      source_key: "Actor.png",
      source_node_id: "1",
      output_node_id: "5",
      output_node_ids: ["5"],
      occurrences: [{ source_node_id: "1", output_node_id: "5" }],
      ops: [{ node_id: "2", class_type: "FluxKontextProImageNode", source_role: "reference_image" }],
    },
    {
      source_kind: "reference",
      source_key: "Actor.png",
      source_node_id: "1",
      output_node_id: "6",
      output_node_ids: ["6"],
      occurrences: [{ source_node_id: "1", output_node_id: "6" }],
      ops: [{ node_id: "2", class_type: "FluxKontextProImageNode", source_role: "init_image" }],
    },
    {
      source_kind: "reference",
      source_key: "actor.png",
      source_node_id: "7",
      output_node_id: "8",
      output_node_ids: ["8"],
    },
  ]);

  assert.equal(rows.length, 2, "source keys retain exact case identity");
  assert.equal(sourceVariantCount(rows[0]), 2);
  assert.deepEqual(rows[0].output_node_ids, ["5", "6"]);
  assert.deepEqual(
    rows[0].ops.map((operation) => operation.source_role),
    ["reference_image", "init_image"],
    "one node can use the same source in distinct rights-relevant roles"
  );
  assert.equal(sourceVariantCount({ output_node_ids: [], occurrences: [] }), 0);
});

test("source previews use ComfyUI basename, subfolder, and annotation fields", () => {
  assert.deepEqual(
    sourceMedia({
      source_key: "shots/day 1/frame.png [output]",
      provenance: ["LoadImage"],
    }),
    {
      kind: "image",
      url: "/api/view?filename=frame.png&type=output&subfolder=shots%2Fday+1&preview=webp%3B80",
    }
  );
  assert.deepEqual(
    sourceMedia({ source_key: "takes/performance.mp4", provenance: ["LoadVideo"] }),
    {
      kind: "video",
      url: "/api/view?filename=performance.mp4&type=input&subfolder=takes",
    }
  );
  assert.equal(sourceSupportsNoPersonReview({
    source_key: "shots/day 1/frame.png [output]",
    provenance: ["LoadImage"],
  }), true);
  assert.equal(sourceSupportsNoPersonReview({
    source_key: "takes/performance.mp4",
    provenance: ["LoadVideo"],
  }), true);
  assert.equal(sourceSupportsNoPersonReview({
    source_key: "takes/performance.wav",
    provenance: ["LoadAudio"],
  }), false);
});

test("preview media falls back to the existing avatar when loading fails", async () => {
  const source = await readFile(new URL("../components.js", import.meta.url), "utf8");
  assert.match(source, /preview\.addEventListener\("error", \(\) => preview\.remove\(\)\)/);
  assert.match(source, /media\.kind === "video" \? "video" : "img"/);
});

test("new person drafts mint one stable id for first write and retries", () => {
  let calls = 0;
  const fakeCrypto = {
    randomUUID() {
      calls += 1;
      return "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4";
    },
  };
  const first = ensurePersonDraftId("", fakeCrypto);
  const retry = ensurePersonDraftId(first, fakeCrypto);
  assert.equal(retry, first);
  assert.equal(calls, 1);
});

test("person draft UI sends its stable id and guards double submit", async () => {
  const source = await readFile(new URL("../person-drafts.js", import.meta.url), "utf8");
  assert.match(source, /draftId: selected\?\.draftId \|\| pendingNewDraftId/);
  assert.match(source, /if \(saving\) return/);
  assert.match(source, /preservedSourceRefs/);
  assert.match(source, /sourceRecords\.unshift/);
  assert.match(source, /Source not in current scan/);
  assert.match(source, /sourceRefs,/);
  assert.match(source, /canonicalPersonId: selected\?\.canonicalPersonId/);
  assert.match(source, /One person can also appear in several sources/);
  assert.match(source, /manifestSynced: false/);
  assert.match(source, /await syncCurrentRightsManifest\(\)/);
  assert.match(source, /const openedScanEpoch = state\.scanEpoch/);
  assert.match(source, /reviewContextIsCurrent/);
  assert.match(source, /loadPersonDrafts\(workflowRef, openedScanEpoch\)/);
  assert.match(source, /Who is visible in this source\?/);
  assert.match(source, /Optional contact, representative, and notes/);
  assert.match(source, /Appears elsewhere/);
  assert.match(source, /Search scenes, characters, or filenames/);
  assert.match(source, /checkbox\.disabled = isCurrentSource/);
  assert.match(source, /role: "dialog"/);
  assert.match(source, /"aria-modal": "true"/);
  assert.match(source, /"aria-labelledby": titleId/);
  assert.match(source, /"aria-label": "Close person details"/);
  assert.match(source, /previousFocus\?\.focus\?\.\(\)/);
  assert.match(source, /dialog\.querySelectorAll\(/);
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /data-autofocus/);
  assert.match(source, /scanMatchesCurrentWorkflow\(openedScan\)/);
  assert.match(source, /identityLinksAfterPersonRemoval/);
  assert.match(source, /identityJobSupportsAuthoritativeLinkScrub\(latest\.identityJob\)/);
  assert.match(source, /Run identity analysis to completion/);
  assert.match(source, /const snapshot = await refreshIdentityLinks\(jobId\)/);
  assert.match(source, /Always perform the revision-checked write/);
  assert.match(source, /commitIdentityLinks\(jobId, links, snapshot\.revision\)/);
  assert.doesNotMatch(source, /!jobId && locallyConfirmed/);
});

test("person draft deletion fails closed after restart without a completed identity job", () => {
  assert.equal(identityJobSupportsAuthoritativeLinkScrub(null), false);
  assert.equal(identityJobSupportsAuthoritativeLinkScrub({ jobId: "job-1", state: "queued" }), false);
  assert.equal(identityJobSupportsAuthoritativeLinkScrub({ jobId: "job-1", state: "failed" }), false);
  assert.equal(identityJobSupportsAuthoritativeLinkScrub({ jobId: "job-1", state: "completed" }), true);
  assert.equal(identityJobSupportsAuthoritativeLinkScrub({ job_id: "job-2", status: "succeeded" }), true);
});

test("manual person details keep the current source fixed and disclose other sources by search", () => {
  const currentSourceRef = "a".repeat(64);
  const alreadyAddedRef = "b".repeat(64);
  const sceneRef = "c".repeat(64);
  const records = [
    { sourceRef: currentSourceRef, source_key: "SC01_current_storyboard.png" },
    { sourceRef: alreadyAddedRef, source_key: "SC02_existing_storyboard.png" },
    { sourceRef: sceneRef, source_key: "SC08_garden_party_storyboard.png" },
    ...Array.from({ length: 15 }, (_, index) => ({
      sourceRef: String(index + 10).padStart(64, "0"),
      source_key: `SC08_party_candidate_${index + 1}.png`,
    })),
  ];

  const collapsed = appearanceSourcesForDisclosure(
    records,
    currentSourceRef,
    [currentSourceRef, alreadyAddedRef],
    ""
  );
  assert.deepEqual(collapsed.sources.map((source) => source.sourceRef), [alreadyAddedRef]);
  assert.equal(collapsed.selectedCount, 1);
  assert.equal(collapsed.hiddenCount, 0);

  const searched = appearanceSourcesForDisclosure(
    records,
    currentSourceRef,
    [currentSourceRef, alreadyAddedRef],
    "garden"
  );
  assert.deepEqual(
    searched.sources.map((source) => source.sourceRef),
    [alreadyAddedRef, sceneRef]
  );
  assert.ok(!searched.sources.some((source) => source.sourceRef === currentSourceRef));

  const bounded = appearanceSourcesForDisclosure(
    records,
    currentSourceRef,
    [currentSourceRef],
    "party",
    5
  );
  assert.equal(bounded.sources.length, 5);
  assert.equal(bounded.hiddenCount, 11);

  const manyExisting = appearanceSourcesForDisclosure(
    records,
    currentSourceRef,
    records.slice(1, 15).map((source) => source.sourceRef),
    "",
    5
  );
  assert.equal(manyExisting.sources.length, 14);
  assert.equal(manyExisting.selectedCount, 14);
  assert.equal(manyExisting.hiddenCount, 0);
});

test("draft promotion preserves fields and visibility follows exact canonical links", () => {
  const draft = {
    draftId: "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4",
    displayName: "Performer",
    role: "Lead",
    talentEmail: "performer@example.com",
    representative: { role: "agent", name: "Representative" },
    notes: "Night exterior",
    sourceRefs: ["a".repeat(64), "b".repeat(64)],
  };
  assert.deepEqual(personDraftPromotionPayload(draft, "person-1"), {
    ...draft,
    representative: { ...draft.representative },
    sourceRefs: [...draft.sourceRefs],
    canonicalPersonId: "person-1",
  });

  const linkedIds = linkedCanonicalPersonIds([
    { disposition: "linked", talentRecordIds: ["person-1"] },
    { disposition: "review_required", talentRecordIds: ["person-2"] },
  ]);
  const drafts = [
    { ...draft, canonicalPersonId: "person-1" },
    { ...draft, draftId: "draft-2", canonicalPersonId: "person-2" },
    { ...draft, draftId: "draft-3" },
  ];
  assert.deepEqual(
    visiblePersonDrafts(drafts, linkedIds).map((item) => item.draftId),
    ["draft-2", "draft-3"]
  );
  assert.equal(visiblePersonDrafts(drafts, new Set()).length, 3);
});

test("canonical person linking can choose and prefill local details", async () => {
  const source = await readFile(new URL("../link-person.js", import.meta.url), "utf8");
  assert.match(source, /const drafts = localDraftsForPerson\(person, state\)/);
  assert.match(source, /fillFromDraft\(drafts\[0\]\)/);
  assert.match(source, /drafts\.length > 1/);
  assert.match(source, /if \(createNew\.checked\)/);
  assert.match(source, /let selectedDraft = drafts\[0\] \|\| null/);
  assert.match(source, /selectedExistingIds\.length === 1/);
  assert.match(source, /await markPersonDraftPromoted\(selectedDraft, promotedCanonicalId\)/);
  assert.ok(source.indexOf("toast(`Linked") < source.indexOf("await markPersonDraftPromoted"));
  assert.match(source, /People were linked, but the local details could not be marked as linked/);
  assert.match(source, /option\("rights_holder", "Rights holder"\)/);
});

test("rights manifest ignores non-rights graph details and is order stable", async () => {
  const workflowRef = "123e4567-e89b-42d3-a456-426614174000";
  const sources = [
    {
      sourceRef: "b".repeat(64),
      sourceKind: "reference",
      disposition: "linked",
      talentRecordIds: ["person-b", "person-a"],
      operations: [{ classType: "IPAdapter" }, { classType: "LoadImage" }],
      displayLabel: "Local label is not material",
      nodeId: 99,
    },
  ];
  const reordered = [{
    ...sources[0],
    talentRecordIds: ["person-a", "person-b"],
    operations: [{ classType: "LoadImage" }, { classType: "IPAdapter" }],
    displayLabel: "Renamed locally",
    sampler: "changed",
  }];

  assert.equal(
    canonicalRightsManifest(workflowRef, sources, "storyboard"),
    canonicalRightsManifest(workflowRef, reordered, "storyboard")
  );
  assert.equal(
    await rightsManifestHash(workflowRef, sources, "storyboard"),
    await rightsManifestHash(workflowRef, reordered, "storyboard")
  );
  assert.doesNotMatch(canonicalRightsManifest(workflowRef, sources), /Local label|nodeId|sampler/);
});

test("rights manifest matches the Python and TypeScript contract fixture", async () => {
  assert.equal(
    await rightsManifestHash(
      "11111111-1111-4111-8111-111111111111",
      [{
        sourceRef: "b".repeat(64),
        sourceKind: "lora",
        disposition: "linked",
        talentRecordIds: ["33333333-3333-4333-8333-333333333333"],
        operations: [
          { classType: "ReActorFaceSwap" },
          { classType: "IPAdapter" },
        ],
      }],
      "storyboard"
    ),
    "1c8d11070aa7066dafda1e7944614fc9ed95d88b9a4f6119f8d80ac1618caa7c"
  );
});

test("only source-scoped normalized rights operations enter the manifest", () => {
  assert.deepEqual(
    normalizedOperations({
      ops: [
        { class_type: "KSampler", node_id: "8" },
        { class_type: "IPAdapter", node_id: "9" },
        { class_type: "SaveImage", node_id: "10" },
      ],
      provenance: ["LoadImage", "IPAdapter"],
    }),
    [{ classType: "IPAdapter" }]
  );
});

test("video input and restyle operations enter rights manifests", () => {
  assert.deepEqual(
    normalizedOperations({
      ops: [
        { class_type: "LoadVideo", node_id: "1" },
        { class_type: "RunwayAleph2VideoToVideoNode", node_id: "2" },
      ],
    }),
    [
      { classType: "LoadVideo" },
      { classType: "RunwayAleph2VideoToVideoNode" },
    ]
  );
  assert.deepEqual(
    aiActionRowsForLinks(
      [{
        talentRecordIds: ["person-video"],
        operations: [
          { classType: "LoadVideo" },
          { classType: "RunwayAleph2VideoToVideoNode" },
        ],
      }],
      false
    ),
    [
      {
        talentRecordId: "person-video",
        modality: "biometric_input",
        action: "process",
        requiresFinalApproval: false,
      },
      {
        talentRecordId: "person-video",
        modality: "synthetic_performance",
        action: "edit",
        requiresFinalApproval: false,
      },
    ]
  );
});

test("paid or external activation requires an actionable revocation path", () => {
  assert.equal(revocationPathRequired(true, ["Internal"], []), true);
  assert.equal(revocationPathRequired(false, ["Organic social"], []), true);
  assert.equal(revocationPathRequired(false, ["Internal"], ["TikTok"]), true);
  assert.equal(revocationPathRequired(false, ["Internal", "Private review"], []), false);

  assert.equal(hasRevocationPath("Contact the producer for takedown.", false, false), true);
  assert.equal(hasRevocationPath("", true, false), true);
  assert.equal(hasRevocationPath("", false, true), true);
  assert.equal(hasRevocationPath("", false, false), false);
});

test("inferred AI actions stay scoped to each linked person", () => {
  assert.deepEqual(
    aiActionRowsForLinks(
      [
        {
          talentRecordIds: ["person-a", "person-b"],
          operations: [{ classType: "IPAdapter" }, { classType: "KSampler" }],
        },
        {
          talentRecordIds: ["person-a"],
          operations: [{ classType: "KlingImage2VideoNode" }, { classType: "IPAdapter" }],
        },
      ],
      true
    ),
    [
      {
        talentRecordId: "person-a",
        modality: "face",
        action: "generate",
        requiresFinalApproval: true,
      },
      {
        talentRecordId: "person-a",
        modality: "synthetic_performance",
        action: "render",
        requiresFinalApproval: true,
      },
      {
        talentRecordId: "person-b",
        modality: "face",
        action: "generate",
        requiresFinalApproval: true,
      },
    ]
  );
});

test("fresh scans reconcile added, removed, and operation-changed sources", async () => {
  const firstPerson = {
    source_kind: "reference",
    source_key: "local-a",
    source_node_id: "1",
    ops: [{ class_type: "IPAdapter" }],
  };
  const addedPerson = {
    source_kind: "lora",
    source_key: "local-c",
    source_node_id: "3",
    ops: [{ class_type: "LoraLoader" }],
  };
  const refs = {
    "reference|local-a|1": "a".repeat(64),
    "reference|local-a|4": "a".repeat(64),
    "reference|local-b|2": "b".repeat(64),
    "lora|local-c|3": "c".repeat(64),
  };
  const existing = [
    {
      sourceRef: "a".repeat(64),
      sourceKind: "reference",
      disposition: "linked",
      talentRecordIds: ["person-1"],
      operations: [{ classType: "IPAdapter" }],
    },
    {
      sourceRef: "b".repeat(64),
      sourceKind: "reference",
      disposition: "not_person",
      talentRecordIds: [],
      operations: [{ classType: "LoadImage" }],
    },
  ];
  const rescanned = manifestSourcesForScan(
    [
      { ...firstPerson, ops: [{ class_type: "KlingImage2VideoNode" }] },
      {
        ...firstPerson,
        source_node_id: "4",
        ops: [{ class_type: "LoadImage" }],
      },
      addedPerson,
    ],
    refs,
    existing
  );

  assert.deepEqual(rescanned.map((source) => source.sourceRef), ["a".repeat(64), "c".repeat(64)]);
  assert.equal(rescanned[0].disposition, "linked");
  assert.deepEqual(rescanned[0].talentRecordIds, ["person-1"]);
  assert.deepEqual(rescanned[0].operations, [
    { classType: "KlingImage2VideoNode" },
    { classType: "LoadImage" },
  ]);
  assert.equal(rescanned[1].disposition, "review_required");

  const workflowRef = "11111111-1111-4111-8111-111111111111";
  const before = await rightsManifestHash(workflowRef, existing, "storyboard");
  const after = await rightsManifestHash(workflowRef, rescanned, "storyboard");
  assert.notEqual(after, before);
  assert.notEqual(
    await rightsManifestHash(workflowRef, existing, "character_sheet"),
    before
  );
  assert.equal(
    await rightsManifestHash(workflowRef, existing, "storyboard"),
    before,
    "graph-only details do not enter the manifest"
  );
});

test("rescan and workflow-kind changes persist the complete current manifest", async () => {
  const [panel, project, sync, request, use] = await Promise.all([
    readFile(new URL("../panel.js", import.meta.url), "utf8"),
    readFile(new URL("../project.js", import.meta.url), "utf8"),
    readFile(new URL("../sync-manifest.js", import.meta.url), "utf8"),
    readFile(new URL("../request-confirmation.js", import.meta.url), "utf8"),
    readFile(new URL("../use-brief.js", import.meta.url), "utf8"),
  ]);
  assert.match(panel, /await syncCurrentRightsManifest\(\)/);
  assert.match(project, /await syncCurrentRightsManifest\(\)/);
  assert.match(sync, /manifestSourcesForScan/);
  assert.match(sync, /let syncRequestId = 0/);
  assert.match(sync, /let syncBarrier = Promise\.resolve\(\)/);
  assert.match(sync, /latest\.scanEpoch === scanEpoch/);
  assert.match(sync, /if \(!contextIsCurrent\(\)\) return null/);
  assert.match(sync, /saveProjectSourceLinks/);
  assert.match(request, /scanMatchesCurrentWorkflow/);
  assert.match(use, /scanMatchesCurrentWorkflow/);
});

test("safe retry freezes mutable invite controls and sends the captured id", async () => {
  const source = await readFile(new URL("../invite.js", import.meta.url), "utf8");
  assert.match(source, /emailInput\.disabled = true/);
  assert.match(source, /noteInput\.disabled = true/);
  assert.match(source, /segEmail\.disabled = true/);
  assert.match(source, /segLink\.disabled = true/);
  assert.match(source, /clientRequestId,/);
  assert.ok(source.indexOf("freezeRequest();") < source.indexOf("await sendInvite"));
});

test("launch runtime uses link, use, request, and independent review semantics", async () => {
  const paths = [
    "../components.js",
    "../panel.js",
    "../pluribus.js",
    "../project.js",
    "../link-person.js",
    "../people.js",
    "../use-brief.js",
    "../request-confirmation.js",
    "../sync-manifest.js",
    "../identity-view.js",
  ];
  const source = (
    await Promise.all(paths.map((path) => readFile(new URL(path, import.meta.url), "utf8")))
  ).join("\n");

  assert.doesNotMatch(
    source,
    /Performance altered by|workflow is ready|CONSENT LIVE|we used your likeness|Invite for terms|ROSTER LINKED|Terms accepted|Sarah Chen/
  );
  assert.match(source, /Used for/);
  assert.match(source, /Visual review stays local/);
  assert.match(source, /identity confidence never means rights clearance|never a clearance decision/i);
  assert.match(source, /Link to Pluribus/);
  assert.match(source, /Request confirmation/);
  assert.match(source, /response remains separate from internal review|stay separate from your team's internal review/is);
});

test("intended use preserves canonical per-person terms and revocation controls", async () => {
  const source = await readFile(new URL("../use-brief.js", import.meta.url), "utf8");

  assert.match(source, /const terms = person\.terms \|\| \{\}/);
  assert.match(source, /terms\.compensation/);
  assert.match(source, /terms\.usageComfort/);
  assert.match(source, /terms\.restrictions/);
  assert.match(source, /terms\.repAuthority/);
  assert.match(source, /peopleUseRows\(personTerms\)/);
  assert.doesNotMatch(source, /usageComfort:\s*null/);
  assert.doesNotMatch(source, /representativeAuthority:\s*null/);
  assert.doesNotMatch(source, /revocationInstructions:\s*null/);
  assert.match(source, /modelDisableRequired: modelDisableRequired\.checked/);
  assert.match(source, /platformRemovalRequired: platformRemovalRequired\.checked/);
  assert.match(source, /workflow\.useBriefManifestHash === workflow\.manifestHash/);
  assert.match(source, /workflow\.useBriefScopeVersion === scope\.versionNumber/);
});

test("confirmation preview exposes the exact rights-relevant producer scope", async () => {
  const source = await readFile(new URL("../request-confirmation.js", import.meta.url), "utf8");
  for (const label of [
    "Languages",
    "Product category",
    "Inferred AI actions",
    "Person compensation",
    "Person restrictions",
    "Usage comfort / caveats",
    "Representative authority notes",
    "Revocation / takedown instructions",
    "Takedown SLA",
    "Model disablement on revocation",
    "Platform removal on revocation",
  ]) {
    assert.match(source, new RegExp(label.replaceAll("/", "\\/")));
  }
  assert.match(source, /response remains separate from internal review/);
  assert.match(source, /context\.workflow\?\.manifestHash \|\| context\.manifestHash/);
  assert.match(source, /rightsManifestHash,/);
  assert.doesNotMatch(source, /rightsManifestHash:\s*state\.workflowBinding\?\.manifestHash/);
});

test("source cards stay available as a technical audit with progressive disclosure", async () => {
  const source = await readFile(new URL("../panel.js", import.meta.url), "utf8");
  assert.match(source, /sourceDisplayLabel\(person\)/);
  assert.match(source, /this list is the technical audit/i);
  assert.match(source, /expanded\.has\(key\)/);
  assert.match(source, /opsChips\(person/);
  assert.match(source, /Used in \$\{variantCount\} variants/);
  assert.doesNotMatch(source, /Local detection|Local scan|Local node/i);
  assert.match(source, /incomplete Pluribus/);
  assert.match(source, /markers were.*ignored/);
  assert.match(source, /issue\.code === "incomplete_source_marker"/);
  assert.match(source, /issue\.code === "unsupported_terminal_node"/);
  assert.match(source, /downstream-use coverage may be incomplete/);
  const publicSummary = source.slice(source.indexOf("function sourcesBody"), source.indexOf("function scanIssueDetails"));
  assert.doesNotMatch(publicSummary, /issue\.node_id/);
  assert.match(source.slice(source.indexOf("function scanIssueDetails")), /issue\.node_id/);
  assert.match(source, /sourceNeedsManualReview/);
  assert.match(source, /No person here/);
  assert.match(source, /sourceHasCurrentNoPersonReview/);
  assert.match(source, /sourceReviewChanged/);
  assert.doesNotMatch(source, /setCanonicalDisposition/);
});

test("identity job payload normalizes aliases and joins candidate occurrences", () => {
  const payload = normalizeIdentityPayload({
    links_revision: 9,
    coverage: { total_sources: 4, analyzed_sources: 3, image_count: 2, video_count: 1 },
    candidates: [{
      candidate_id: "person-1",
      suggested_name: "Layla",
      suggested_role: "Lead",
      confidence: 0.93,
      occurrence_ids: ["face-1"],
      evidence: [
        "/pluribus/identity/evidence/person-1.webp",
        "character sheet label",
        { description: "recurs across scenes" },
      ],
    }],
    occurrences: [{
      occurrence_id: "face-1",
      candidate_id: "person-1",
      source_ref: "a".repeat(64),
      source_label: "scene-01.png",
      crop_url: "/pluribus/identity/crops/face-1.webp",
      scene_label: "Scene 01",
    }],
    links: [{
      candidate_id: "person-1",
      person_id: "22222222-2222-4222-8222-222222222222",
      display_name: "Layla",
      state: "confirmed",
      occurrence_ids: ["face-1"],
    }],
    issues: [{ candidate_id: "person-1", title: "Compare extra", description: "Crowd frame" }],
  });

  assert.equal(payload.coverage.total, 4);
  assert.equal(payload.coverage.analyzed, 3);
  assert.equal(payload.coverage.skipped, 1);
  assert.equal(payload.candidates[0].suggestedName, "Layla");
  assert.equal(payload.candidates[0].occurrences[0].cropUrl, "/pluribus/identity/crops/face-1.webp");
  assert.deepEqual(payload.candidates[0].sourceRefs, ["a".repeat(64)]);
  assert.deepEqual(payload.candidates[0].evidenceImages, ["/pluribus/identity/evidence/person-1.webp"]);
  assert.deepEqual(payload.candidates[0].evidence, ["character sheet label", "recurs across scenes"]);
  assert.doesNotMatch(payload.candidates[0].evidence.join(" "), /\/pluribus\/identity\/evidence/);
  assert.equal(payload.issues[0].candidateId, "person-1");
  assert.deepEqual(payload.links[0].occurrenceIds, ["face-1"]);
  assert.equal(payload.links[0].personId, "22222222-2222-4222-8222-222222222222");
  assert.equal(payload.linksRevision, 9);
  assert.equal(identityResultFromJob({ state: "running", ...payload }), null);
  assert.equal(identityResultFromJob({ state: "completed", ...payload }).candidates.length, 1);
  assert.equal(identityResultFromJob({
    state: "completed",
    result: { candidates: [{ candidate_id: "person-1" }] },
    linksRevision: 10,
    links: [{ candidate_id: "person-1", person_id: "person-from-envelope" }],
  }).links[0].personId, "person-from-envelope");
  assert.equal(identityResultFromJob({
    state: "completed",
    result: { candidates: [] },
    linksRevision: 10,
  }).linksRevision, 10);
});

test("identity review groups visual occurrences by source and keeps ambiguity explicit", () => {
  const identity = normalizeIdentityPayload({
    candidates: [{ candidateId: "person-1", confidence: 0.81, state: "ambiguous" }],
    occurrences: [
      { occurrenceId: "one", candidateId: "person-1", sourceRef: "a", sourceLabel: "Scene A" },
      { occurrenceId: "two", candidateId: "person-1", sourceRef: "a", sourceLabel: "Scene A" },
      { occurrenceId: "three", candidateId: "person-1", sourceRef: "b", sourceLabel: "Scene B" },
    ],
  });
  const candidate = identity.candidates[0];
  assert.deepEqual(groupOccurrencesBySource(candidate, identity).map((group) => [group.sourceRef, group.occurrences.length]), [["a", 2], ["b", 1]]);
  assert.equal(candidateNeedsReview(candidate, identity), true);
  assert.equal(visualGroupingLabel({ groupingBand: "likely" }, identity), "Likely visual grouping");
  assert.equal(visualGroupingLabel(candidate, identity), "Mixed visual grouping");
  assert.equal(coverageLabel({ analyzed: 3, total: 4, skipped: 1 }), "3 of 4 sources analyzed · 1 skipped");
});

test("repeated identity analysis issues collapse into readable topics", () => {
  const grouped = aggregateIdentityIssues([
    { code: "ambiguous_identity", title: "Appearance needs identity review", description: "one" },
    { code: "ambiguous_identity", title: "Appearance needs identity review", description: "two" },
    { code: "media_unreadable", title: "Media could not be read", description: "missing codec" },
  ]);
  assert.equal(grouped.length, 2);
  assert.deepEqual(grouped[0], {
    code: "ambiguous_identity",
    title: "2 appearances need comparison",
    description: "These appearances were close to more than one likely person. Review them on the relevant person card.",
    count: 2,
  });
  assert.equal(grouped[1].title, "Media could not be read");
});

test("shared scene sources never confirm two identity candidates by source overlap alone", () => {
  const first = { candidateId: "candidate-a", sourceRefs: ["shared-scene"] };
  const second = { candidateId: "candidate-b", sourceRefs: ["shared-scene"] };
  const draft = { draftId: "person-a", displayName: "Layla", sourceRefs: ["shared-scene"] };
  const base = {
    identityPayload: { candidates: [first, second] },
    identityLinks: [],
    personDrafts: [draft],
  };
  assert.equal(draftForCandidate(first, base), null);
  assert.equal(draftForCandidate(second, base), null);
  assert.equal(
    draftForCandidate(first, {
      ...base,
      identityLinks: [{ candidateId: "candidate-a", personId: "person-a", state: "confirmed" }],
    }),
    draft
  );
});

test("saved identity choices reuse canonical project people without duplicating promoted drafts", () => {
  const canonicalPersonId = "22222222-2222-4222-8222-222222222222";
  const mappedDraft = {
    draftId: "11111111-1111-4111-8111-111111111111",
    canonicalPersonId,
    displayName: "Local Layla",
    role: "Lead",
    representative: { role: "agent", email: "agent@example.com" },
    sourceRefs: ["source-a"],
  };
  const localDraft = {
    draftId: "33333333-3333-4333-8333-333333333333",
    displayName: "Background performer",
    sourceRefs: ["source-b"],
  };
  const choices = existingIdentityChoices(
    [mappedDraft, localDraft],
    [
      { id: canonicalPersonId, displayName: "Layla Hassan", role: "Lead actor" },
      { id: "44444444-4444-4444-8444-444444444444", displayName: "Nisreen Salem" },
    ]
  );

  assert.equal(choices.length, 3);
  const canonical = existingIdentityChoiceForId(choices, canonicalPersonId);
  assert.equal(canonical.displayName, "Layla Hassan");
  assert.equal(canonical.personId, canonicalPersonId);
  assert.equal(canonical.draftId, mappedDraft.draftId);
  assert.deepEqual(canonical.sourceRefs, ["source-a"]);
  assert.equal(
    existingIdentityChoiceForId(choices, mappedDraft.draftId),
    canonical,
    "the local and canonical ids resolve to one saved identity choice"
  );
  assert.equal(existingIdentityChoiceForId(choices, localDraft.draftId).scope, "workflow");
  const canonicalWithoutDraft = existingIdentityChoiceForId(
    choices,
    "44444444-4444-4444-8444-444444444444"
  );
  assert.equal(canonicalWithoutDraft.displayName, "Nisreen Salem");
  assert.equal(canonicalWithoutDraft.draft, null);
  assert.equal(canonicalWithoutDraft.canonicalPersonId, canonicalWithoutDraft.personId);
  assert.equal(existingIdentityChoiceForId(choices, ""), null);
});

test("candidate links confirm only the appearances explicitly reviewed", () => {
  const identity = normalizeIdentityPayload({
    candidates: [{ candidateId: "candidate-a", occurrenceIds: ["one", "two", "three"] }],
    occurrences: ["one", "two", "three"].map((occurrenceId) => ({ occurrenceId, candidateId: "candidate-a" })),
  });
  const candidate = identity.candidates[0];
  const draft = { draftId: "person-a", displayName: "Layla", sourceRefs: [] };
  const partial = {
    identityPayload: identity,
    personDrafts: [draft],
    identityLinks: [{ candidateId: "candidate-a", personId: "person-a", state: "confirmed", occurrenceIds: ["one", "two"] }],
  };
  assert.equal(candidateIsFullyConfirmed(candidate, identity, partial), false);
  assert.equal(candidateUnresolvedCount(candidate, identity, partial), 1);
  assert.equal(candidateIsFullyConfirmed(candidate, identity, {
    ...partial,
    identityLinks: [{ ...partial.identityLinks[0], occurrenceIds: ["one", "two", "three"] }],
  }), true);
  assert.equal(candidateIsFullyConfirmed(candidate, identity, {
    ...partial,
    identityLinks: [{ candidateId: "candidate-a", personId: "person-a", state: "confirmed" }],
  }), false);
  assert.equal(candidateIsFullyConfirmed(candidate, identity, {
    ...partial,
    identityLinks: [],
  }), false);
  const split = {
    ...partial,
    personDrafts: [
      draft,
      { draftId: "person-b", displayName: "Party guest", sourceRefs: [] },
    ],
    identityLinks: [
      { candidateId: "candidate-a", personId: "person-a", state: "confirmed", occurrenceIds: ["one"] },
      { candidateId: "candidate-a", personId: "person-b", state: "confirmed", occurrenceIds: ["two", "three"] },
    ],
  };
  assert.equal(candidateIsFullyConfirmed(candidate, identity, split), true);
  assert.equal(candidateUnresolvedCount(candidate, identity, split), 0);
  assert.equal(draftForCandidate(candidate, split), null);
  assert.equal(draftForCandidate(candidate, split, "person-b")?.displayName, "Party guest");
  assert.equal(draftForCandidate(candidate, {
    ...partial,
    identityLinks: [{ candidateId: "candidate-a", personId: "person-a", state: "rejected" }],
  }), null);
});

test("dismissing one false detection retains the person's other confirmed appearances", () => {
  const personId = "22222222-2222-4222-8222-222222222222";
  const links = identityLinksWithFalsePositiveDecision(
    [{
      candidateId: "candidate-a",
      personId,
      state: "confirmed",
      occurrenceIds: ["one", "two", "three"],
    }],
    "candidate-a",
    ["one"],
    { priorPersonId: personId }
  );
  assert.deepEqual(links, [
    {
      candidateId: "candidate-a",
      personId,
      state: "confirmed",
      occurrenceIds: ["two", "three"],
    },
    {
      candidateId: "candidate-a",
      state: "rejected",
      displayName: "False detection",
      occurrenceIds: ["one"],
    },
  ]);

  const identity = normalizeIdentityPayload({
    candidates: [{ candidateId: "candidate-a", occurrenceIds: ["one", "two", "three"] }],
    occurrences: [
      { occurrenceId: "one", candidateId: "candidate-a", sourceRef: "false-source" },
      { occurrenceId: "two", candidateId: "candidate-a", sourceRef: "real-source" },
      { occurrenceId: "three", candidateId: "candidate-a", sourceRef: "real-source" },
    ],
  });
  const state = { identityPayload: identity, identityLinks: links, personDrafts: [] };
  const candidate = identity.candidates[0];
  assert.deepEqual([...candidateDismissedOccurrenceIds(candidate, identity, state)], ["one"]);
  assert.equal(candidateIsFullyConfirmed(candidate, identity, state), false);
  assert.equal(candidateIsResolved(candidate, identity, state), true);
  assert.equal(candidateUnresolvedCount(candidate, identity, state), 0);
  assert.equal(candidateHasActiveOccurrencesForSource(candidate, identity, "false-source", state), false);
  assert.equal(candidateHasActiveOccurrencesForSource(candidate, identity, "real-source", state), true);
});

test("partial different-person reassignment preserves the prior person's other appearances", () => {
  const priorPersonId = "22222222-2222-4222-8222-222222222222";
  const newPersonId = "33333333-3333-4333-8333-333333333333";
  const links = identityLinksWithConfirmedDecision(
    [{
      candidateId: "candidate-a",
      personId: priorPersonId,
      state: "confirmed",
      occurrenceIds: ["a", "b", "c"],
    }],
    "candidate-a",
    {
      personId: newPersonId,
      displayName: "Different person",
      occurrenceIds: ["a"],
      priorPersonId,
      preservePriorUnselected: true,
      candidateOccurrenceIds: ["a", "b", "c"],
    }
  );
  assert.deepEqual(links, [
    {
      candidateId: "candidate-a",
      personId: priorPersonId,
      state: "confirmed",
      occurrenceIds: ["b", "c"],
    },
    {
      candidateId: "candidate-a",
      personId: newPersonId,
      state: "confirmed",
      displayName: "Different person",
      occurrenceIds: ["a"],
    },
  ]);
  assert.equal(
    links.some((link) => link.personId === priorPersonId && link.state === "rejected"),
    false,
    "the prior person is rejected only when no confirmed appearances remain"
  );
});

test("assigning remaining appearances to a saved person preserves their earlier assignments", () => {
  const personId = "22222222-2222-4222-8222-222222222222";
  const links = identityLinksWithConfirmedDecision(
    [{
      candidateId: "candidate-a",
      personId,
      state: "confirmed",
      occurrenceIds: ["a"],
    }],
    "candidate-a",
    {
      personId,
      displayName: "Layla",
      occurrenceIds: ["b"],
      preserveTargetExisting: true,
      candidateOccurrenceIds: ["a", "b", "c"],
    }
  );
  assert.deepEqual(links, [{
    candidateId: "candidate-a",
    personId,
    state: "confirmed",
    displayName: "Layla",
    occurrenceIds: ["b", "a"],
  }]);

  const legacyCandidateWide = identityLinksWithConfirmedDecision(
    [{
      candidateId: "candidate-a",
      personId,
      state: "confirmed",
    }],
    "candidate-a",
    {
      personId,
      displayName: "Layla",
      occurrenceIds: ["b"],
      preserveTargetExisting: true,
      candidateOccurrenceIds: ["a", "b", "c"],
    }
  );
  assert.deepEqual(legacyCandidateWide[0].occurrenceIds, ["b", "a", "c"]);
});

test("saved canonical identity consolidates its promoted local draft alias", () => {
  const draftId = "11111111-1111-4111-8111-111111111111";
  const canonicalPersonId = "22222222-2222-4222-8222-222222222222";
  const links = identityLinksWithConfirmedDecision(
    [{
      candidateId: "candidate-a",
      personId: draftId,
      state: "confirmed",
      occurrenceIds: ["a"],
    }],
    "candidate-a",
    {
      personId: canonicalPersonId,
      targetPersonIds: [draftId, canonicalPersonId],
      displayName: "Layla",
      occurrenceIds: ["b"],
      preserveTargetExisting: true,
      candidateOccurrenceIds: ["a", "b", "c"],
    }
  );

  assert.deepEqual(links, [{
    candidateId: "candidate-a",
    personId: canonicalPersonId,
    state: "confirmed",
    displayName: "Layla",
    occurrenceIds: ["b", "a"],
  }]);
});

test("partial not-sure review preserves the prior person's other confirmed appearances", () => {
  const priorPersonId = "22222222-2222-4222-8222-222222222222";
  const links = identityLinksWithUnresolvedDecision(
    [{
      candidateId: "candidate-a",
      personId: priorPersonId,
      state: "confirmed",
      occurrenceIds: ["a", "b", "c"],
    }],
    "candidate-a",
    ["a"],
    {
      priorPersonId,
      displayName: "Layla",
      candidateOccurrenceIds: ["a", "b", "c"],
    }
  );
  assert.deepEqual(links, [
    {
      candidateId: "candidate-a",
      personId: priorPersonId,
      state: "confirmed",
      occurrenceIds: ["b", "c"],
    },
    {
      candidateId: "candidate-a",
      personId: priorPersonId,
      displayName: "Layla",
      state: "unsure",
      occurrenceIds: ["a"],
    },
  ]);
});

test("deleting a person removes every orphan link and reopens their appearances", () => {
  const personId = "22222222-2222-4222-8222-222222222222";
  const otherPersonId = "33333333-3333-4333-8333-333333333333";
  const links = identityLinksAfterPersonRemoval([
    { candidateId: "candidate-a", personId, state: "confirmed", occurrenceIds: ["one", "two"] },
    { candidateId: "candidate-b", personId, state: "unsure", occurrenceIds: ["three"] },
    { candidateId: "candidate-c", personId, state: "rejected" },
    { candidateId: "candidate-d", personId: otherPersonId, state: "confirmed", occurrenceIds: ["four"] },
  ], [personId]);
  assert.equal(links.some((link) => (link.personId || link.person_id) === personId), false);
  assert.deepEqual(links, [
    { candidateId: "candidate-d", personId: otherPersonId, state: "confirmed", occurrenceIds: ["four"] },
    { candidateId: "candidate-a", state: "unsure", displayName: "Person removed; review again", occurrenceIds: ["one", "two"] },
    { candidateId: "candidate-b", state: "unsure", displayName: "Person removed; review again", occurrenceIds: ["three"] },
  ]);
});

test("identity review cannot report clear while manual source work remains", () => {
  const sourceHash = "d".repeat(64);
  const identity = normalizeIdentityPayload({
    candidates: [{ candidateId: "candidate-a", occurrenceIds: ["occurrence-a"] }],
    occurrences: [{
      occurrenceId: "occurrence-a",
      candidateId: "candidate-a",
      sourceRef: "portrait-source",
    }],
    sourceHashes: [{ sourceRef: "manual-source", sourceHash }],
    issues: [{ code: "no_face_detected", sourceRef: "manual-source" }],
  });
  const state = {
    identityPayload: identity,
    identityLinks: [{
      candidateId: "candidate-a",
      personId: "22222222-2222-4222-8222-222222222222",
      state: "confirmed",
      occurrenceIds: ["occurrence-a"],
    }],
    sourceReviews: {},
    personDrafts: [],
    projectContext: null,
  };
  assert.deepEqual(identityReviewSummary(identity, state).review, []);
  assert.equal(identityReviewSummary(identity, state).manualSourceIssues.length, 1);
  assert.equal(identityReviewSummary(identity, state).isClear, false);
  assert.equal(identityReviewSummary(identity, {
    ...state,
    sourceReviews: { "manual-source": { state: "not_person", sourceHash } },
  }).isClear, true);
});

test("editing one visual candidate preserves a person's unrelated source assignments", () => {
  assert.deepEqual(
    mergeIdentityDraftSourceRefs(
      ["candidate-a", "candidate-b", "outside-candidate"],
      ["candidate-a", "candidate-b"],
      ["candidate-a"]
    ),
    ["outside-candidate", "candidate-a"]
  );
  assert.deepEqual(
    mergeIdentityDraftSourceRefs(
      ["candidate-a", "outside-candidate"],
      ["candidate-a"],
      ["candidate-a"],
      false
    ),
    ["candidate-a"]
  );
});

test("manual no-face outcomes resolve locally and keep identified performers visible", () => {
  const shadowHash = "a".repeat(64);
  const roomHash = "b".repeat(64);
  const identity = normalizeIdentityPayload({
    candidates: [],
    occurrences: [],
    sourceHashes: [
      { sourceRef: "shadow", sourceHash: shadowHash },
      { sourceRef: "empty-room", sourceHash: roomHash },
    ],
    issues: [
      { code: "no_face_detected", sourceRef: "shadow" },
      { code: "no_face_detected", sourceRef: "empty-room" },
    ],
  });
  const base = {
    identityPayload: identity,
    identityLinks: [],
    sourceReviews: {
      "empty-room": { state: "not_person", sourceHash: roomHash },
    },
    personDrafts: [
      { draftId: "shadow-person", displayName: "Nightmare Shadow", sourceRefs: ["shadow"] },
    ],
    projectContext: null,
  };
  assert.deepEqual(unresolvedManualSourceIssues(identity, base), []);
  assert.deepEqual(manualIdentityDrafts(identity, base).map((draft) => draft.draftId), ["shadow-person"]);
  assert.equal(unresolvedManualSourceIssues(identity, {
    ...base,
    sourceReviews: {},
    personDrafts: [],
  }).length, 2);
  assert.equal(unresolvedManualSourceIssues(identity, {
    ...base,
    sourceReviews: {
      "empty-room": { state: "not_person", sourceHash: "stale-hash" },
    },
    personDrafts: [],
    projectContext: {
      sourceLinks: [{ sourceRef: "empty-room", disposition: "not_person" }],
    },
  }).length, 2, "canonical no-person without a current content hash cannot clear local review");
});

test("storage-limited evidence remains explicit manual person work", () => {
  const sourceHash = "d".repeat(64);
  const identity = normalizeIdentityPayload({
    candidates: [],
    occurrences: [],
    sourceHashes: [{ sourceRef: "storage-limited", sourceHash }],
    issues: [{
      code: "evidence_omitted_source",
      sourceRef: "storage-limited",
      title: "Visual source needs manual person review",
    }],
  });
  const state = {
    identityPayload: identity,
    identityLinks: [],
    sourceReviews: {},
    personDrafts: [],
    projectContext: null,
  };
  assert.equal(unresolvedManualSourceIssues(identity, state).length, 1);
  assert.deepEqual(manualIdentityDrafts(identity, state), []);
});

test("incomplete and limited source analysis always remains unresolved manual work", () => {
  const decodeHash = "e".repeat(64);
  const limitHash = "f".repeat(64);
  const futureHash = "1".repeat(64);
  const identity = normalizeIdentityPayload({
    manual_review_required: true,
    manual_review_sources: [
      {
        source_ref: "resolver-source",
        source_hash: null,
        issue_codes: ["source_unavailable"],
      },
      {
        source_ref: "decode-source",
        source_hash: decodeHash,
        issue_codes: ["video_decode_failed"],
      },
    ],
    coverage: { total: 4, analyzed: 1, manual_review_sources: 4 },
    source_hashes: [
      { source_ref: "decode-source", source_hash: decodeHash },
      { source_ref: "limit-source", source_hash: limitHash },
      { source_ref: "future-source", source_hash: futureHash },
    ],
    candidates: [],
    occurrences: [],
    issues: [
      {
        code: "faces_per_frame_limited",
        source_ref: "limit-source",
        source_hash: limitHash,
      },
      {
        code: "future_backend_partial_analysis",
        source_ref: "future-source",
        source_hash: futureHash,
        manual_review_required: true,
      },
    ],
  });
  const items = identityManualReviewItems(identity);
  assert.deepEqual(
    items.map((item) => item.sourceRef).sort(),
    ["decode-source", "future-source", "limit-source", "resolver-source"]
  );
  assert.equal(identity.coverage.manualReviewSourceCount, 4);
  assert.equal(sourceIssueNeedsManualReview({ code: "source_unavailable", manualReviewRequired: false }), true);
  assert.equal(sourceIssueNeedsManualReview({ code: "future_backend_partial_analysis", manualReviewRequired: true }), true);
  assert.equal(sourceIssueNeedsManualReview({ code: "informational_note" }), false);

  const base = {
    identityPayload: identity,
    identityLinks: [],
    sourceReviews: {},
    personDrafts: [],
    projectContext: null,
  };
  assert.equal(unresolvedManualSourceIssues(identity, base).length, 4);
  assert.equal(identityReviewSummary(identity, base).isClear, false);

  const partlyResolved = {
    ...base,
    sourceReviews: {
      "decode-source": { state: "not_person", sourceHash: decodeHash },
    },
    personDrafts: [
      { draftId: "limited-person", sourceRefs: ["limit-source"] },
    ],
    projectContext: {
      sourceLinks: [{ sourceRef: "future-source", disposition: "linked" }],
    },
  };
  assert.deepEqual(
    unresolvedManualSourceIssues(identity, partlyResolved).map((item) => item.sourceRef),
    ["resolver-source"]
  );
  assert.equal(identityReviewSummary(identity, partlyResolved).isClear, false);
  assert.equal(identityReviewSummary(identity, {
    ...partlyResolved,
    personDrafts: [
      ...partlyResolved.personDrafts,
      { draftId: "resolver-person", sourceRefs: ["resolver-source"] },
    ],
  }).isClear, true);
});

test("coverage-only manual review counts fail closed without source references", () => {
  const identity = normalizeIdentityPayload({
    coverage: { total: 2, analyzed: 0, manual_review_sources: 2 },
    candidates: [],
    occurrences: [],
    issues: [],
  });
  const items = identityManualReviewItems(identity);
  assert.equal(items.length, 2);
  assert.ok(items.every((item) => !item.sourceRef));
  assert.equal(identityReviewSummary(identity, {
    identityLinks: [],
    sourceReviews: {},
    personDrafts: [],
    projectContext: null,
  }).isClear, false);
});

test("hash-scoped local source reviews carry forward and can be reversed", () => {
  const sourceRef = "a".repeat(64);
  const sourceHash = "b".repeat(64);
  const existing = [{ sourceRef, disposition: "not_person" }];
  const hashes = [{ sourceRef, sourceHash }];
  assert.deepEqual(
    manifestOverridesForLocalReviews(
      { [sourceRef]: { state: "review_required", sourceHash } },
      existing,
      new Map(),
      hashes
    ).get(sourceRef),
    { disposition: "review_required" }
  );
  assert.deepEqual(
    manifestOverridesForLocalReviews(
      { [sourceRef]: { state: "not_person", sourceHash: "c".repeat(64) } },
      existing,
      new Map(),
      hashes
    ).get(sourceRef),
    { disposition: "review_required" },
    "a changed media hash reopens the old no-person decision"
  );
  assert.equal(
    manifestOverridesForLocalReviews(
      { [sourceRef]: { state: "not_person", sourceHash } },
      [{ sourceRef, disposition: "linked" }],
      new Map(),
      hashes
    ).has(sourceRef),
    false,
    "a local review cannot override an existing canonical person link"
  );
  assert.deepEqual(
    manifestOverridesForLocalReviews(
      { [sourceRef]: { state: "not_person", sourceHash } },
      existing,
      new Map(),
      hashes,
      [{ draftId: "manual-person", sourceRefs: [sourceRef] }]
    ).get(sourceRef),
    { disposition: "review_required" },
    "adding a person supersedes the earlier no-person decision"
  );
});

test("identity job progress reads completed and total counts", () => {
  assert.equal(progressValue({ progress: { completed: 3, total: 4 } }), 75);
  assert.equal(progressValue({ progress: { completed: 0, total: 0 } }), 0);
  assert.equal(progressValue({ progress: 0.42 }), 42);
  assert.equal(progressValue({
    progress: { completed: 1, total: 4, sampledFrames: 50, sampledFrameTotal: 100 },
  }), 37.5);
  assert.equal(progressDetailLabel({
    progress: { completed: 1, total: 4, sampledFrames: 50, sampledFrameTotal: 100 },
  }), "Sampled frame 50 of 100");
  assert.equal(progressDetailLabel({ progress: { completed: 2, total: 4 } }), "2 of 4 sources");
  assert.equal(progressPhaseLabel({ progress: { phase: "reading_media" } }), "Reading media");
  assert.equal(progressPhaseLabel({ progress: { phase: "grouping_people" } }), "Grouping likely people");
  assert.equal(progressPhaseLabel({ progress: { phase: "building_evidence" } }), "Building visual evidence");
  assert.equal(progressPhaseLabel({ progress: { phase: "future_phase" } }), "Analyzing locally");
});

test("people presentation keeps recurring identities visible and collapses one-offs", () => {
  const candidates = [5, 2, 4, 1, 3].map((count) => ({
    candidateId: `candidate-${count}`,
    occurrenceIds: Array.from({ length: count }, (_, index) => `${count}-${index}`),
  }));
  const occurrences = candidates.flatMap((candidate) => candidate.occurrenceIds.map((occurrenceId) => ({
    occurrenceId,
    candidateId: candidate.candidateId,
  })));
  const identity = normalizeIdentityPayload({ candidates, occurrences });
  const groups = identityPresentationGroups(identity);
  assert.deepEqual(groups.recurring.map((candidate) => candidate.candidateId), ["candidate-5", "candidate-4"]);
  assert.deepEqual(groups.supporting.map((candidate) => candidate.candidateId), ["candidate-3"]);
  assert.deepEqual(groups.oneOff.map((candidate) => candidate.candidateId), ["candidate-2", "candidate-1"]);
  assert.deepEqual(groups.primary.map((candidate) => candidate.candidateId), ["candidate-5", "candidate-4", "candidate-3"]);
});

test("candidate roles distinguish an unreviewed suggestion from a true split group", () => {
  const candidate = { suggestedRole: "Layla" };
  assert.equal(candidateRoleLabel([], null, candidate), "Layla");
  assert.equal(candidateRoleLabel([{}], { role: "Lead performer" }, candidate), "Lead performer");
  assert.equal(candidateRoleLabel([{}, {}], null, candidate), "Split appearance group");
});

test("working-name provenance stays separate from visual grouping", () => {
  assert.deepEqual(identitySuggestionProvenance({
    suggestedName: "Nisreen Salem",
    suggestedRole: "Layla",
    suggestionSource: "source_label",
  }), {
    label: "Source-label suggestion",
    badge: "Name from source label",
    description: "The working name and role came from a filename or project asset label. Visual analysis only grouped the appearances.",
  });
  assert.match(
    identitySuggestionProvenance({ suggestedName: "Working name" }).description,
    /project metadata.*Visual analysis only grouped/i
  );
});

test("appearance previews sample the full source while filmstrips adapt to crop count", () => {
  const occurrences = Array.from({ length: 20 }, (_, index) => ({ occurrenceId: String(index) }));
  assert.deepEqual(
    representativeOccurrences(occurrences, 3).map((occurrence) => occurrence.occurrenceId),
    ["0", "10", "19"]
  );
  assert.equal(filmstripColumns(1), "minmax(0, 1fr)");
  assert.equal(filmstripColumns(2), "1.25fr minmax(0, 1fr)");
  assert.equal(filmstripColumns(5), "1.3fr repeat(4, minmax(0, 1fr))");
  assert.equal(filmstripColumns(6, "review"), "1.5fr repeat(2, minmax(0, 0.75fr))");
});

test("people-first summaries translate graph classes without claiming clearance", () => {
  assert.equal(
    plainLanguageUseSummary(
      { sourceRefs: ["source-a"] },
      [{
        sourceRef: "source-a",
        ops: [
          { class_type: "FluxKontextProImageNode" },
          { class_type: "ByteDance2ReferenceNode" },
        ],
      }]
    ),
    "This workflow may guide or edit imagery with their likeness, and animate their likeness into video."
  );
  assert.equal(
    plainLanguageUseSummary(
      { sourceRefs: ["audio-source"] },
      [{
        sourceRef: "audio-source",
        ops: [
          { class_type: "LoadAudio", source_role: "reference_audio" },
          { class_type: "ByteDance2ReferenceNode", source_role: "reference_audio" },
        ],
      }]
    ),
    "This workflow may use or transform their voice or performance audio."
  );
});

test("people-first panel and review wizard expose an accessible progressive flow", async () => {
  const [panel, view, styles] = await Promise.all([
    readFile(new URL("../panel.js", import.meta.url), "utf8"),
    readFile(new URL("../identity-view.js", import.meta.url), "utf8"),
    readFile(new URL("../pluribus.css", import.meta.url), "utf8"),
  ]);
  assert.ok(panel.indexOf('tab("overview", "Overview")') < panel.indexOf('tab("people", "People")'));
  assert.ok(panel.indexOf('tab("people", "People")') < panel.indexOf('tab("sources", "Sources")'));
  assert.match(panel, /tab\("use", "Use & rights"\)/);
  assert.match(view, /role: "dialog"/);
  assert.match(view, /"aria-modal": "true"/);
  assert.match(view, /"aria-labelledby": titleId/);
  assert.match(view, /"aria-live": "polite"/);
  assert.match(view, /role: "progressbar"/);
  assert.match(view, /querySelectorAll\(/);
  assert.match(view, /previousFocus\?\.focus\?\.\(\)/);
  assert.match(view, /\[1, "Confirm identity"\].*\[2, "Review appearances"\].*\[3, "Rights contact"\]/s);
  assert.match(view, /el\(\s*"fieldset"/);
  assert.match(view, /el\("legend", \{ text: "Identity decision" \}\)/);
  assert.match(view, /el\("details", \{ class: "plb-contact-details" \}/);
  assert.match(view, /Likely matches are preselected and grouped by source/);
  assert.match(view, /Select all appearances/);
  assert.match(view, /checkbox\.indeterminate/);
  assert.match(view, /representativeOccurrences\(group\.occurrences\)/);
  assert.match(view, /occurrenceDetails\?\.addEventListener\("toggle"/);
  assert.match(view, /if \(occurrenceDetails\.open\) buildOccurrenceControls\(\)/);
  assert.match(view, /Expand for individual selection/);
  assert.match(view, /decision === "false_positive" \? "Dismissed" : "Included"/);
  assert.match(view, /occurrenceIds: \[\.\.\.form\.occurrenceIds\]/);
  assert.match(view, /Add person from remaining/);
  assert.match(view, /Assigned to \$\{item\.lockedOwner\}/);
  assert.match(view, /reviewContextIsCurrent/);
  assert.doesNotMatch(view.slice(view.indexOf("function appearanceGroup"), view.indexOf("function contactStage")), /\.slice\(0, 8\)/);
  assert.match(view, /Identity is not permission/);
  assert.match(view, /Install local models/);
  assert.match(view, /Media and embeddings stay on this machine/);
  assert.match(view, /Local Python setup required/);
  assert.match(view, /same Python environment that launches ComfyUI/);
  assert.ok(view.indexOf('issue.code === "dependency_unavailable"') < view.indexOf("if (!bundle.installed)"));
  assert.match(view, /No complete visual groups found yet/);
  assert.match(view, /Other \/ one-off appearances/);
  assert.match(view, /ONE_OFF_PAGE_SIZE = 24/);
  assert.match(view, /section\.addEventListener\("toggle"/);
  assert.match(view, /candidates\.slice\(rendered, end\)/);
  assert.doesNotMatch(view, /presentation\.oneOff\.map/);
  assert.match(view, /People seen once or twice/);
  assert.match(view, /Recurring people/);
  assert.match(view, /Supporting people/);
  assert.match(view, /"Recurring groups"/);
  assert.match(view, /"Supporting groups"/);
  assert.match(view, /"One-off groups"/);
  assert.match(view, /Source-label suggestion/);
  assert.match(view, /Working-name provenance/);
  assert.match(view, /manual person review/);
  assert.match(view, /Body, silhouette, or masked performers/);
  assert.match(view, /state: "rejected"/);
  assert.match(view, /persistUnresolvedDecision/);
  assert.match(view, /identityLinksWithUnresolvedDecision/);
  assert.match(view, /Use a saved person/);
  assert.match(view, /Pluribus never merges identities automatically/);
  assert.match(view, /Confirm existing person/);
  assert.match(view, /This will use the existing identity instead of creating a duplicate/);
  assert.match(view, /personId: targetPersonId/);
  assert.match(view, /preserveTargetExisting: confirmingSavedPerson/);
  assert.match(view, /canonicalPersonId: canonicalPersonId \|\| undefined/);
  assert.match(view, /No duplicate person was created/);
  assert.match(view, /Different person confirmed\. The original person record was kept\./);
  assert.match(view, /mergeIdentityDraftSourceRefs/);
  assert.match(view, /Scoped detector correction/);
  assert.match(view, /This does not mark the whole source as person-free/);
  assert.match(view, /persistFalsePositiveDecision/);
  assert.match(view, /scanMatchesCurrentWorkflow\(openedScan\)/);
  assert.match(view, /commitIdentityLinks\(openedJobId, remaining, snapshot\.revision\)/);
  assert.match(view, /Visual review changed in another window/);
  assert.doesNotMatch(view, /confidencePercent|visual similarity/);
  assert.match(view, /visualGroupingLabel/);
  assert.match(styles, /button:focus-visible/);
  assert.match(styles, /prefers-reduced-motion: reduce/);
  assert.match(styles, /\.plb-evidence-sheet-grid img[^}]*height: auto[^}]*object-fit: contain/);
  assert.match(styles, /\.plb-appearance-details[^}]*border-top/);
});

test("runtime does not request third-party fonts or expose graph material upstream", async () => {
  const entry = await readFile(new URL("../pluribus.js", import.meta.url), "utf8");
  const sync = await readFile(new URL("../sync-manifest.js", import.meta.url), "utf8");
  assert.doesNotMatch(entry, /fonts\.googleapis\.com|pluribus-fonts/);
  const outbound = sync.slice(sync.indexOf("saveProjectSourceLinks"));
  assert.doesNotMatch(outbound, /source_key|sourceNodeId|output_node_id|workflow:/);
  assert.match(outbound, /sourceRef/);
  assert.match(outbound, /manifestSourcesForScan/);
});
