import {
  deleteIdentityAnalysisJob,
  getIdentityCapabilities,
  getIdentityAnalysisJob,
  getIdentityLinks,
  installIdentityModels,
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
