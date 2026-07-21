// Canonical Pluribus project workflow for ComfyUI.
// Detection stays local. Connected actions send only opaque source references,
// normalized operation classes, project/person IDs, and the intended-use form.

import {
  getLocalPersonDrafts,
  getLocalSourceReviews,
  resolveLocalSource,
  resolveLocalWorkflow,
  saveLocalSourceReview,
  scanWorkflow,
} from "./api.js";
import {
  avatar,
  button,
  el,
  metaLabel,
  opsChips,
  pluribusMark,
  statusAxes,
  toast,
} from "./components.js";
import {
  applyReticles,
  focusNodeById,
  focusPerson,
  localWorkflowKey,
  personMatchesCurrentWorkflow,
  snapshotWorkflow,
  workflowName,
} from "./canvas.js";
import { workflowFingerprint } from "./fingerprint.js";
import {
  analyzeWorkflowIdentity,
  refreshIdentityCapabilities,
  retryIdentityWorkspaceSync,
} from "./identity-analysis.js";
import { identityManualReviewItems, plainLanguageUseSummary } from "./identity-contract.js";
import {
  candidateHasActiveOccurrencesForSource,
  draftForCandidate,
  identityLinksForCandidate,
  openIdentityReviewDialog,
  renderIdentityOverview,
  renderIdentityPeople,
} from "./identity-view.js";
import { openConnectDialog, refreshConnection, requirePluribusConnection } from "./connect.js";
import { linkedPeopleForSource, openLinkPersonDialog } from "./link-person.js";
import { personLocalKey } from "./manifest.js";
import {
  localDraftsForPerson,
  openPersonDraftDialog,
  visiblePersonDrafts,
} from "./person-drafts.js";
import { renderPeople } from "./people.js";
import {
  loadProductContext,
  openProjectDialog,
  openWorkspaceSetupDialog,
  selectProject,
} from "./project.js";
import { internalStateForPerson, requestStateForPerson } from "./request-confirmation.js";
import {
  activeProject,
  getState,
  projectSourceLinks,
  setState,
  subscribe,
} from "./store.js";
import { renderUseBrief } from "./use-brief.js";
import { syncCurrentRightsManifest } from "./sync-manifest.js";
import {
  sourceDisplayLabel,
  sourceMedia,
  sourceRecordsForScan,
  sourceSupportsNoPersonReview,
  sourceVariantCount,
} from "./source-records.js";

const expanded = new Set();
let root = null;
let mountedContainer = null;
let unsubscribePanel = null;
let activeTab = "overview";
let contextRequested = false;
let scanRequestId = 0;
let scanBarrier = Promise.resolve();

export function mountPanel(container) {
  // ComfyUI may call a native sidebar tab's render callback more than once for
  // the same live container. Reuse that mount so we do not duplicate controls
  // or store subscriptions. If the host discarded the previous DOM (or hands
  // us a replacement container), tear down our listener before remounting.
  if (mountedContainer === container && root && container.contains(root)) return root;

  unmountPanel();
  root = el("div", { class: "plb-root" });
  mountedContainer = container;
  container.appendChild(root);
  unsubscribePanel = subscribe((state, patch = {}) => {
    const patchKeys = Object.keys(patch);
    const identityOnly = patchKeys.length > 0 && patchKeys.every((key) => key.startsWith("identity"));
    const sourceReviewChanged = activeTab === "sources"
      && (Object.hasOwn(patch, "identityPayload") || Object.hasOwn(patch, "identityLinks"));
    if (!identityOnly || activeTab === "overview" || activeTab === "people" || sourceReviewChanged) render(state);
    maybeLoadConnectedContext(state);
  });
  render(getState());
  if (!getState().scan && !getState().scanning) void scan();
  void refreshIdentityCapabilities();
  void refreshConnection();
  return root;
}

export function unmountPanel() {
  unsubscribePanel?.();
  unsubscribePanel = null;
  root?.remove();
  root = null;
  mountedContainer = null;
}

