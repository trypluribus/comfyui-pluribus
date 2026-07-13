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
  manifestSourcesForScan,
  normalizedOperations,
  rightsManifestHash,
} from "../manifest.js";
import {
  aiActionRowsForLinks,
  hasRevocationPath,
  revocationPathRequired,
} from "../use-brief-contract.js";

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

test("only normalized rights operations enter the manifest", () => {
  assert.deepEqual(
    normalizedOperations({
      ops: [
        { class_type: "KSampler", node_id: "8" },
        { class_type: "IPAdapter", node_id: "9" },
        { class_type: "SaveImage", node_id: "10" },
      ],
      provenance: ["LoadImage", "IPAdapter"],
    }),
    [{ classType: "IPAdapter" }, { classType: "LoadImage" }]
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
  ];
  const source = (
    await Promise.all(paths.map((path) => readFile(new URL(path, import.meta.url), "utf8")))
  ).join("\n");

  assert.doesNotMatch(
    source,
    /Performance altered by|workflow is ready|CONSENT LIVE|we used your likeness|Invite for terms|ROSTER LINKED|Terms accepted|Sarah Chen/
  );
  assert.match(source, /Downstream graph nodes/);
  assert.match(source, /This scan does not inspect rendered pixels/);
  assert.match(source, /Link to person/);
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

test("source cards preserve local marker names and surface ignored incomplete markers", async () => {
  const source = await readFile(new URL("../panel.js", import.meta.url), "utf8");
  assert.match(source, /person\.name \|\| "Detected person source"/);
  assert.match(source, /incomplete Pluribus/);
  assert.match(source, /markers were.*ignored/);
  assert.match(source, /issue\.node_id/);
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
