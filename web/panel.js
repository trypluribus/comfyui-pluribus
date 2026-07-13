// Canonical Pluribus project workflow for ComfyUI.
// Detection stays local. Connected actions send only opaque source references,
// normalized operation classes, project/person IDs, and the intended-use form.

import {
  resolveLocalSource,
  resolveLocalWorkflow,
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
import { openConnectDialog, refreshConnection } from "./connect.js";
import { linkedPeopleForSource, openLinkPersonDialog, setSourceDisposition } from "./link-person.js";
import { personLocalKey } from "./manifest.js";
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

const expanded = new Set();
let root = null;
let mountedContainer = null;
let unsubscribePanel = null;
let activeTab = "sources";
let contextRequested = false;

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
  unsubscribePanel = subscribe((state) => {
    render(state);
    maybeLoadConnectedContext(state);
  });
  render(getState());
  if (!getState().scan && !getState().scanning) void scan();
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
    .catch((error) => toast(error.message || "Could not load your Pluribus workspace."))
    .finally(() => {
      contextRequested = false;
    });
}

export async function scan() {
  const scanEpoch = getState().scanEpoch;
  setState({ scanning: true, error: null });
  try {
    const workflow = await snapshotWorkflow();
    const name = workflowName();
    const graphHash = await workflowFingerprint(workflow);
    const [scanResult, workflowBinding] = await Promise.all([
      scanWorkflow(workflow, name, graphHash),
      resolveLocalWorkflow(localWorkflowKey(), graphHash),
    ]);
    if (getState().scanEpoch !== scanEpoch) return;

    const sourceEntries = await Promise.all(
      (scanResult.persons || []).map(async (person) => {
        const source = await resolveLocalSource(
          workflowBinding.workflowRef,
          person.source_key || `${person.source_kind}:${person.source_node_id || person.output_node_id}`,
          person.source_kind || "unknown"
        );
        return [personLocalKey(person), source.sourceRef];
      })
    );
    setState({
      scan: scanResult,
      workflow,
      workflowBinding,
      sourceRefs: Object.fromEntries(sourceEntries),
      manifestSynced: false,
      scanning: false,
      scannedAt: new Date(),
    });
    applyReticles(scanResult.persons || []);

    if (workflowBinding.projectId && getState().connection?.state === "connected") {
      await loadProductContext();
      if (getState().activeProjectId !== workflowBinding.projectId) {
        await selectProject(workflowBinding.projectId, workflowBinding.workflowKind || "production");
      } else {
        await syncCurrentRightsManifest();
      }
    }
  } catch (error) {
    if (getState().scanEpoch !== scanEpoch) return;
    console.error("[Pluribus] scan failed", error);
    setState({ scanning: false, error });
  }
}

function render(state) {
  if (!root) return;
  const content = el("div", { class: "plb-tab-content" });
  if (activeTab === "people") renderPeople(content);
  else if (activeTab === "use") renderUseBrief(content);
  else content.replaceChildren(sourcesBody(state));
  root.replaceChildren(header(state), tabs(), projectBand(state), content, footer(state));
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
      el("span", { class: "plb-header-sub", text: "People & use" })
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
        activeTab = id;
        render(getState());
      },
    });
  return el(
    "div",
    { class: "plb-tabs" },
    tab("sources", "Sources"),
    tab("people", "People"),
    tab("use", "Intended use")
  );
}

function projectBand(state) {
  if (state.connection?.state !== "connected") {
    return el(
      "div",
      { class: "plb-project-band" },
      el("span", { text: "Scan stays local" }),
      el("small", { text: "Connect to create projects, link people, and request confirmation." })
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
          ? "Finding person-bearing sources in the current graph…"
          : "Find marked references, identity models, and other person-bearing inputs. This scan does not inspect rendered pixels.",
        state.scanning ? null : button("Find people", "primary", scan)
      )
    );
  }
  const wrap = el("div", { class: "plb-sources" }, sourceSummary(state));
  const persons = state.scan.persons || [];
  const issues = state.scan.issues || [];
  if (issues.length) {
    const issueNodes = issues
      .map((issue) => issue.node_id)
      .filter(Boolean)
      .map((nodeId) => `#${nodeId}`)
      .join(", ");
    wrap.append(
      el(
        "div",
        { class: "plb-warnstrip" },
        el("span", { class: "plb-warnmark", text: "!" }),
        el("span", {
          text: `${issues.length} incomplete Pluribus ${issues.length === 1 ? "marker was" : "markers were"} ignored${issueNodes ? ` (${issueNodes})` : ""}. Add a source key, or describe a prompt-only source, then find people again.`,
        })
      )
    );
  }
  if (!persons.length) {
    wrap.append(
      empty(
        "No supported person-bearing source was derived from this graph. This scan does not inspect rendered pixels. Add a Pluribus Source Marker when detection needs a hand."
      )
    );
  } else {
    wrap.append(el("div", { class: "plb-list" }, persons.map((person) => sourceCard(person, state))));
  }
  return wrap;
}

