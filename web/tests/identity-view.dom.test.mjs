import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { JSDOM } from "jsdom";

import {
  openIdentityReviewDialog,
  renderIdentityPeople,
} from "../identity-view.js";
import { invalidateScan, setState } from "../store.js";

const PROJECT_PERSON_ID = "22222222-2222-4222-8222-222222222222";
const PROJECT_DRAFT_ID = "33333333-3333-4333-8333-333333333333";
const LOCAL_DRAFT_ID = "44444444-4444-4444-8444-444444444444";
const JOB_ID = "55555555-5555-4555-8555-555555555555";
const WORKFLOW_REF = "66666666-6666-4666-8666-666666666666";

let dom;

function occurrence(candidateId, occurrenceId, sourceRef, sceneLabel) {
  return {
    candidateId,
    occurrenceId,
    sourceRef,
    sourceLabel: `${sourceRef}.png`,
    sceneLabel,
    ambiguous: false,
  };
}

function identityFixture() {
  const candidates = [
    {
      candidateId: "candidate-a",
      suggestedName: "Alex suggestion one",
      suggestedRole: "Lead",
      sourceRefs: ["source-a"],
      occurrenceIds: ["a-1", "a-2"],
      evidence: [],
    },
    {
      candidateId: "candidate-b",
      suggestedName: "Alex suggestion two",
      suggestedRole: "Lead",
      sourceRefs: ["source-b"],
      occurrenceIds: ["b-1", "b-2"],
      evidence: [],
    },
    {
      candidateId: "candidate-proposed",
      suggestedName: "Unresolved crew member",
      sourceRefs: ["source-c"],
      occurrenceIds: ["p-1", "p-2", "p-3", "p-4"],
      evidence: [],
    },
  ];
  const occurrences = [
    occurrence("candidate-a", "a-1", "source-a", "Close-up"),
    occurrence("candidate-a", "a-2", "source-a", "Wide shot"),
    occurrence("candidate-b", "b-1", "source-b", "Opening"),
    occurrence("candidate-b", "b-2", "source-b", "Closing"),
    occurrence("candidate-proposed", "p-1", "source-c", "Frame one"),
    occurrence("candidate-proposed", "p-2", "source-c", "Frame two"),
    occurrence("candidate-proposed", "p-3", "source-c", "Frame three"),
    occurrence("candidate-proposed", "p-4", "source-c", "Frame four"),
  ];
  return {
    coverage: { analyzed: 3, total: 3, skipped: 0 },
    candidates,
    occurrences,
    issues: [],
  };
}

function installState(syncState = "saved_local") {
  const identityPayload = identityFixture();
  setState({
    scan: {
      workflow_name: "Identity DOM fixture",
      workflow_fingerprint: "a".repeat(64),
      persons: [],
    },
    workflow: {},
    workflowBinding: { workflowRef: WORKFLOW_REF, projectId: "project-fixture" },
    activeProjectId: "project-fixture",
    projectContext: {
      workflow: { workflowRef: WORKFLOW_REF },
      people: [{
        id: PROJECT_PERSON_ID,
        displayName: "Alex Project",
        role: "Lead",
      }],
    },
    identityJob: { jobId: JOB_ID, state: "completed" },
    identityPayload,
    identityLinks: [
      {
        candidateId: "candidate-a",
        personId: PROJECT_DRAFT_ID,
        displayName: "Alex Project",
        state: "confirmed",
        occurrenceIds: ["a-1", "a-2"],
      },
      {
        candidateId: "candidate-b",
        personId: PROJECT_DRAFT_ID,
        displayName: "Alex Project",
        state: "confirmed",
        occurrenceIds: ["b-1", "b-2"],
      },
    ],
    identityLinksRevision: 4,
    identitySyncState: syncState,
    personDrafts: [
      {
        draftId: PROJECT_DRAFT_ID,
        canonicalPersonId: PROJECT_PERSON_ID,
        displayName: "Alex Project",
        role: "Lead",
        sourceRefs: ["source-a", "source-b"],
      },
      {
        draftId: LOCAL_DRAFT_ID,
        displayName: "Riley Local",
        role: "Supporting",
        sourceRefs: ["source-d"],
      },
    ],
    sourceRefs: {},
    connection: { state: "connected" },
  });
  return identityPayload;
}

function buttonWithText(text) {
  return [...document.querySelectorAll("button")].find((button) =>
    button.textContent.trim() === text
  );
}

function change(control) {
  control.dispatchEvent(new window.Event("change", { bubbles: true }));
}

function selectDecision(value) {
  const radio = document.querySelector(`input[name="identity-decision"][value="${value}"]`);
  assert.ok(radio, `expected ${value} identity decision`);
  radio.checked = true;
  change(radio);
}

function openIndividualAppearances() {
  const details = document.querySelector(".plb-appearance-details");
  assert.ok(details, "expected appearance details");
  details.open = true;
  details.dispatchEvent(new window.Event("toggle"));
  return [...details.querySelectorAll('input[type="checkbox"]')];
}

