// Minimal state container. Render code subscribes and re-renders on change;
// keeping this pure-data makes a later framework migration mechanical.

const state = {
  scan: null, // { summary, persons } from /pluribus/scan
  workflow: null, // API-format snapshot the scan ran against
  scanning: false,
  error: null,
  invited: new Set(), // workflow/source/scope contexts invited this session
  scannedAt: null,
  scanEpoch: 0, // incremented when ComfyUI configures a different graph
  connection: null, // { state, account_email?, ... } from /pluribus/connect
  workspace: null, // explicit self-serve or existing canonical workspace
  workspaceReady: false,
  projects: [],
  activeProjectId: null,
  projectContext: null,
  workflowBinding: null,
  sourceRefs: {}, // local person identity -> opaque 64-hex source reference
  personDrafts: [], // private local drafts loaded from bindings.json
  sourceReviews: {}, // local no-face outcomes keyed by opaque sourceRef
  manifestSynced: false, // current scan + workflow kind are canonical upstream
  projectLoading: false,
  identityJob: null, // { jobId, state, progress? } from the local media worker
  identityPayload: null, // normalized { coverage, candidates, occurrences, issues }
  identityAnalyzing: false,
  identityError: null,
  identityCapabilities: null,
  identityCapabilitiesLoading: false,
  identityModelsInstalling: false,
  identityLinks: [], // candidate-specific confirmations; do not infer identity from shared sources
  identityLinksRevision: null, // compare-and-set revision for the current analysis link document
  identitySyncState: "saved_local", // saved_local | sync_pending | reconnect_required | synced
  identitySyncIssue: null, // safe actionable portrait/identity sync summary
};

const listeners = new Set();

export function getState() {
  return state;
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setState(patch) {
  Object.assign(state, patch);
  for (const listener of listeners) listener(state, patch);
}

export function invalidateScan() {
  setState({
    scan: null,
    workflow: null,
    scanning: false,
    error: null,
    scannedAt: null,
    scanEpoch: state.scanEpoch + 1,
    sourceRefs: {},
    personDrafts: [],
    sourceReviews: {},
    manifestSynced: false,
    workflowBinding: null,
    activeProjectId: null,
    projectContext: null,
    identityJob: null,
    identityPayload: null,
    identityAnalyzing: false,
    identityError: null,
    identityLinks: [],
    identityLinksRevision: null,
    identitySyncState: "saved_local",
    identitySyncIssue: null,
  });
}

export function isWorkflowContextReady() {
  const workflowRef = state.workflowBinding?.workflowRef;
  return Boolean(
    state.scan &&
      state.workflow &&
      state.manifestSynced &&
      workflowRef &&
      state.activeProjectId &&
      state.workflowBinding?.projectId === state.activeProjectId &&
      state.projectContext?.workflow?.workflowRef === workflowRef
  );
}

export function activeProject() {
  return state.projects.find((project) => project.id === state.activeProjectId) || null;
}

export function projectPeople() {
  return state.projectContext?.people || state.projectContext?.project?.people || [];
}

export function projectSourceLinks() {
  return state.projectContext?.sourceLinks || state.projectContext?.sources || [];
}

function inviteKey(person) {
  return JSON.stringify([
    person.workflow_name || "",
    person.workflow_fingerprint || "",
    person.source_kind || "",
    person.source_key || "",
    person.scope_statements || [],
  ]);
}

export function markInvited(person) {
  state.invited.add(inviteKey(person));
  setState({});
}

export function wasInvited(person) {
  return state.invited.has(inviteKey(person));
}

// Persons that can be invited and have not been this session.
export function invitablePersons() {
  const persons = state.scan?.persons || [];
  return persons.filter(
    (person) => person.available_actions.includes("invite") && !wasInvited(person)
  );
}

// Anything a producer still has to deal with.
export function needsActionCount() {
  const persons = state.scan?.persons || [];
  return persons.filter((p) =>
    ["needs_review", "restricted", "unidentified"].includes(p.state)
  ).length;
}
