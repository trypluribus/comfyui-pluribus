import {
  deleteIdentityAnalysisJob,
  getIdentityCapabilities,
  getIdentityAnalysisJob,
  getIdentityLinks,
  getIdentitySyncStatus,
  getLocalPersonDrafts,
  installIdentityModels,
  retryIdentitySync,
  saveIdentityDecision as putIdentityDecision,
  saveIdentityLinks as putIdentityLinks,
  startIdentityAnalysis,
} from "./api.js";
import { identityResultFromJob, normalizeIdentityPayload } from "./identity-contract.js";
import { personLocalKey } from "./manifest.js";
import { getState, setState } from "./store.js";

let analysisGeneration = 0;

function jobIdFor(value) {
  return String(value?.jobId || value?.job_id || value?.id || "");
}

function jobState(value) {
  return String(value?.state || value?.status || "queued").toLowerCase();
}

function finished(state) {
  return ["completed", "complete", "succeeded", "failed", "error", "cancelled", "canceled"].includes(state);
}

function wait(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function sourcePayload(scan, sourceRefs) {
  return (scan?.persons || []).map((source) => ({
    ...source,
    sourceRef: source.sourceRef || sourceRefs?.[personLocalKey(source)],
  }));
}

export async function analyzeWorkflowIdentity({ workflowName, workflowFingerprint, workflowBinding, scan }) {
  const priorJob = getState().identityJob;
  const priorJobId = jobIdFor(priorJob);
  const priorJobState = jobState(priorJob);
  const retirePriorAfterStart = Boolean(priorJobId && finished(priorJobState));
  const generation = ++analysisGeneration;
  const scanEpoch = getState().scanEpoch;
  setState({
    identityAnalyzing: true,
    identityError: null,
    identityPayload: null,
    identityLinks: [],
    identityLinksRevision: null,
    identityJob: { state: "queued", progress: 0 },
  });

  try {
    if (priorJobId && !finished(priorJobState)) {
      try {
        await deleteIdentityAnalysisJob(priorJobId);
      } catch {
        // The old job may have completed between the local state read and the
        // cancellation request. Generation guards still prevent stale output.
      }
      if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) return;
    }
    const capabilities = await getIdentityCapabilities();
    if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) {
      if (retirePriorAfterStart) {
        try {
          await deleteIdentityAnalysisJob(priorJobId);
        } catch {
          // Retention cleanup is best effort for an obsolete invocation.
        }
      }
      return;
    }
    setState({ identityCapabilities: capabilities, identityCapabilitiesLoading: false });
    if (capabilities?.state !== "ready") {
      if (retirePriorAfterStart) {
        try {
          await deleteIdentityAnalysisJob(priorJobId);
        } catch {
          // Retention cleanup is best effort when analysis is unavailable.
        }
      }
      if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) return;
      setState({ identityAnalyzing: false, identityJob: null, identityError: null });
      return;
    }
    const sources = sourcePayload(scan, getState().sourceRefs);
    const started = await startIdentityAnalysis({
      workflowName,
      workflowFingerprint,
      workflowRef: workflowBinding?.workflowRef || "",
      sources,
    });
    const startedJobId = jobIdFor(started);
    if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) {
      if (startedJobId) {
        try {
          await deleteIdentityAnalysisJob(startedJobId);
        } catch {
          // Stale-generation cleanup must not revive obsolete UI state.
        }
      }
      if (retirePriorAfterStart && priorJobId !== startedJobId) {
        try {
          await deleteIdentityAnalysisJob(priorJobId);
        } catch {
          // Retention cleanup is best effort for an obsolete invocation.
        }
      }
      return;
    }
    if (retirePriorAfterStart && priorJobId !== startedJobId) {
      try {
        await deleteIdentityAnalysisJob(priorJobId);
      } catch {
        // The new job is already durable; old-job retention cleanup is best effort.
      }
      if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) {
        if (startedJobId) {
          try {
            await deleteIdentityAnalysisJob(startedJobId);
          } catch {
            // Stale-generation cleanup must not revive obsolete UI state.
          }
        }
        return;
      }
    }

    const startedResult = identityResultFromJob(started);
    if (startedResult) {
      setState({
        identityJob: { ...started, state: "completed" },
        identityPayload: startedResult,
        identityLinks: startedResult.links || [],
        identityLinksRevision: startedResult.linksRevision,
        identityAnalyzing: false,
      });
      return;
    }

    const jobId = jobIdFor(started);
    if (!jobId) {
      // Permit a synchronous implementation that returns the completed payload
      // without wrapping it in a job envelope.
      if (started?.candidates || started?.occurrences || started?.coverage) {
        const result = normalizeIdentityPayload(started);
        setState({
          identityJob: { state: "completed" },
          identityPayload: result,
          identityLinks: result.links || [],
          identityLinksRevision: result.linksRevision,
          identityAnalyzing: false,
        });
        return;
      }
      throw new Error("The identity worker did not return a job id.");
    }

    setState({ identityJob: { ...started, jobId, state: jobState(started) } });
    let attempt = 0;
    while (generation === analysisGeneration && scanEpoch === getState().scanEpoch) {
      await wait(Math.min(2200, 650 + attempt * 120));
      if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) return;
      const job = await getIdentityAnalysisJob(jobId);
      if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) return;
      const state = jobState(job);
      setState({ identityJob: { ...job, jobId, state } });
      const result = identityResultFromJob(job);
      if (result) {
        setState({
          identityPayload: result,
          identityLinks: result.links || [],
          identityLinksRevision: result.linksRevision,
          identityAnalyzing: false,
        });
        return;
      }
      if (finished(state)) {
        throw new Error(job.message || job.error || "Identity analysis did not complete.");
      }
      attempt += 1;
    }
  } catch (error) {
    if (retirePriorAfterStart) {
      try {
        await deleteIdentityAnalysisJob(priorJobId);
      } catch {
        // A failed capability/start request must not strand the previous
        // completed job's private evidence after its UI handle was replaced.
      }
    }
    if (generation !== analysisGeneration || scanEpoch !== getState().scanEpoch) return;
    const unavailable = /404|not found/i.test(String(error?.message || error));
    setState({
      identityAnalyzing: false,
      identityError: new Error(
        unavailable
          ? "Local identity analysis is not available in this ComfyUI install yet. Source review still works."
          : error?.message || "Identity analysis could not finish."
      ),
    });
  }
}

