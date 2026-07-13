import {
  bindLocalWorkflow,
  createProject,
  getPluginWorkspace,
  getProject,
  listProjects,
  setupPluginWorkspace,
} from "./api.js";
import { button, el, metaLabel, pluribusMark, toast } from "./components.js";
import { getState, setState } from "./store.js";
import { syncCurrentRightsManifest } from "./sync-manifest.js";

let loadingPromise = null;

function workspaceFromPayload(payload) {
  if (payload?.workspace === null) return null;
  return payload?.workspace || (payload?.id ? payload : null);
}

export async function loadProductContext() {
  if (loadingPromise) return loadingPromise;
  loadingPromise = (async () => {
    setState({ projectLoading: true });
    try {
      const workspacePayload = await getPluginWorkspace();
      const workspace = workspaceFromPayload(workspacePayload);
      if (!workspace) {
        setState({
          workspace: null,
          workspaceReady: true,
          projects: [],
          activeProjectId: null,
          projectContext: null,
          projectLoading: false,
        });
        return null;
      }

      const projectsPayload = await listProjects();
      const projects = projectsPayload.projects || [];
      const current = getState();
      const preferred =
        current.workflowBinding?.projectId || current.activeProjectId || projects[0]?.id || null;
      setState({ workspace, workspaceReady: true, projects, activeProjectId: preferred });
      if (preferred) await loadProjectContext(preferred);
      else setState({ projectContext: null });
      return workspace;
    } catch (error) {
      setState({ projectLoading: false });
      throw error;
    } finally {
      setState({ projectLoading: false });
      loadingPromise = null;
    }
  })();
  return loadingPromise;
}

export async function loadProjectContext(projectId = getState().activeProjectId) {
  if (!projectId) {
    setState({ activeProjectId: null, projectContext: null });
    return null;
  }
  setState({ projectLoading: true, activeProjectId: projectId });
  try {
    const payload = await getProject(projectId, getState().workflowBinding?.workflowRef || "");
    const context = payload.project || payload.projectContext || payload;
    setState({ projectContext: context });
    return context;
  } finally {
    setState({ projectLoading: false });
  }
}

export async function selectProject(projectId, workflowKind = "production") {
  const binding = getState().workflowBinding;
  if (binding?.workflowRef) {
    setState({ manifestSynced: false });
    const updated = await bindLocalWorkflow(binding.workflowRef, projectId, workflowKind);
    setState({ workflowBinding: updated });
  }
  const context = await loadProjectContext(projectId);
  if (getState().scan && getState().workflowBinding?.workflowRef) {
    await syncCurrentRightsManifest();
    return getState().projectContext;
  }
  return context;
}

export function openWorkspaceSetupDialog(onDone) {
  const organization = el("input", {
    class: "plb-input",
    placeholder: "Your studio or company",
    maxlength: "120",
  });
  const type = el(
    "select",
    { class: "plb-input" },
    option("individual", "Individual producer")
  );
  openFormDialog({
    title: "Set up your workspace",
    subtitle: "One canonical home for projects, people, requests, and decisions",
    fields: [field("Workspace name", organization), field("Workspace type", type)],
    note:
      "Self-serve setup creates a personal production workspace. Team workspaces are joined by invitation. Pairing alone does not create projects or upload anything from your graph.",
    actionLabel: "Create workspace",
    async submit(close, action) {
      if (!organization.value.trim()) {
        organization.reportValidity();
        return;
      }
      action.disabled = true;
      try {
        await setupPluginWorkspace({
          organizationName: organization.value.trim(),
          licenseeType: type.value,
        });
        await loadProductContext();
        close();
        toast("Workspace ready.");
        await onDone?.();
      } catch (error) {
        toast(error.message || "Workspace setup failed.");
        action.disabled = false;
      }
    },
  });
  organization.required = true;
  organization.focus();
}

export function openProjectDialog(onDone) {
  const title = el("input", { class: "plb-input", placeholder: "Morning Miles", maxlength: "160" });
  const clientName = el("input", { class: "plb-input", placeholder: "Allbirds", maxlength: "160" });
  const agencyName = el("input", { class: "plb-input", placeholder: "Optional", maxlength: "160" });
  const description = el("textarea", {
    class: "plb-textarea",
    placeholder: "What are you producing?",
    maxlength: "3000",
  });
  openFormDialog({
    title: "Create a project",
    subtitle: "Character sheets, storyboards, production graphs, and final review stay together",
    fields: [
      field("Project name", title),
      field("Brand / client", clientName),
      field("Agency (optional)", agencyName),
      field("Project context", description),
    ],
    note: "The current graph is associated only after you choose Create project.",
    actionLabel: "Create project",
    async submit(close, action) {
      if (!title.value.trim() || !clientName.value.trim()) {
        toast("Add a project name and real brand or client.");
        return;
      }
      action.disabled = true;
      try {
        const result = await createProject({
          title: title.value.trim(),
          clientName: clientName.value.trim(),
          agencyName: agencyName.value.trim() || undefined,
          description: description.value.trim() || undefined,
        });
        const project = result.project || result;
        const projectsPayload = await listProjects();
        setState({ projects: projectsPayload.projects || [], activeProjectId: project.id });
        await selectProject(project.id);
        close();
        toast(`Project created: ${project.title || title.value.trim()}.`);
        await onDone?.(project);
      } catch (error) {
        toast(error.message || "Project creation failed.");
        action.disabled = false;
      }
    },
  });
  title.focus();
}

function openFormDialog({ title, subtitle, fields, note, actionLabel, submit }) {
  const overlay = el("div", { class: "plb-overlay plb-root" });
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  const action = button(actionLabel, "primary", () => submit(close, action));
  const dialog = el(
    "div",
    { class: "plb-dialog plb-connect-dialog" },
    el(
      "div",
      { class: "plb-dialog-header" },
      el(
        "div",
        {},
        el("div", { class: "plb-dialog-title" }, pluribusMark(13), el("span", { text: title })),
        el("div", { class: "plb-dialog-sub", text: subtitle })
      ),
      el("button", { class: "plb-x", type: "button", text: "×", onclick: close })
    ),
    el(
      "div",
      { class: "plb-dialog-right" },
      fields,
      el("p", { class: "plb-dialog-note", text: note }),
      el("div", { class: "plb-dialog-actions" }, action, button("Cancel", "secondary", close))
    )
  );
  overlay.append(dialog);
  document.body.append(overlay);
}

function field(label, control) {
  return el("label", { class: "plb-field" }, metaLabel(label), control);
}

function option(value, label) {
  return el("option", { value, text: label });
}