function maybeLoadConnectedContext(state) {
  if (state.connection?.state !== "connected") {
    contextRequested = false;
    return;
  }
  if (state.workspaceReady || state.projectLoading || contextRequested) return;
  contextRequested = true;
  void loadProductContext()
    .then(async () => {
      // Reconnect can promote durable person operations before the active
      // project/workflow context is restored. Retry once hydration finishes so
      // the complete source manifest is projected and can acknowledge those
      // already-idempotent person operations.
      if (getState().connection?.state === "connected") {
        await retryIdentityWorkspaceSync();
      }
    })
    .catch((error) => toast(error.message || "Could not load your Pluribus workspace."))
    .finally(() => {
      contextRequested = false;
    });
}

export async function scan() {
  const requestId = ++scanRequestId;
  const scanEpoch = getState().scanEpoch;
  const isCurrent = () => requestId === scanRequestId && getState().scanEpoch === scanEpoch;
  setState({ scanning: true, error: null });
  const priorScan = scanBarrier;
  let releaseScan;
  scanBarrier = new Promise((resolve) => {
    releaseScan = resolve;
  });
  await priorScan;
  if (!isCurrent()) {
    releaseScan();
    return;
  }
  try {
    const workflow = await snapshotWorkflow();
    if (!isCurrent()) return;
    const name = workflowName();
    const graphHash = await workflowFingerprint(workflow);
    if (!isCurrent()) return;
    const [scanResult, workflowBinding] = await Promise.all([
      scanWorkflow(workflow, name, graphHash),
      resolveLocalWorkflow(localWorkflowKey(), graphHash),
    ]);
    if (!isCurrent()) return;

    const [sourceEntries, draftPayload, reviewPayload] = await Promise.all([
      Promise.all(
        (scanResult.persons || []).map(async (person) => {
          const source = await resolveLocalSource(
            workflowBinding.workflowRef,
            person.source_key || `${person.source_kind}:${person.source_node_id || person.output_node_id}`,
            person.source_kind || "unknown"
          );
          return [personLocalKey(person), source.sourceRef];
        })
      ),
      getLocalPersonDrafts(workflowBinding.workflowRef),
      getLocalSourceReviews(workflowBinding.workflowRef),
    ]);
    if (!isCurrent()) return;
    setState({
      scan: scanResult,
      workflow,
      workflowBinding,
      sourceRefs: Object.fromEntries(sourceEntries),
      personDrafts: draftPayload.drafts || [],
      sourceReviews: Object.fromEntries(
        (reviewPayload.reviews || []).map((review) => [review.sourceRef, review])
      ),
      manifestSynced: false,
      scannedAt: new Date(),
    });
    applyReticles(scanResult.persons || []);

    let identityPromise = null;
    if ((scanResult.persons || []).length) {
      identityPromise = analyzeWorkflowIdentity({
        workflow,
        workflowName: name,
        workflowFingerprint: graphHash,
        workflowBinding,
        scan: scanResult,
      });
    }

    if (workflowBinding.projectId && getState().connection?.state === "connected") {
      await loadProductContext();
      if (!isCurrent()) return;
      if (getState().activeProjectId !== workflowBinding.projectId) {
        await selectProject(workflowBinding.projectId, workflowBinding.workflowKind || "production");
      } else {
        await syncCurrentRightsManifest();
      }
      if (identityPromise) {
        void identityPromise.then(async () => {
          if (!isCurrent() || getState().connection?.state !== "connected") return;
          // Identity ownership and its opaque review hash arrive after the
          // initial graph manifest. Always replace the full manifest again so
          // proof/readiness cannot remain current on a pre-review snapshot.
          await syncCurrentRightsManifest();
        }).catch((error) => console.warn("[Pluribus] local source review sync failed", error));
      }
    }
  } catch (error) {
    if (!isCurrent()) return;
    console.error("[Pluribus] scan failed", error);
    setState({ error });
  } finally {
    if (isCurrent()) setState({ scanning: false });
    releaseScan();
  }
}

function render(state) {
  if (!root) return;
  const content = el("div", { class: "plb-tab-content" });
  if (activeTab === "overview") {
    renderIdentityOverview(content, {
      openPeople: () => setActiveTab("people"),
      openSources: () => setActiveTab("sources"),
      openUse: () => setActiveTab("use"),
      rescan: scan,
    });
  }
  else if (activeTab === "people") {
    if (!renderIdentityPeople(content)) renderPeople(content);
  }
  else if (activeTab === "use") renderUseBrief(content);
  else content.replaceChildren(sourcesBody(state));
  root.replaceChildren(header(state), tabs(), projectBand(state), content, footer(state));
}