function sourceSummary(state) {
  const persons = state.scan.persons || [];
  const linked = persons.filter((person) => linkedPeopleForSource(person).length > 0).length;
  const handled = persons.filter((person) => sourceDisposition(person) === "not_person").length;
  const remaining = Math.max(0, persons.length - linked - handled);
  return el(
    "div",
    { class: "plb-summary" },
    el(
      "div",
      { class: "plb-summary-title" },
      metaLabel("Current graph · local detection", true),
      el("span", { class: "plb-meta plb-meta--dim", text: stamp(state.scannedAt) })
    ),
    el(
      "div",
      { class: "plb-tiles" },
      tile(persons.length, "detected", "var(--plb-ink)"),
      tile(linked, "linked", "var(--plb-ok)"),
      tile(remaining, "need action", remaining ? "var(--plb-warn)" : "var(--plb-ok)")
    ),
    remaining
      ? el(
          "div",
          { class: "plb-warnstrip" },
          el("span", { class: "plb-warnmark", text: "!" }),
          el("span", {
            text: `${remaining} ${remaining === 1 ? "source needs" : "sources need"} a person link or an explicit not-a-person decision.`,
          })
        )
      : null
  );
}

function sourceCard(person, state) {
  const key = personLocalKey(person);
  const linked = linkedPeopleForSource(person);
  const disposition = sourceDisposition(person);
  const personState = linked.length
    ? linked.map((candidate) => candidate.displayName || candidate.name).join(", ")
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
  const top = el(
    "div",
    {
      class: "plb-card-top",
      title: "Locate this source in the graph",
      onclick: () => {
        if (!focusPerson(person)) toast("Node not found in the open graph.");
      },
    },
    avatar({ ...person, name: linked[0]?.displayName || linked[0]?.name || "?" }),
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
            text: linked.length ? personState : person.name || "Detected person source",
          }),
          el("div", { class: "plb-card-src", text: localSourceLabel(person) })
        ),
        el("span", { class: "plb-kind-tag", text: dispositionLabel(disposition, linked.length) })
      )
    )
  );
  const actions = el("div", { class: "plb-actions" });
  const link = button(linked.length ? "Edit people" : "Link to person", "primary", async () => {
    if (!(await ensureCurrentPerson(person))) return;
    openLinkPersonDialog(person);
  });
  link.disabled = !state.activeProjectId;
  actions.append(link);
  if (!linked.length) {
    actions.append(
      button("Not a person", "secondary", () => setSourceDisposition(person, "not_person")),
      button("Review", "ghost", () => setSourceDisposition(person, "review_required"))
    );
  }
  const details = button(expanded.has(key) ? "Hide" : "Details", "secondary", () => {
    if (expanded.has(key)) expanded.delete(key);
    else expanded.add(key);
    render(getState());
  });
  actions.append(details);
  const card = el(
    "section",
    { class: `plb-card ${linked.length ? "linked" : "unidentified"}` },
    top,
    statusAxes(personState, requestState, internalState),
    opsChips(person, (nodeId) => {
      if (!focusNodeById(nodeId)) toast("Node not found in the open graph.");
    }),
    actions
  );
  if (expanded.has(key)) {
    card.append(
      el(
        "div",
        { class: "plb-details" },
        detailRow("Local node", `#${person.source_node_id || person.output_node_id}`),
        detailRow("Source type", person.source_kind || "unknown"),
        detailRow("Detected path", (person.provenance || []).join(" → ") || "Marker only"),
        el("p", {
          class: "plb-note",
          text:
            "The source path, graph, prompts, and media stay on this machine. Linking sends only an opaque source reference and normalized rights-relevant operation classes.",
        })
      )
    );
  }
  return card;
}

function footer(state) {
  const project = activeProject();
  return el(
    "div",
    { class: "plb-footer" },
    el("span", {
      class: "plb-footer-context",
      text: project ? project.title : state.connection?.state === "connected" ? "No project selected" : "Local scan only",
    }),
    button(state.scanning ? "Finding…" : "Find people", "secondary", scan)
  );
}

function sourceDisposition(person) {
  const sourceRef = getState().sourceRefs[personLocalKey(person)];
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

function localSourceLabel(person) {
  const node = person.source_node_id || person.output_node_id;
  return `${person.source_kind || "source"}${node ? ` · local node #${node}` : ""}`;
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

async function ensureCurrentPerson(person) {
  try {
    if (await personMatchesCurrentWorkflow(person)) return true;
  } catch (error) {
    console.warn("[Pluribus] could not verify workflow context", error);
  }
  toast("The graph changed after this scan. Find people again before taking action.");
  return false;
}

function stamp(date) {
  if (!date) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