export async function refreshIdentityLinks(jobId = jobIdFor(getState().identityJob)) {
  if (!jobId) {
    setState({ identityLinks: [], identityLinksRevision: null });
    return { links: [], revision: null };
  }
  try {
    const payload = await getIdentityLinks(jobId);
    const links = Array.isArray(payload?.links) ? payload.links : [];
    const revision = Number.isInteger(payload?.revision) && payload.revision >= 0
      ? payload.revision
      : null;
    if (jobIdFor(getState().identityJob) === jobId) {
      setState({ identityLinks: links, identityLinksRevision: revision });
    }
    return { links, revision };
  } catch {
    return null;
  }
}

export async function commitIdentityLinks(jobId, links, baseRevision) {
  if (!Number.isInteger(baseRevision) || baseRevision < 0) {
    throw new Error("Visual identity links do not have a current revision. Reopen this review and try again.");
  }
  const payload = await putIdentityLinks(jobId, links, baseRevision);
  const savedLinks = Array.isArray(payload?.links) ? payload.links : links;
  const revision = Number.isInteger(payload?.revision) && payload.revision >= 0
    ? payload.revision
    : null;
  if (revision == null) throw new Error("The visual identity update did not return a revision.");
  if (jobIdFor(getState().identityJob) === jobId) {
    setState({ identityLinks: savedLinks, identityLinksRevision: revision });
  }
  return { links: savedLinks, revision };
}