function setActiveTab(tabId) {
  activeTab = tabId;
  render(getState());
}

function header(state) {
  return el(
    "div",
    { class: "plb-header" },
    el(
      "div",
      { class: "plb-header-brand" },
      pluribusMark(15),
      el("span", { class: "plb-wordmark", text: "Pluribus" }),
      el("span", { class: "plb-header-sub", text: "Identity & rights" })
    ),
    connectionChip(state)
  );
}

function connectionChip(state) {
  const connection = state.connection;
  if (connection?.state === "connected") {
    return el(
      "button",
      {
        class: "plb-linked plb-linked--btn",
        type: "button",
        title: "Manage the Pluribus connection",
        onclick: () => openConnectDialog(connection),
      },
      el("span", { class: "plb-dot" }),
      el("span", { class: "plb-linked-email", text: connection.account_email || "connected" })
    );
  }
  return el("button", {
    class: "plb-connect-cta",
    type: "button",
    text: connection?.state === "pairing" ? "Pairing…" : "Connect",
    onclick: () => openConnectDialog(connection),
  });
}

function tabs() {
  const tab = (id, label) =>
    el("button", {
      class: `plb-tab${activeTab === id ? " active" : ""}`,
      type: "button",
      text: label,
      onclick: () => {
        setActiveTab(id);
      },
    });
  return el(
    "div",
    { class: "plb-tabs" },
    tab("overview", "Overview"),
    tab("people", "People"),
    tab("sources", "Sources"),
    tab("use", "Use & rights")
  );
}

function projectBand(state) {
  if (state.connection?.state !== "connected") {
    return el(
      "div",
      { class: "plb-project-band" },
      el("span", { text: "Visual review stays local." }),
      el("small", { text: "Connect only when you're ready to link records or request permission." })
    );
  }
  if (!state.workspaceReady || state.projectLoading) {
    return el("div", { class: "plb-project-band" }, el("span", { text: "Loading workspace…" }));
  }
  if (!state.workspace) {
    return el(
      "div",
      { class: "plb-project-band" },
      el("span", { text: "Workspace setup required" }),
      button("Set up", "secondary", () => openWorkspaceSetupDialog())
    );
  }
  if (!state.projects.length) {
    return el(
      "div",
      { class: "plb-project-band" },
      el("span", { text: state.workspace.displayName || "Your workspace" }),
      button("New project", "secondary", () => openProjectDialog())
    );
  }
  const picker = el(
    "select",
    {
      class: "plb-project-select",
      "aria-label": "Project",
      onchange: async (event) => {
        try {
          await selectProject(event.target.value, kind.value);
        } catch (error) {
          toast(error.message || "Could not switch projects.");
        }
      },
    },
    state.projects.map((project) => {
      const option = el("option", { value: project.id, text: project.title });
      option.selected = project.id === state.activeProjectId;
      return option;
    })
  );
  const kind = el(
    "select",
    {
      class: "plb-kind-select",
      "aria-label": "Workflow kind",
      onchange: async (event) => {
        if (state.activeProjectId) await selectProject(state.activeProjectId, event.target.value);
      },
    },
    workflowKindOptions(state.workflowBinding?.workflowKind || "production")
  );
  return el(
    "div",
    { class: "plb-project-band" },
    picker,
    kind,
    button("+", "secondary", () => openProjectDialog())
  );
}

