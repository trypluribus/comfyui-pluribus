import {
  getProject,
  resolveLocalWorkflow,
  saveProjectSourceLinks,
} from "./api.js";
import { localWorkflowKey } from "./canvas.js";
import {
  manifestOverridesForLocalReviews,
  manifestSourcesForScan,
  mergeManifestOverrideMaps,
} from "./manifest.js";
import {
  identityReviewHash,
  projectIdentitySources,
} from "./identity-projection.js";
import {
  getState,
  setState,
} from "./store.js";

const pendingSyncBatches = [];
let syncDrainPromise = null;
const SOURCE_SYNC_ATTEMPTS = 5;

function projectContextFromPayload(payload = {}) {
  return payload.project || payload.projectContext || payload;
}

function sourceSyncConflict(error) {
  return error?.status === 409 && [
    "rights_manifest_conflict",
    // Kept only for rolling deployments where an older hosted instance still
    // owns the retired metadata lease. New writes serialize in Postgres.
    "rights_manifest_sync_in_progress",
  ].includes(error?.code);
}

function retryDelay(attempt) {
  return new Promise((resolve) => setTimeout(resolve, 75 * (2 ** attempt)));
}

function syncContextChangedError() {
  const error = new Error(
    "The workflow changed before its source links were saved. Reopen the current People view and retry."
  );
  error.code = "workflow_context_changed";
  return error;
}

function captureSyncSnapshot(overrides) {
  const state = getState();
  const scanEpoch = state.scanEpoch;
  const projectId = state.activeProjectId;
  const workflowRef = state.workflowBinding?.workflowRef;
  if (!projectId || !workflowRef || !state.scan) {
    throw new Error("Find people and choose a project before syncing this workflow.");
  }
  return {
    contextKey: `${scanEpoch}|${projectId}|${workflowRef}`,
    scanEpoch,
    projectId,
    workflowRef,
    workflowKind: state.workflowBinding.workflowKind || "production",
    graphHash: state.scan.workflow_fingerprint || "",
    localKey: localWorkflowKey(),
    persons: [...(state.scan.persons || [])],
    sourceRefs: { ...(state.sourceRefs || {}) },
    sourceReviews: { ...(state.sourceReviews || {}) },
    sourceHashes: [...(state.identityPayload?.sourceHashes || [])],
    identityPayload: state.identityPayload,
    identityLinks: [...(state.identityLinks || [])],
    identityRevision: Number.isInteger(state.identityLinksRevision)
      ? state.identityLinksRevision
      : undefined,
    personDrafts: [...(state.personDrafts || [])],
    canonicalPeople: [
      ...(state.projectContext?.people || state.projectContext?.project?.people || []),
    ],
    overrides: overrides instanceof Map
      ? new Map(overrides)
      : new Map(Object.entries(overrides || {})),
  };
}

function syncContextIsCurrent(snapshot) {
  const latest = getState();
  return latest.scanEpoch === snapshot.scanEpoch
    && latest.activeProjectId === snapshot.projectId
    && latest.workflowBinding?.workflowRef === snapshot.workflowRef;
}