export async function commitIdentityDecision(jobId, decision) {
  if (!jobId) throw new Error("Run identity analysis again before saving this review.");
  if (!Number.isInteger(decision?.baseRevision) || decision.baseRevision < 0) {
    throw new Error("Visual identity links do not have a current revision. Reopen this review and try again.");
  }
  const payload = await putIdentityDecision(jobId, decision);
  const links = Array.isArray(payload?.links) ? payload.links : [];
  const revision = Number.isInteger(payload?.revision) && payload.revision >= 0
    ? payload.revision
    : null;
  if (revision == null) throw new Error("The identity decision did not return a revision.");
  let syncState = typeof payload?.syncState === "string"
    ? payload.syncState
    : payload?.syncState?.state || "saved_local";
  const portraitState = String(payload?.portraitSync?.state || "");
  const portraitIssue = portraitState === "projection_blocked"
    ? {
        code: String(payload?.portraitSync?.code || "identity_projection_blocked"),
        message: String(payload?.portraitSync?.message || "Portrait sync is paused until identity review is corrected."),
      }
    : null;
  if (["sync_pending", "projection_blocked"].includes(portraitState) && syncState !== "reconnect_required") {
    syncState = "sync_pending";
  }
  if (jobIdFor(getState().identityJob) === jobId) {
    setState({
      identityLinks: links,
      identityLinksRevision: revision,
      ...(Array.isArray(payload?.personDrafts)
        ? { personDrafts: payload.personDrafts }
        : {}),
      identitySyncState: syncState,
      identitySyncIssue: portraitIssue,
    });
  }
  const current = getState();
  if (
    syncState === "sync_pending"
    && jobIdFor(current.identityJob) === jobId
    && current.scan
    && current.activeProjectId
    && current.workflowBinding?.workflowRef
  ) {
    // The decision endpoint has already committed the local transaction and
    // durable outbox. Do not keep the review modal open while the second-phase
    // workspace manifest sync runs; the explicit sync state remains visible
    // in the People view and the outbox will retry safely.
    void (async () => {
      try {
        const { syncCurrentRightsManifest } = await import("./sync-manifest.js");
        await syncCurrentRightsManifest();
        await refreshIdentityWorkspaceSyncState();
      } catch {
        // Network, auth, or manifest conflicts remain visible as pending and
        // retry on reconnect, panel load, startup, or explicit retry.
      }
    })();
  }
  return { ...payload, links, revision, syncState };
}

function latestIdentitySyncEntry(payload = {}, workflowRef = "", projectId = "") {
  const entries = Array.isArray(payload?.entries) ? payload.entries : [];
  const scoped = entries.filter((entry) =>
    (!workflowRef || String(entry?.workflowRef || "") === workflowRef)
    && (!projectId || String(entry?.projectId || "") === projectId)
  );
  return [...scoped].sort((left, right) =>
    Number(right?.revision || 0) - Number(left?.revision || 0)
      || String(right?.entryId || "").localeCompare(String(left?.entryId || ""))
  )[0] || null;
}

export function identityWorkspaceSyncSummary(
  payload = {},
  workflowRef = "",
  projectId = ""
) {
  const identityEntry = latestIdentitySyncEntry(payload, workflowRef, projectId);
  const portraits = (Array.isArray(payload?.portraitEntries) ? payload.portraitEntries : [])
    .filter((entry) => !projectId || String(entry?.projectId || "") === projectId);
  const portraitReconnect = portraits.some((entry) =>
    entry?.requiresReconnect === true
    || Number(entry?.lastStatus || 0) === 401
    || String(entry?.state || "") === "reconnect_required"
  );
  const portraitIssueEntry = portraits.find((entry) =>
    String(entry?.state || "") === "projection_blocked"
  );
  const portraitPending = portraits.some((entry) =>
    ["waiting_for_person", "pending", "retire_pending", "projection_blocked", "sync_pending"]
      .includes(String(entry?.state || ""))
  );
  const identityState = String(identityEntry?.state || "");
  const state = (
    identityState === "reconnect_required" || portraitReconnect
      ? "reconnect_required"
      : identityState === "sync_pending" || portraitPending
        ? "sync_pending"
        : identityState === "saved_local"
          ? "saved_local"
          : identityState === "synced"
            ? "synced"
            : portraits.length && portraits.every((entry) => String(entry?.state || "") === "synced")
              ? "synced"
              : null
  );
  const issue = portraitIssueEntry
    ? {
        code: String(portraitIssueEntry.code || "identity_projection_blocked"),
        message: String(portraitIssueEntry.message || "Portrait sync is paused until identity review is corrected."),
      }
    : null;
  return { state, identityEntry, issue };
}