function sourcesBody(state) {
  if (state.error) {
    return el(
      "div",
      { class: "plb-list" },
      empty(state.error.message || String(state.error), button("Try again", "secondary", scan))
    );
  }
  if (!state.scan) {
    return el(
      "div",
      { class: "plb-list" },
      empty(
        state.scanning
          ? "Reading rights-relevant sources in the current graph…"
          : "Find identity models, reference media, performances, and other rights-relevant inputs.",
        state.scanning ? null : button("Scan workflow", "primary", scan)
      )
    );
  }
  const persons = sourceRecordsForScan(state.scan.persons || [], state.sourceRefs);
  const orderedPersons = [...persons].sort((left, right) =>
    Number(sourceNeedsManualReview(right, state)) - Number(sourceNeedsManualReview(left, state))
  );
  const wrap = el("div", { class: "plb-sources" }, sourceSummary(state, persons));
  const issues = state.scan.issues || [];
  const incompleteMarkers = issues.filter((issue) => issue.code === "incomplete_source_marker");
  const coverageWarnings = issues.filter((issue) => issue.code === "unsupported_terminal_node");
  const otherIssues = issues.filter((issue) =>
    issue.code !== "incomplete_source_marker" && issue.code !== "unsupported_terminal_node"
  );
  if (incompleteMarkers.length) {
    wrap.append(
      el(
        "div",
        { class: "plb-warnstrip" },
        el("span", { class: "plb-warnmark", text: "!" }),
        el("span", {
          text: `${incompleteMarkers.length} incomplete Pluribus ${incompleteMarkers.length === 1 ? "marker was" : "markers were"} ignored. Add a source key, or describe a prompt-only source, then find people again.`,
        })
      )
    );
  }
  if (coverageWarnings.length) {
    wrap.append(
      el(
        "div",
        { class: "plb-warnstrip" },
        el("span", { class: "plb-warnmark", text: "!" }),
        el("span", {
          text: `${coverageWarnings.length} custom workflow ${coverageWarnings.length === 1 ? "ending has" : "endings have"} source lineage, but downstream-use coverage may be incomplete.`,
        })
      )
    );
  }
  if (otherIssues.length) {
    wrap.append(
      el(
        "div",
        { class: "plb-warnstrip" },
        el("span", { class: "plb-warnmark", text: "!" }),
        el("span", { text: `${otherIssues.length} additional technical scan ${otherIssues.length === 1 ? "note needs" : "notes need"} review.` })
      )
    );
  }
  if (issues.length) wrap.append(scanIssueDetails(issues));
  if (!persons.length) {
    wrap.append(
      empty(
        "No supported rights-relevant source was found in this graph. Check coverage details before adding a source marker."
      )
    );
  } else {
    wrap.append(el("div", { class: "plb-list" }, orderedPersons.map((person) => sourceCard(person, state))));
  }
  return wrap;
}

function scanIssueDetails(issues) {
  return el(
    "details",
    { class: "plb-coverage-details plb-scan-issue-details" },
    el("summary", { text: `${issues.length} technical scan ${issues.length === 1 ? "detail" : "details"}` }),
    el(
      "ul",
      {},
      issues.map((issue) => el(
        "li",
        {},
        el("strong", { text: issue.code || "scan_note" }),
        issue.message ? el("span", { text: issue.message }) : null,
        el("code", {
          text: [
            issue.class_type ? `Node type: ${issue.class_type}` : "",
            issue.node_id ? `Node ID: ${issue.node_id}` : "",
          ].filter(Boolean).join(" · "),
        })
      ))
    )
  );
}

function sourceSummary(state, persons) {
  const draftIds = new Set();
  for (const person of persons) {
    for (const draft of localDraftsForPerson(person, state)) draftIds.add(draft.draftId);
  }
  const remaining = persons.filter((person) =>
    !linkedPeopleForSource(person).length &&
    !localDraftsForPerson(person, state).length &&
    !sourceHasCurrentNoPersonReview(person.sourceRef, state) &&
    sourceDisposition(person) !== "not_person"
  ).length;
  return el(
    "div",
    { class: "plb-summary" },
    el(
      "div",
      { class: "plb-summary-title" },
      metaLabel("Current workflow", true),
      el("span", { class: "plb-meta plb-meta--dim", text: stamp(state.scannedAt) })
    ),
    el(
      "div",
      { class: "plb-tiles" },
      tile(persons.length, "source records", "var(--plb-ink)"),
      tile(draftIds.size, "people mapped", draftIds.size ? "var(--plb-ok)" : "var(--plb-ink)"),
      tile(remaining, "unclassified", remaining ? "var(--plb-warn)" : "var(--plb-ok)")
    ),
    remaining
      ? el(
          "div",
          { class: "plb-warnstrip" },
          el("span", { class: "plb-warnmark", text: "!" }),
          el("span", {
            text: `${remaining} ${remaining === 1 ? "source is" : "sources are"} not mapped yet. Use People for visual review; this list is the technical audit.`,
          })
        )
      : null
  );
}

