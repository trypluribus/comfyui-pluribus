import {
  getProject,
  resolveLocalWorkflow,
  saveProjectSourceLinks,
} from "./api.js";
import { localWorkflowKey } from "./canvas.js";
import { manifestOverridesForLocalReviews, manifestSourcesForScan } from "./manifest.js";
import {
  getState,
  projectSourceLinks,
  setState,
} from "./store.js";

let syncRequestId = 0;
let syncBarrier = Promise.resolve();

// Persist the full current rights manifest after every rescan and workflow-kind
// change. The ComfyUI graph remains local: only opaque refs, dispositions,
// canonical person IDs, and normalized operation classes cross the boundary.
export async function syncCurrentRightsManifest(overrides = new Map()) {
  const state = getState();
  const requestId = ++syncRequestId;
  const scanEpoch = state.scanEpoch;
  const projectId = state.activeProjectId;
  const workflowRef = state.workflowBinding?.workflowRef;
  if (!projectId || !workflowRef || !state.scan) {
    throw new Error("Find people and choose a project before syncing this workflow.");
  }

  const workflowKind = state.workflowBinding.workflowKind || "production";
  const graphHash = state.scan.workflow_fingerprint || "";
  const localKey = localWorkflowKey();
  const persons = [...(state.scan.persons || [])];
  const sourceRefs = { ...(state.sourceRefs || {}) };
  const existingSources = [...projectSourceLinks()];
  const sourceReviews = { ...(state.sourceReviews || {}) };
  const sourceHashes = [...(state.identityPayload?.sourceHashes || [])];
  const personDrafts = [...(state.personDrafts || [])];
  const capturedOverrides = overrides instanceof Map
    ? new Map(overrides)
    : new Map(Object.entries(overrides || {}));
  const contextIsCurrent = () => {
    const latest = getState();
    return requestId === syncRequestId
      && latest.scanEpoch === scanEpoch
      && latest.activeProjectId === projectId
      && latest.workflowBinding?.workflowRef === workflowRef;
  };

  setState({ manifestSynced: false });
  const sources = manifestSourcesForScan(
    persons,
    sourceRefs,
    existingSources,
    manifestOverridesForLocalReviews(
      sourceReviews,
      existingSources,
      capturedOverrides,
      sourceHashes,
      personDrafts
    )
  );
  const priorSync = syncBarrier;
  let releaseSync;
  syncBarrier = new Promise((resolve) => {
    releaseSync = resolve;
  });
  await priorSync;
  try {
    if (!contextIsCurrent()) return null;
    await saveProjectSourceLinks(projectId, {
      workflowRef,
      workflowKind,
      graphHash: graphHash || undefined,
      sources,
    });
    if (!contextIsCurrent()) return null;
    const workflowBinding = await resolveLocalWorkflow(localKey, graphHash);
    if (!contextIsCurrent()) return null;
    const payload = await getProject(projectId, workflowRef);
    if (!contextIsCurrent()) return null;
    const projectContext = payload.project || payload.projectContext || payload;
    setState({ workflowBinding, projectContext, manifestSynced: true });
    return projectContext.workflow;
  } finally {
    releaseSync();
  }
}
