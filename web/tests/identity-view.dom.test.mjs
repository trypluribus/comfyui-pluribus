import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { JSDOM } from "jsdom";

import {
  openIdentityReviewDialog,
  renderIdentityPeople,
} from "../identity-view.js";
import {
  identityWorkspaceSyncSummary,
  refreshIdentityWorkspaceSyncState,
  retryIdentityWorkspaceSync,
} from "../identity-analysis.js";
import { getState, invalidateScan, setState, subscribe } from "../store.js";

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

async function waitFor(predicate, message = "condition") {
  for (let attempt = 0; attempt < 25; attempt += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  assert.fail(`timed out waiting for ${message}`);
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

test("partially reviewed groups show only unresolved leftovers, not duplicate person cards", () => {
  const identity = installState();
  const candidate = identity.candidates[0];
  const selectedOccurrence = occurrence("candidate-a", "a-3", "source-a", "Selected three");
  const remainingOccurrences = [1, 2, 3, 4].map((number) =>
    occurrence("candidate-a", `a-left-${number}`, "source-a", `Remaining ${number}`)
  );
  candidate.occurrenceIds.push(
    selectedOccurrence.occurrenceId,
    ...remainingOccurrences.map((item) => item.occurrenceId)
  );
  identity.occurrences.push(selectedOccurrence, ...remainingOccurrences);
  getState().identityLinks[0].occurrenceIds.push(selectedOccurrence.occurrenceId);

  const container = document.querySelector("#people");
  renderIdentityPeople(container);

  const confirmedPeople = container.querySelectorAll(".plb-person-card");
  assert.equal(confirmedPeople.length, 1);
  assert.match(confirmedPeople[0].textContent, /Alex Project/);
  assert.match(confirmedPeople[0].textContent, /5 appearances/);
  assert.match(confirmedPeople[0].textContent, /2 visual groups/);
  assert.equal(
    [...container.querySelectorAll(".plb-candidate-card h3")]
      .filter((heading) => heading.textContent === "Alex Project").length,
    1,
    "the confirmed person's name should appear as a card heading only once"
  );

  const leftover = [...container.querySelectorAll(".plb-candidate-card")].find((card) =>
    card.textContent.includes("confirmed appearances for Alex Project")
  );
  assert.ok(leftover, "expected an unresolved-leftovers card associated with Alex");
  assert.equal(leftover.querySelector("h3").textContent, "4 appearances still need review");
  assert.match(leftover.textContent, /Only unassigned appearances are shown/);
  assert.match(leftover.textContent, /4 appearances/);
  assert.doesNotMatch(leftover.textContent, /7 appearances/);
  assert.equal(leftover.querySelector(".plb-filmstrip")?.dataset.count, "4");
  assert.ok([...leftover.querySelectorAll("button")].some((item) =>
    item.textContent === "Review Alex Project's assignment"
  ));
  assert.ok([...leftover.querySelectorAll("button")].some((item) =>
    item.textContent === "Identify remaining appearances"
  ));
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

test("panel-load status never reports Synced while a project portrait is pending", async () => {
  installState("synced");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (path) => {
    assert.equal(String(path), "/pluribus/identity/sync");
    return Response.json({
      entries: [{
        workflowRef: WORKFLOW_REF,
        projectId: "project-fixture",
        revision: 4,
        state: "synced",
      }],
      portraitEntries: [{
        projectId: "project-fixture",
        operationId: "portrait-pending",
        state: "retire_pending",
      }],
    });
  };
  try {
    assert.equal(await refreshIdentityWorkspaceSyncState(), "sync_pending");
    const container = document.querySelector("#people");
    renderIdentityPeople(container);
    assert.equal(
      container.querySelector(".plb-person-card .plb-status-pill")?.textContent,
      "Sync pending"
    );
    assert.ok(buttonWithText("Retry sync"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("retry keeps projection-blocked portrait work pending and actionable", async () => {
  installState("synced");
  const originalFetch = globalThis.fetch;
  const payload = {
    entries: [{
      workflowRef: WORKFLOW_REF,
      projectId: "project-fixture",
      revision: 4,
      state: "synced",
    }],
    portraitEntries: [{
      projectId: "project-fixture",
      operationId: "project:project-fixture",
      state: "projection_blocked",
      code: "identity_requires_review",
      message: "Review this identity in the current project.",
    }],
  };
  globalThis.fetch = async (path) => {
    if (String(path) === "/pluribus/identity/sync/retry") return Response.json(payload);
    if (String(path) === "/pluribus/identity/sync") return Response.json(payload);
    if (String(path).includes("/pluribus/bindings/") && String(path).endsWith("/people")) {
      return Response.json({ drafts: getState().personDrafts });
    }
    return Response.json({ message: `Unexpected ${path}` }, { status: 500 });
  };
  try {
    assert.equal(
      await retryIdentityWorkspaceSync({ syncManifest: false }),
      "sync_pending"
    );
    assert.equal(getState().identitySyncIssue?.code, "identity_requires_review");
    const container = document.querySelector("#people");
    renderIdentityPeople(container);
    assert.match(buttonWithText("Retry sync")?.title || "", /Review this identity/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("portrait authentication failure raises Reconnect required above synced identity", () => {
  const summary = identityWorkspaceSyncSummary(
    {
      entries: [{ workflowRef: WORKFLOW_REF, revision: 4, state: "synced" }],
      portraitEntries: [{
        projectId: "project-fixture",
        operationId: "portrait-auth",
        state: "pending",
        lastStatus: 401,
      }],
    },
    WORKFLOW_REF,
    "project-fixture"
  );
  assert.equal(summary.state, "reconnect_required");
});

test("old project sync receipts cannot mark a rebound project Synced", async () => {
  installState("saved_local");
  setState({
    activeProjectId: "project-b",
    workflowBinding: { workflowRef: WORKFLOW_REF, projectId: "project-b" },
    identitySyncIssue: {
      code: "identity_requires_review",
      message: "Review identities in project B.",
    },
  });
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({
    entries: [{
      workflowRef: WORKFLOW_REF,
      projectId: "project-fixture",
      revision: 4,
      state: "synced",
    }],
    portraitEntries: [],
  });
  try {
    assert.equal(await refreshIdentityWorkspaceSyncState(), null);
    assert.equal(getState().identitySyncState, "saved_local");
    assert.equal(getState().identitySyncIssue?.code, "identity_requires_review");
  } finally {
    globalThis.fetch = originalFetch;
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
  assert.deepEqual(controls.map((control) => control.disabled), [false, false]);
  controls[1].click();
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

test("appearance review locks only frames assigned to another person", () => {
  const identity = installState();
  getState().identityLinks[0].occurrenceIds = ["a-1"];
  getState().identityLinks.push({
    candidateId: "candidate-a",
    personId: LOCAL_DRAFT_ID,
    displayName: "Riley Local",
    state: "confirmed",
    occurrenceIds: ["a-2"],
  });

  openIdentityReviewDialog(identity.candidates[0], PROJECT_DRAFT_ID);
  buttonWithText("Next · appearances").click();
  const controls = openIndividualAppearances();

  assert.deepEqual(controls.map((control) => control.disabled), [false, true]);
  assert.deepEqual(controls.map((control) => control.checked), [true, false]);
  assert.match(
    controls[1].closest(".plb-appearance-item").textContent,
    /Assigned to Riley Local/
  );
});

test("confirmed person can deselect one appearance, save the exact subset, and reopen it", async () => {
  const identity = installState("synced");
  identity.candidates[0].occurrenceIds.push("a-wrong");
  identity.occurrences.push(
    occurrence("candidate-a", "a-wrong", "source-a", "Incorrect person")
  );
  getState().identityLinks[0].occurrenceIds.push("a-wrong");

  const container = document.querySelector("#people");
  renderIdentityPeople(container);
  assert.match(container.querySelector(".plb-person-card").textContent, /5 appearances/);
  assert.match(container.querySelector(".plb-person-card").textContent, /2 visual groups/);

  const requests = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (path, init = {}) => {
    const method = init.method || "GET";
    if (method === "GET" && String(path).endsWith(`/identity/jobs/${JOB_ID}/links`)) {
      return Response.json({
        links: getState().identityLinks,
        revision: getState().identityLinksRevision,
      });
    }
    if (method === "PUT" && String(path).endsWith(`/identity/jobs/${JOB_ID}/decision`)) {
      const payload = JSON.parse(init.body);
      requests.push(payload);
      const nextLinks = getState().identityLinks.map((link) =>
        link.candidateId === payload.candidateId
          && link.personId === PROJECT_DRAFT_ID
          ? { ...link, occurrenceIds: [...payload.occurrenceIds] }
          : link
      );
      return Response.json({
        links: nextLinks,
        revision: getState().identityLinksRevision + 1,
        personDrafts: getState().personDrafts,
        syncState: "synced",
      });
    }
    return Response.json({ message: `Unexpected ${method} ${path}` }, { status: 500 });
  };

  const dialogOptions = { scanMatchesCurrentWorkflow: async () => true };
  try {
    openIdentityReviewDialog(identity.candidates[0], PROJECT_DRAFT_ID, dialogOptions);
    buttonWithText("Next · appearances").click();
    let controls = openIndividualAppearances();
    assert.deepEqual(controls.map((control) => control.disabled), [false, false, false]);
    assert.deepEqual(controls.map((control) => control.checked), [true, true, true]);

    controls[2].click();
    assert.equal(controls[2].checked, false);
    assert.match(
      document.querySelector("[data-appearance-summary]").textContent,
      /2 of 3 available appearances selected · 1 unresolved/
    );

    buttonWithText("Next · rights contact").click();
    buttonWithText("Save person").click();
    await waitFor(() => !document.querySelector(".plb-dialog"), "identity save");

    assert.equal(requests.length, 1);
    assert.deepEqual(requests[0].occurrenceIds.sort(), ["a-1", "a-2"]);
    assert.equal(requests[0].action, "assign");

    renderIdentityPeople(container);
    const person = container.querySelector(".plb-person-card");
    assert.match(person.textContent, /4 appearances/);
    assert.match(person.textContent, /2 visual groups/);

    openIdentityReviewDialog(identity.candidates[0], PROJECT_DRAFT_ID, dialogOptions);
    buttonWithText("Next · appearances").click();
    controls = openIndividualAppearances();
    assert.deepEqual(
      controls.map((control) => control.checked),
      [true, true, false],
      "the incorrect appearance must stay deselected after reopening"
    );
    assert.match(
      document.querySelector("[data-appearance-summary]").textContent,
      /2 of 3 available appearances selected · 1 unresolved/
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rights-contact save closes and refreshes People for every durable sync state", async () => {
  const originalFetch = globalThis.fetch;
  const expectedLabels = new Map([
    ["synced", "Synced"],
    ["saved_local", "Saved locally"],
    ["sync_pending", "Sync pending"],
    ["reconnect_required", "Reconnect required"],
  ]);

  try {
    for (const [syncState, expectedLabel] of expectedLabels) {
      document.body.innerHTML = '<main id="people"></main>';
      const identity = installState("saved_local");
      const container = document.querySelector("#people");
      renderIdentityPeople(container);
      const renderedRevisions = [];
      const unsubscribe = subscribe((_state, patch) => {
        if (!Object.hasOwn(patch, "identityLinksRevision")) return;
        renderedRevisions.push(getState().identityLinksRevision);
        renderIdentityPeople(container);
      });

      globalThis.fetch = async (path, init = {}) => {
        const method = init.method || "GET";
        if (method === "GET" && String(path).endsWith(`/identity/jobs/${JOB_ID}/links`)) {
          return Response.json({
            links: getState().identityLinks,
            revision: getState().identityLinksRevision,
          });
        }
        if (method === "PUT" && String(path).endsWith(`/identity/jobs/${JOB_ID}/decision`)) {
          const payload = JSON.parse(init.body);
          const nextLinks = getState().identityLinks.map((link) =>
            link.candidateId === payload.candidateId
              && link.personId === PROJECT_DRAFT_ID
              ? { ...link, occurrenceIds: [...payload.occurrenceIds] }
              : link
          );
          return Response.json({
            links: nextLinks,
            revision: 5,
            personDrafts: getState().personDrafts,
            syncState,
          });
        }
        return Response.json({ message: `Unexpected ${method} ${path}` }, { status: 500 });
      };

      try {
        openIdentityReviewDialog(identity.candidates[0], PROJECT_DRAFT_ID, {
          scanMatchesCurrentWorkflow: async () => true,
        });
        buttonWithText("Next · appearances").click();
        buttonWithText("Next · rights contact").click();
        buttonWithText("Save person").click();

        await waitFor(() => !document.querySelector(".plb-dialog"), `${syncState} modal close`);
        assert.equal(getState().identitySyncState, syncState);
        assert.ok(renderedRevisions.includes(5), `${syncState} should rerender the saved revision`);
        assert.equal(
          container.querySelector(".plb-person-card .plb-status-pill")?.textContent,
          expectedLabel
        );
      } finally {
        unsubscribe();
      }
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("failed or conflicting rights-contact save stays open with an actionable error", async () => {
  const originalFetch = globalThis.fetch;
  const cases = [
    {
      name: "failed save",
      status: 500,
      body: { message: "Temporary local write failure. Try again." },
      expected: /Temporary local write failure\. Try again\./,
    },
    {
      name: "revision conflict",
      status: 409,
      body: { message: "Identity links revision conflict; refresh and retry." },
      expected: /Visual review changed in another window\. Close and reopen this person before saving again\./,
    },
  ];

  try {
    for (const failure of cases) {
      document.body.innerHTML = '<main id="people"></main>';
      const identity = installState("saved_local");
      globalThis.fetch = async (path, init = {}) => {
        const method = init.method || "GET";
        if (method === "GET" && String(path).endsWith(`/identity/jobs/${JOB_ID}/links`)) {
          return Response.json({
            links: getState().identityLinks,
            revision: getState().identityLinksRevision,
          });
        }
        if (method === "PUT" && String(path).endsWith(`/identity/jobs/${JOB_ID}/decision`)) {
          return Response.json(failure.body, { status: failure.status });
        }
        return Response.json({ message: `Unexpected ${method} ${path}` }, { status: 500 });
      };

      openIdentityReviewDialog(identity.candidates[0], PROJECT_DRAFT_ID, {
        scanMatchesCurrentWorkflow: async () => true,
      });
      buttonWithText("Next · appearances").click();
      buttonWithText("Next · rights contact").click();
      buttonWithText("Save person").click();

      await waitFor(
        () => [...document.querySelectorAll(".plb-toast")].some((toast) =>
          failure.expected.test(toast.textContent)
        ),
        failure.name
      );
      assert.ok(document.querySelector(".plb-dialog"), `${failure.name} should keep the modal open`);
      assert.equal(buttonWithText("Save person")?.disabled, false);
      assert.equal(getState().identityLinksRevision, 4);
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