function sourceCard(person, state) {
  const key = person.sourceRef || personLocalKey(person);
  const linked = linkedPeopleForSource(person);
  const drafts = localDraftsForPerson(person, state);
  const linkedIds = new Set(linked.map((candidate) => candidate.id || candidate.talentRecordId));
  const visibleDrafts = visiblePersonDrafts(drafts, linkedIds);
  const disposition = sourceDisposition(person);
  const variantCount = sourceVariantCount(person);
  const linkedNames = linked.map((candidate) => candidate.displayName || candidate.name).filter(Boolean);
  const draftNames = visibleDrafts.map((draft) => draft.displayName).filter(Boolean);
  const visibleNames = uniqueNames([...linkedNames, ...draftNames]);
  const personState = linked.length
    ? linkedNames.join(", ")
    : disposition === "not_person"
      ? "Not a person"
      : disposition === "review_required"
        ? "Review required"
        : "Not linked";
  const requestState = linked.length
    ? summarizeStates(linked.map(requestStateForPerson))
    : "Not ready";
  const internalState = linked.length
    ? summarizeStates(linked.map(internalStateForPerson))
    : "Not reviewed";
  const identityCandidates = identityCandidatesForSource(person.sourceRef, state);
  const activeIdentityLinks = identityCandidates.flatMap((candidate) =>
    identityLinksForCandidate(candidate, state).filter((link) => link.state !== "rejected")
  );
  const manualIssue = manualIdentityIssueForSource(person.sourceRef, state);
  const currentSourceHash = (state.identityPayload?.sourceHashes || []).find(
    (entry) => entry.sourceRef === person.sourceRef
  )?.sourceHash;
  const manualReviewNeeded = sourceNeedsManualReview(person, state);
  const currentNoPersonReview = sourceHasCurrentNoPersonReview(person.sourceRef, state);
  const previewMedia = sourceMedia(person);
  const reviewableVisual = sourceSupportsNoPersonReview(person);
  const noPersonBlocked = Boolean(
    linked.length || drafts.length || identityCandidates.length || activeIdentityLinks.length
  );
  const noPersonConflict = currentNoPersonReview && noPersonBlocked;
  const localNoPerson = currentNoPersonReview && !noPersonBlocked;
  const top = el(
    "div",
    {
      class: "plb-card-top",
      title: "Locate this source in the graph",
      onclick: () => {
        if (!focusPerson(person)) toast("Node not found in the open graph.");
      },
    },
    avatar(
      { ...person, name: linkedNames[0] || draftNames[0] || "?" },
      previewMedia
    ),
    el(
      "div",
      { class: "plb-card-id" },
      el(
        "div",
        { class: "plb-card-name-row" },
        el(
          "div",
          {},
          el("div", {
            class: "plb-card-name",
            text: sourceDisplayLabel(person),
          }),
          el(
            "div",
            { class: "plb-card-src" },
            el("span", { text: `${String(person.source_kind || "source").replaceAll("_", " ")} source` }),
            variantCount > 1
              ? el("span", { class: "plb-variant-count", text: `Used in ${variantCount} variants` })
              : null
          )
        ),
        el("span", {
          class: "plb-kind-tag",
          text: linked.length
            ? dispositionLabel(disposition, linked.length)
            : visibleDrafts.length
              ? `${visibleDrafts.length} ${visibleDrafts.length === 1 ? "person" : "people"}`
              : noPersonConflict
                ? "Review conflict"
              : localNoPerson
                ? "No person"
                : manualReviewNeeded
                  ? "Manual review"
              : dispositionLabel(disposition, 0),
        })
      )
    )
  );
  const actions = el("div", { class: "plb-actions" });
  actions.append(button(drafts.length ? "Edit people" : "Review people", "primary", async () => {
    if (!(await ensureCurrentPerson(person))) return;
    if (identityCandidates.length === 1) {
      const candidate = identityCandidates[0];
      const confirmedPersonIds = confirmedPersonIdsForCandidate(candidate, state);
      if (confirmedPersonIds.length > 1) {
        setActiveTab("people");
        toast(`This visual group is split across ${confirmedPersonIds.length} people. Open each person in People to edit their assigned appearances.`);
        return;
      }
      const draft = draftForCandidate(candidate, state, confirmedPersonIds[0] || "");
      openIdentityReviewDialog(
        candidate,
        confirmedPersonIds[0] || draft?.canonicalPersonId || draft?.draftId || ""
      );
    } else if (identityCandidates.length > 1) {
      setActiveTab("people");
      toast(`${identityCandidates.length} likely people share this source. Review their portrait groups separately.`);
    } else {
      openPersonDraftDialog(person);
    }
  }));
  actions.append(button(linked.length ? "Edit Pluribus links" : "Link to Pluribus", "secondary", () => {
    void openCanonicalLink(person);
  }));
  if (localNoPerson && !reviewableVisual) {
    actions.append(button("Return source to review", "secondary", () => {
      void setLocalSourceReview(person, "review_required");
    }));
  } else if (noPersonConflict) {
    actions.append(button("Return source to review", "secondary", () => {
      void setLocalSourceReview(person, "review_required");
    }));
  } else if (
    manualIssue
    && reviewableVisual
    && !linked.length
    && !drafts.length
    && !identityCandidates.length
    && !activeIdentityLinks.length
  ) {
    actions.append(button(localNoPerson ? "Review again" : "No person here", "secondary", () => {
      void setLocalSourceReview(person, localNoPerson ? "review_required" : "not_person");
    }));
  }
  const details = button(expanded.has(key) ? "Hide" : "Details", "secondary", () => {
    if (expanded.has(key)) expanded.delete(key);
    else expanded.add(key);
    render(getState());
  });
  actions.append(details);
  const card = el(
    "section",
    {
      class: `plb-card ${
        linked.length
          ? "linked"
          : noPersonConflict
            ? "needs_review"
          : disposition === "not_person"
            ? "not_person"
            : visibleDrafts.length || disposition === "review_required" || manualReviewNeeded ? "needs_review" : "unidentified"
      }${manualReviewNeeded ? " plb-manual-review" : ""}`,
    },
    top,
    manualReviewNeeded
      ? el("p", {
          class: "plb-manual-review-note",
          text: manualIssue?.code === "evidence_omitted_source"
            ? "Face evidence could not be retained within the local storage budget. Review this source directly."
            : manualIssue?.code === "no_face_detected"
              ? "No clear face was found. Check for a body, silhouette, mask, or distant performer."
              : manualIssue?.description
                || manualIssue?.title
                || "Identity analysis did not fully cover this source. Review it directly before moving to rights.",
        })
      : null,
    noPersonConflict
      ? el("p", {
          class: "plb-manual-review-note",
          text: "A prior no-person classification conflicts with detected or assigned people. Return this source to review before changing its people.",
        })
      : null,
    el("p", { class: "plb-use-sentence", text: plainLanguageUseSummary({ sourceRefs: [person.sourceRef] }, [person]) }),
    visibleNames.length
      ? el("div", { class: "plb-source-people" }, metaLabel("Mapped people"), el("strong", { text: visibleNames.join(", ") }))
      : null,
    actions
  );
  if (expanded.has(key)) {
    card.append(
      el(
        "div",
        { class: "plb-details" },
        detailRow("Source", sourceDisplayLabel(person)),
        variantCount
          ? detailRow("Workflow use", variantCount === 1 ? "Used once" : `Used in ${variantCount} variants`)
          : null,
        linked.length ? statusAxes(personState, requestState, internalState) : null,
        opsChips(person, (nodeId) => {
          if (!focusNodeById(nodeId)) toast("Node not found in the open graph.");
        }),
        !linked.length && reviewableVisual && currentSourceHash && !manualIssue && !noPersonBlocked
          ? el(
              "div",
              { class: "plb-detail-actions" },
              button(
                currentNoPersonReview ? "Return to review" : "Mark as no person",
                "ghost",
                () => void setLocalSourceReview(
                  person,
                  currentNoPersonReview ? "review_required" : "not_person"
                )
              )
            )
          : null
      )
    );
  }
  return card;
}