export async function refreshIdentityWorkspaceSyncState() {
  try {
    const payload = await getIdentitySyncStatus();
    const summary = identityWorkspaceSyncSummary(
      payload,
      String(getState().workflowBinding?.workflowRef || ""),
      String(getState().activeProjectId || "")
    );
    const state = String(summary.state || "");
    if (["saved_local", "sync_pending", "reconnect_required", "synced"].includes(state)) {
      setState({ identitySyncState: state, identitySyncIssue: summary.issue });
      return state;
    }
  } catch {
    // Older local backends do not expose outbox status. Keep the last explicit
    // state rather than treating an unavailable status route as success.
  }
  return null;
}

export async function retryIdentityWorkspaceSync({ syncManifest = true } = {}) {
  const workflowRef = String(getState().workflowBinding?.workflowRef || "");
  try {
    const payload = await retryIdentitySync();
    const summary = identityWorkspaceSyncSummary(
      payload,
      workflowRef,
      String(getState().activeProjectId || "")
    );
    const pendingState = String(summary.state || "sync_pending");
    if (["saved_local", "sync_pending", "reconnect_required", "synced"].includes(pendingState)) {
      setState({ identitySyncState: pendingState, identitySyncIssue: summary.issue });
    }
    if (workflowRef) {
      const drafts = await getLocalPersonDrafts(workflowRef);
      setState({ personDrafts: Array.isArray(drafts?.drafts) ? drafts.drafts : [] });
    }
    const current = getState();
    if (
      syncManifest
      && current.connection?.state === "connected"
      && current.scan
      && current.activeProjectId
      && current.workflowBinding?.workflowRef
    ) {
      const { syncCurrentRightsManifest } = await import("./sync-manifest.js");
      await syncCurrentRightsManifest();
    }
    return await refreshIdentityWorkspaceSyncState() || pendingState;
  } catch {
    return await refreshIdentityWorkspaceSyncState();
  }
}

export function identityRevisionConflict(error) {
  return /revision|changed in another|conflict|stale link/i.test(String(error?.message || error || ""));
}

export async function refreshIdentityCapabilities() {
  setState({ identityCapabilitiesLoading: true });
  try {
    const capabilities = await getIdentityCapabilities();
    setState({ identityCapabilities: capabilities, identityCapabilitiesLoading: false });
    return capabilities;
  } catch {
    setState({ identityCapabilitiesLoading: false });
    return null;
  }
}

export async function installLocalIdentityModels() {
  const capabilities = getState().identityCapabilities;
  const modelId = capabilities?.modelBundle?.modelId;
  if (!modelId) throw new Error("No supported local identity model bundle was offered.");
  setState({ identityModelsInstalling: true, identityError: null });
  try {
    await installIdentityModels(modelId);
    return await refreshIdentityCapabilities();
  } finally {
    setState({ identityModelsInstalling: false });
  }
}

export async function cancelIdentityAnalysis({ remove = false } = {}) {
  analysisGeneration += 1;
  const jobId = jobIdFor(getState().identityJob);
  setState({ identityAnalyzing: false, identityJob: jobId ? { jobId, state: "cancelled" } : null });
  if (remove && jobId) {
    try {
      await deleteIdentityAnalysisJob(jobId);
      if (jobIdFor(getState().identityJob) === jobId) {
        setState({
          identityJob: null,
          identityPayload: null,
          identityLinks: [],
          identityLinksRevision: null,
        });
      }
    } catch (error) {
      setState({ identityError: error });
    }
  }
}