async function performManifestSync(snapshot) {
  if (!syncContextIsCurrent(snapshot)) throw syncContextChangedError();
  let committedWorkflow = null;
  for (let attempt = 0; attempt < SOURCE_SYNC_ATTEMPTS; attempt += 1) {
    const latestPayload = await getProject(snapshot.projectId, snapshot.workflowRef);
    if (!syncContextIsCurrent(snapshot)) throw syncContextChangedError();
    const latestContext = projectContextFromPayload(latestPayload);
    const existingSources = [
      ...(latestContext.sourceLinks || latestContext.sources || []),
    ];
    const identityProjection = projectIdentitySources(
      snapshot.identityPayload || {},
      snapshot.identityLinks,
      snapshot.personDrafts,
      snapshot.canonicalPeople
    );
    const completeOverrides = mergeManifestOverrideMaps(
      snapshot.overrides,
      identityProjection
    );
    const sources = manifestSourcesForScan(
      snapshot.persons,
      snapshot.sourceRefs,
      existingSources,
      manifestOverridesForLocalReviews(
        snapshot.sourceReviews,
        existingSources,
        completeOverrides,
        snapshot.sourceHashes,
        snapshot.personDrafts
      )
    );
    const baseManifestVersion = Number.isInteger(latestContext.workflow?.rightsManifestVersion)
      ? latestContext.workflow.rightsManifestVersion
      : 0;
    const reviewHash = snapshot.identityPayload
      ? await identityReviewHash(
          snapshot.identityPayload,
          snapshot.identityLinks,
          snapshot.personDrafts
        )
      : "";
    try {
      if (!syncContextIsCurrent(snapshot)) throw syncContextChangedError();
      const saved = await saveProjectSourceLinks(snapshot.projectId, {
        workflowRef: snapshot.workflowRef,
        workflowKind: snapshot.workflowKind,
        graphHash: snapshot.graphHash || undefined,
        identityReviewHash: reviewHash || undefined,
        identityRevision: snapshot.identityRevision,
        baseManifestVersion,
        sources,
      });
      committedWorkflow = saved?.workflow || saved?.project?.workflow || saved || null;
      break;
    } catch (error) {
      if (!sourceSyncConflict(error) || attempt === SOURCE_SYNC_ATTEMPTS - 1) throw error;
      if (error.code === "rights_manifest_sync_in_progress") {
        await retryDelay(attempt);
      }
      if (!syncContextIsCurrent(snapshot)) throw syncContextChangedError();
    }
  }

  // The delta has committed at this point. A later workflow switch must not
  // turn that committed write into a false cancellation (and a false retry
  // prompt) for the identity review that initiated it.
  if (!syncContextIsCurrent(snapshot)) return committedWorkflow;
  try {
    const workflowBinding = await resolveLocalWorkflow(snapshot.localKey, snapshot.graphHash);
    if (!syncContextIsCurrent(snapshot)) return committedWorkflow;
    const payload = await getProject(snapshot.projectId, snapshot.workflowRef);
    if (!syncContextIsCurrent(snapshot)) return committedWorkflow;
    const projectContext = projectContextFromPayload(payload);
    setState({ workflowBinding, projectContext, manifestSynced: true });
    return projectContext.workflow;
  } catch (error) {
    // Saving the canonical delta succeeded; only the local refresh failed.
    // Keep confirmation gated until a later refresh without telling the user
    // that their identity assignment itself is still pending.
    setState({ manifestSynced: false });
    console.warn("Pluribus manifest committed but local refresh failed", error);
    return committedWorkflow;
  }
}

async function drainManifestSyncQueue() {
  while (pendingSyncBatches.length) {
    const batch = pendingSyncBatches.shift();
    setState({ manifestSynced: false });
    try {
      const workflow = await performManifestSync(batch.snapshot);
      for (const waiter of batch.waiters) waiter.resolve(workflow);
    } catch (error) {
      for (const waiter of batch.waiters) waiter.reject(error);
    }
  }
}

function scheduleManifestSyncDrain() {
  if (syncDrainPromise) return;
  // Defer one microtask so multiple review handlers fired in the same browser
  // turn become one complete manifest replacement.
  syncDrainPromise = Promise.resolve()
    .then(drainManifestSyncQueue)
    .finally(() => {
      syncDrainPromise = null;
      if (pendingSyncBatches.length) scheduleManifestSyncDrain();
    });
}

// Persist the full current rights manifest after every rescan and workflow-kind
// change. Same-context calls are queued; pending identity add/remove deltas are
// coalesced, and every caller resolves only after its batch commits.
export function syncCurrentRightsManifest(overrides = new Map()) {
  let snapshot;
  try {
    snapshot = captureSyncSnapshot(overrides);
  } catch (error) {
    return Promise.reject(error);
  }
  setState({ manifestSynced: false });
  return new Promise((resolve, reject) => {
    const pending = pendingSyncBatches.at(-1);
    if (pending?.snapshot.contextKey === snapshot.contextKey) {
      pending.snapshot = {
        ...snapshot,
        overrides: mergeManifestOverrideMaps(
          pending.snapshot.overrides,
          snapshot.overrides
        ),
      };
      pending.waiters.push({ resolve, reject });
    } else {
      pendingSyncBatches.push({ snapshot, waiters: [{ resolve, reject }] });
    }
    scheduleManifestSyncDrain();
  });
}