function identityCandidatesForSource(sourceRef, state = getState()) {
  if (!sourceRef) return [];
  return (state.identityPayload?.candidates || []).filter((candidate) =>
    (candidate.sourceRefs || []).includes(sourceRef)
    && candidateHasActiveOccurrencesForSource(candidate, state.identityPayload, sourceRef, state)
  );
}

function confirmedPersonIdsForCandidate(candidate, state = getState()) {
  return [...new Set(
    identityLinksForCandidate(candidate, state)
      .filter((link) => link.state === "confirmed")
      .map((link) => String(link.personId || link.person_id || ""))
      .filter(Boolean)
  )];
}

function manualIdentityIssueForSource(sourceRef, state = getState()) {
  if (!sourceRef) return null;
  return identityManualReviewItems(state.identityPayload).find((issue) =>
    issue.sourceRef === sourceRef
  ) || null;
}

function sourceNeedsManualReview(person, state = getState()) {
  if (!manualIdentityIssueForSource(person.sourceRef, state)) return false;
  if (linkedPeopleForSource(person).length || localDraftsForPerson(person, state).length) return false;
  if (sourceHasCurrentNoPersonReview(person.sourceRef, state)) return false;
  return true;
}

function sourceHasCurrentNoPersonReview(sourceRef, state = getState()) {
  const review = state.sourceReviews?.[sourceRef];
  if (review?.state !== "not_person") return false;
  const currentHash = (state.identityPayload?.sourceHashes || []).find(
    (entry) => entry.sourceRef === sourceRef
  )?.sourceHash;
  return Boolean(currentHash && review.sourceHash === currentHash);
}