beforeEach(() => {
  dom = new JSDOM("<!doctype html><html><body><main id=\"people\"></main></body></html>", {
    url: "http://localhost/",
  });
  globalThis.window = dom.window;
  globalThis.document = dom.window.document;
  globalThis.Event = dom.window.Event;
  globalThis.KeyboardEvent = dom.window.KeyboardEvent;
  globalThis.MouseEvent = dom.window.MouseEvent;
  globalThis.HTMLElement = dom.window.HTMLElement;
  globalThis.Node = dom.window.Node;
});

afterEach(() => {
  invalidateScan();
  dom.window.close();
  delete globalThis.window;
  delete globalThis.document;
  delete globalThis.Event;
  delete globalThis.KeyboardEvent;
  delete globalThis.MouseEvent;
  delete globalThis.HTMLElement;
  delete globalThis.Node;
});

test("People view aggregates confirmed candidate groups and keeps proposals separate", () => {
  installState();
  const container = document.querySelector("#people");

  assert.equal(renderIdentityPeople(container), true);

  const people = container.querySelectorAll(".plb-person-card");
  assert.equal(people.length, 1, "one logical person should render once");
  assert.match(people[0].textContent, /Alex Project/);
  assert.match(people[0].textContent, /4 appearances/);
  assert.match(people[0].textContent, /2 sources/);
  assert.match(people[0].textContent, /2 visual groups/);
  assert.match(container.textContent, /Proposed recurring groups/);
  assert.match(container.textContent, /Unresolved crew member/);
  assert.doesNotMatch(container.textContent, /Alex suggestion one/);
  assert.doesNotMatch(container.textContent, /Alex suggestion two/);
});

test("People cards show every durable identity sync state explicitly", () => {
  const expectedLabels = new Map([
    ["saved_local", "Saved locally"],
    ["sync_pending", "Sync pending"],
    ["reconnect_required", "Reconnect required"],
    ["synced", "Synced"],
  ]);
  const container = document.querySelector("#people");

  for (const [syncState, label] of expectedLabels) {
    installState(syncState);
    renderIdentityPeople(container);
    assert.equal(
      container.querySelector(".plb-person-card .plb-status-pill")?.textContent,
      label,
      syncState
    );
    if (syncState === "sync_pending") {
      assert.equal(buttonWithText("Retry sync")?.textContent, "Retry sync");
    }
    if (syncState === "reconnect_required") {
      assert.equal(buttonWithText("Reconnect")?.textContent, "Reconnect");
    }
  }
});

test("review dropdown distinguishes project people and local drafts, then exposes assign and combine", () => {
  const identity = installState();
  openIdentityReviewDialog(identity.candidates[0], PROJECT_DRAFT_ID);

  const picker = document.querySelector('select[aria-label="Use an existing project person"]');
  assert.ok(picker, "expected saved-person picker");
  const optionLabels = [...picker.options].map((option) => option.textContent);
  assert.ok(optionLabels.some((label) => label === "Project person · Alex Project · Lead · 2 sources"));
  assert.ok(optionLabels.some((label) => label === "Local draft · Riley Local · Supporting · 1 source"));

  const localOption = [...picker.options].find((option) => option.textContent.startsWith("Local draft · Riley Local"));
  picker.value = localOption.value;
  change(picker);

  assert.match(document.querySelector(".plb-dialog").textContent, /Assign selected appearances/);
  assert.match(document.querySelector(".plb-dialog").textContent, /Combine identities/);
  const assign = document.querySelector('input[name="identity-assignment-action"][value="assign"]');
  const combine = document.querySelector('input[name="identity-assignment-action"][value="combine"]');
  assert.equal(assign.checked, true);
  combine.checked = true;
  change(combine);
  assert.equal(
    document.querySelector('input[name="identity-assignment-action"][value="combine"]').checked,
    true
  );
  assert.match(document.querySelector(".plb-dialog").textContent, /preserve an audit tombstone/);
});

test("appearance checkbox snapshot survives toggling through Not a person", () => {
  const identity = installState();
  openIdentityReviewDialog(identity.candidates[0], PROJECT_DRAFT_ID);

  buttonWithText("Next · appearances").click();
  let controls = openIndividualAppearances();
  assert.deepEqual(controls.map((control) => control.checked), [true, true]);
  controls[1].checked = false;
  change(controls[1]);
  assert.match(document.querySelector("[data-appearance-summary]").textContent, /1 of 2 available appearances selected/);

  buttonWithText("Back").click();
  selectDecision("false_positive");
  buttonWithText("Next · appearances").click();
  controls = openIndividualAppearances();
  assert.deepEqual(controls.map((control) => control.checked), [true, true]);

  buttonWithText("Back").click();
  selectDecision("same");
  buttonWithText("Next · appearances").click();
  controls = openIndividualAppearances();
  assert.deepEqual(
    controls.map((control) => control.checked),
    [true, false],
    "person selections should be restored independently of false-positive selections"
  );
});
