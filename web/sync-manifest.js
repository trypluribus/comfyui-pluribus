import {
  getProject,
  resolveLocalWorkflow,
  saveProjectSourceLinks,
} from "./api.js";
import { localWorkflowKey } from "./canvas.js";
import { manifestSourcesForScan } from "./manifest.js";
import {
  getState,
  projectSourceLinks,
  setState,
} from "./store.js";

// Persist the full current rights manifest after every rescan and workflow-kind
// change. The ComfyUI graph remains local: only opaque refs, dispositions,
// canonical person IDs, and normalized operation classes cross the boundary.
export async function syncCurrentRightsManifest(overrides = new Map()) {
  const state = getState();
  const projectId = state.activeProjectId;
  const workflowRef = state.workflowBinding?.workflowRef;
  if (!projectId || !workflowRef || !state.scan) {
    throw new Error("Find people and choose a project before syncing this workflow.");
  }

  setState({ manifestSynced: false });
  const sources = manifestSourcesForScan(
    state.scan.persons || [],
    state.sourceRefs,
    projectSourceLinks(),
    overrides
  );
  await saveProjectSourceLinks(projectId, {
    workflowRef,
    workflowKind: state.workflowBinding.workflowKind || "production",
    graphHash: state.scan.workflow_fingerprint || undefined,
    sources,
  });

  const workflowBinding = await resolveLocalWorkflow(
    localWorkflowKey(),
    state.scan.workflow_fingerprint || ""
  );
  const payload = await getProject(projectId, workflowRef);
  const projectContext = payload.project || payload.projectContext || payload;
  setState({ workflowBinding, projectContext, manifestSynced: true });
  return projectContext.workflow;
}