async function setLocalSourceReview(person, reviewState) {
  if (!(await ensureCurrentPerson(person))) return;
  const openedState = getState();
  const workflowRef = openedState.workflowBinding?.workflowRef;
  const scanEpoch = openedState.scanEpoch;
  if (!workflowRef || !person.sourceRef) {
    toast("Find people again before reviewing this source.");
    return;
  }
  if (reviewState === "not_person") {
    if (!sourceSupportsNoPersonReview(person)) {
      toast("Only a previewable image or video can be marked as containing no person. Audio and other performance sources require their own rights review.");
      return;
    }
    const identityCandidates = identityCandidatesForSource(person.sourceRef, openedState);
    const activeIdentityLinks = identityCandidates.flatMap((candidate) =>
      identityLinksForCandidate(candidate, openedState).filter((link) => link.state !== "rejected")
    );
    if (
      identityCandidates.length
      || activeIdentityLinks.length
      || linkedPeopleForSource(person).length
      || localDraftsForPerson(person, openedState).length
    ) {
      setActiveTab("people");
      toast("This source has detected or assigned people. Review those people before classifying the source as containing no person.");
      return;
    }
  }
  try {
    const sourceHash = (openedState.identityPayload?.sourceHashes || []).find(
      (entry) => entry.sourceRef === person.sourceRef
    )?.sourceHash;
    if (!sourceHash) throw new Error("Analyze the current media before saving this review.");
    const result = await saveLocalSourceReview(
      workflowRef,
      person.sourceRef,
      reviewState,
      sourceHash
    );
    const afterSave = getState();
    const currentSourceHash = (afterSave.identityPayload?.sourceHashes || []).find(
      (entry) => entry.sourceRef === person.sourceRef
    )?.sourceHash;
    if (
      afterSave.scanEpoch !== scanEpoch
      || afterSave.workflowBinding?.workflowRef !== workflowRef
      || currentSourceHash !== sourceHash
    ) return;
    setState({
      sourceReviews: {
        ...(getState().sourceReviews || {}),
        [person.sourceRef]: result.review || {
          sourceRef: person.sourceRef,
          state: reviewState,
          sourceHash,
        },
      },
      manifestSynced: false,
    });
    const latest = getState();
    if (
      latest.connection?.state === "connected"
      && latest.activeProjectId
      && latest.workflowBinding?.projectId === latest.activeProjectId
    ) {
      await syncCurrentRightsManifest();
    }
    toast(reviewState === "not_person" ? "Marked as containing no person." : "Source returned to manual review.");
  } catch (error) {
    toast(error.message || "Could not save the source review.");
  }
}

function footer(state) {
  const project = activeProject();
  return el(
    "div",
    { class: "plb-footer" },
    el("span", {
      class: "plb-footer-context",
      text: project
        ? project.title
        : state.personDrafts.length
          ? `${state.personDrafts.length} ${state.personDrafts.length === 1 ? "person" : "people"} added`
          : "Current workflow",
    }),
    button(state.scanning ? "Scanning…" : "Rescan", "secondary", scan)
  );
}

function sourceDisposition(person) {
  const sourceRef = person.sourceRef || getState().sourceRefs[personLocalKey(person)];
  if (!sourceRef) return "detected";
  const matches = projectSourceLinks().filter((link) =>
    (link.sourceRef || link.source_ref) === sourceRef
  );
  if (matches.some((link) => link.disposition === "linked")) return "linked";
  return matches[0]?.disposition || "detected";
}

function dispositionLabel(disposition, linkedCount) {
  if (linkedCount > 1) return `${linkedCount} people`;
  if (linkedCount === 1) return "Linked";
  if (disposition === "not_person") return "Not person";
  if (disposition === "review_required") return "Review";
  return "Detected";
}

function tile(value, label, color) {
  const number = el("div", { class: "plb-tile-n", text: String(value) });
  number.style.color = color;
  return el("div", { class: "plb-tile" }, number, el("div", { class: "plb-tile-l", text: label }));
}

function detailRow(label, value) {
  return el("div", { class: "plb-scope-row" }, el("dt", { text: label }), el("dd", { text: value }));
}

function empty(message, action = null) {
  return el("div", { class: "plb-empty" }, pluribusMark(20), el("div", { text: message }), action);
}

function workflowKindOptions(current) {
  return [
    ["character_sheet", "Character sheet"],
    ["storyboard", "Storyboard"],
    ["production", "Production"],
    ["final", "Final review"],
    ["other", "Other"],
  ].map(([value, label]) => {
    const node = el("option", { value, text: label });
    node.selected = current === value;
    return node;
  });
}

function summarizeStates(values) {
  const unique = [...new Set(values.filter(Boolean))];
  return unique.length <= 1 ? unique[0] || "Not ready" : "Mixed — review each person";
}

function normalizedName(value) {
  return String(value || "").trim().toLocaleLowerCase();
}

function uniqueNames(values) {
  const seen = new Set();
  return values.filter((value) => {
    const key = normalizedName(value);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function ensureCurrentPerson(person) {
  try {
    if (await personMatchesCurrentWorkflow(person)) return true;
  } catch (error) {
    console.warn("[Pluribus] could not verify workflow context", error);
  }
  toast("The graph changed after this scan. Find people again before taking action.");
  return false;
}

async function openCanonicalLink(person) {
  if (!(await ensureCurrentPerson(person))) return;
  requirePluribusConnection(async () => {
    if (!(await ensureCurrentPerson(person))) return;
    await withCanonicalProject(async () => {
      if (await ensureCurrentPerson(person)) openLinkPersonDialog(person);
    });
  });
}

async function withCanonicalProject(action) {
  if (!getState().workspaceReady) await loadProductContext();
  let state = getState();
  if (!state.workspace) {
    openWorkspaceSetupDialog(() => withCanonicalProject(action));
    return;
  }
  if (!state.projects.length) {
    openProjectDialog(() => withCanonicalProject(action));
    return;
  }
  const projectId = state.activeProjectId || state.projects[0]?.id;
  if (!projectId) {
    openProjectDialog(() => withCanonicalProject(action));
    return;
  }
  if (
    state.workflowBinding?.projectId !== projectId ||
    !state.projectContext ||
    !state.manifestSynced
  ) {
    await selectProject(projectId, state.workflowBinding?.workflowKind || "production");
  }
  await action();
}

function stamp(date) {
  if (!date) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
