// CS4 — the Rights scan panel. Renders into whatever container it's given
// (native sidebar tab or the floating fallback) from store state.

import { recordAction, replaceSource, scanWorkflow, syncInvites } from "./api.js";
import {
  avatar,
  button,
  el,
  metaLabel,
  opsChips,
  pluribusMark,
  statusLine,
  STATE_META,
  toast,
} from "./components.js";
import {
  applyReplacementToGraph,
  applyReticles,
  focusNodeById,
  focusPerson,
  personMatchesCurrentWorkflow,
  snapshotWorkflow,
  workflowName,
} from "./canvas.js";
import { workflowFingerprint } from "./fingerprint.js";
import { openConnectDialog, refreshConnection } from "./connect.js";
import { openInviteDialog } from "./invite.js";
import { renderRosterTab } from "./roster.js";
import {
  getState,
  invitablePersons,
  needsActionCount,
  setState,
  subscribe,
  wasInvited,
} from "./store.js";

const expanded = new Set(); // person identity → details open, survives re-renders
let root = null;
let activeTab = "scan"; // "scan" | "roster"

export function mountPanel(container) {
  root = el("div", { class: "plb-root" });
  container.appendChild(root);
  subscribe(render);
  render(getState());
  if (!getState().scan && !getState().scanning) scan();
  void refreshConnection({
    onInviteSync: async () => {
      await scan();
    },
  });
  return root;
}

export async function scan({ syncFirst = false } = {}) {
  const scanEpoch = getState().scanEpoch;
  setState({ scanning: true, error: null });
  try {
    if (syncFirst) {
      // Manual rescans always wait for the local sync route. Disconnected
      // calls return immediately; connected calls fetch server status first.
      try {
        await syncInvites();
      } catch (error) {
        console.warn("[Pluribus] invite sync failed before rescan", error);
      }
    }
    const workflow = await snapshotWorkflow();
    const name = workflowName();
    const fingerprint = await workflowFingerprint(workflow);
    const scanResult = await scanWorkflow(workflow, name, fingerprint);
    if (getState().scanEpoch !== scanEpoch) return;
    setState({
      scan: scanResult,
      workflow,
      scanning: false,
      scannedAt: new Date(),
    });
    applyReticles(scanResult.persons);
  } catch (error) {
    if (getState().scanEpoch !== scanEpoch) return;
    console.error("[Pluribus] scan failed", error);
    setState({ scanning: false, error });
  }
}

function personKey(person) {
  return `${person.output_node_id}|${person.source_kind}|${person.source_key}`;
}

function render(state) {
  if (!root) return;
  if (activeTab === "roster") {
    const rosterBody = el("div", {
      style: "display:flex;flex-direction:column;flex:1;min-height:0;overflow-y:auto",
    });
    root.replaceChildren(header(state), tabs(), rosterBody);
    renderRosterTab(rosterBody);
    return;
  }
  root.replaceChildren(header(state), tabs(), body(state), footer(state));
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
  return el("div", { class: "plb-tabs" }, tab("scan", "Rights scan"), tab("roster", "Roster"));
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
      el("span", { class: "plb-header-sub", text: "Talent layer" })
    ),
    connectionChip(state)
  );
}

function connectionChip(state) {
  if (state.scanning) {
    return el(
      "span",
      { class: "plb-linked" },
      el("span", { class: "plb-dot" }),
      el("span", { text: "scanning" })
    );
  }
  const connection = state.connection;
  if (connection?.state === "connected") {
    return el(
      "button",
      {
        class: "plb-linked plb-linked--btn",
        type: "button",
        title: "Connected to Pluribus — click to manage",
        onclick: () => openConnectDialog(connection),
      },
      el("span", { class: "plb-dot" }),
      el("span", { class: "plb-linked-email", text: connection.account_email || "connected" })
    );
  }
  return el(
    "button",
    {
      class: "plb-connect-cta",
      type: "button",
      title: "Link this ComfyUI to your Pluribus account",
      text: connection?.state === "pairing" ? "Pairing…" : "Connect",
      onclick: () => openConnectDialog(connection),
    }
  );
}

function body(state) {
  if (state.error) {
    return el(
      "div",
      { class: "plb-list" },
      el(
        "div",
        { class: "plb-empty" },
        pluribusMark(20),
        el("div", { text: state.error.message || String(state.error) }),
        button("Try again", "secondary", scan)
      )
    );
  }
  if (!state.scan) {
    return el(
      "div",
      { class: "plb-list" },
      el(
        "div",
        { class: "plb-empty" },
        pluribusMark(22),
        metaLabel("Talent layer", true),
        el("div", {
          text: state.scanning
            ? "Scanning the current workflow…"
            : "Scan graph provenance and roster records for person-bearing sources and the operations that alter them.",
        }),
        state.scanning ? null : button("Scan workflow", "primary", scan)
      )
    );
  }
  if (state.scan.workflow_name !== workflowName()) {
    return el(
      "div",
      { class: "plb-list" },
      el(
        "div",
        { class: "plb-empty" },
        pluribusMark(20),
        el("div", { text: "The open workflow changed. Rescan before using these results." }),
        button("Rescan", "primary", () => scan({ syncFirst: true }))
      )
    );
  }

  const wrap = el("div", { style: "display:flex;flex-direction:column;flex:1;min-height:0" });
  wrap.append(summary(state), cardList(state));
  return wrap;
}

function summary(state) {
  const persons = state.scan.persons;
  const needAction = needsActionCount();
  const synthetic = persons.filter((p) => p.state === "synthetic_unverified").length;

  const tiles = el(
    "div",
    { class: "plb-tiles" },
    tile(persons.length, persons.length === 1 ? "source" : "sources", "var(--plb-ink)"),
    tile(needAction, "need action", needAction ? "var(--plb-bad)" : "var(--plb-ok)"),
    tile(synthetic, "synthetic", "var(--plb-synth)")
  );

  const block = el(
    "div",
    { class: "plb-summary" },
    el(
      "div",
      { class: "plb-summary-title" },
      metaLabel(`Scan of ${state.scan.workflow_name || "current graph"}`, true),
      el("span", { class: "plb-meta plb-meta--dim", text: stamp(state.scannedAt) })
    ),
    tiles
  );

  if (needAction > 0) {
    block.append(
      el(
        "div",
        { class: "plb-warnstrip" },
        el("span", { class: "plb-warnmark", text: "⚠" }),
        el("span", {
          text:
            `${needAction} ${needAction === 1 ? "source still needs" : "sources still need"} review. ` +
            `Check accepted terms, restrictions, and unidentified sources before relying on this scan.`,
        })
      )
    );
  }
  return block;
}

function tile(value, label, color) {
  const number = el("div", { class: "plb-tile-n", text: String(value) });
  number.style.color = color;
  return el("div", { class: "plb-tile" }, number, el("div", { class: "plb-tile-l", text: label }));
}

function cardList(state) {
  const persons = state.scan.persons;
  if (!persons.length) {
    return el(
      "div",
      { class: "plb-list" },
      el(
        "div",
        { class: "plb-empty" },
        pluribusMark(20),
        el("div", {
          text:
            "No supported person-bearing source was derived from the current graph. " +
            "This scan does not inspect rendered pixels.",
        })
      )
    );
  }
  return el("div", { class: "plb-list" }, persons.map((person) => card(person, state)));
}

function card(person, state) {
  const meta = STATE_META[person.state] || { tag: person.state, status: person.state };
  const key = personKey(person);

  const top = el(
    "div",
    {
      class: "plb-card-top",
      title: "Click to locate in graph",
      onclick: () => {
        if (!focusPerson(person)) toast("Node not found in the open graph.");
      },
    },
    avatar(person),
    el(
      "div",
      { class: "plb-card-id" },
      el(
        "div",
        { class: "plb-card-name-row" },
        el(
          "div",
          {},
          el("div", { class: "plb-card-name", text: person.name || "Unidentified source" }),
          el("div", { class: "plb-card-src", text: srcLine(person) })
        ),
        el("span", { class: "plb-kind-tag", text: meta.tag })
      ),
      statusLine(person)
    )
  );

  const actions = el("div", { class: "plb-actions" });
  if (person.available_actions.includes("invite")) {
    const alreadyInvited = wasInvited(person);
    const invite = button(alreadyInvited ? "Invite sent" : "Invite for terms", "primary", () =>
      openInviteDialog([person])
    );
    invite.disabled = alreadyInvited;
    actions.append(invite);
  }
  const detailsBtn = button(expanded.has(key) ? "Hide details" : "Details", "secondary", () => {
    if (expanded.has(key)) expanded.delete(key);
    else expanded.add(key);
    render(getState());
  });
  actions.append(detailsBtn);

  const node = el(
    "section",
    { class: `plb-card ${person.state}` },
    top,
    opsChips(person, (nodeId) => {
      if (!focusNodeById(nodeId)) toast("Node not found in the open graph.");
    }),
    actions
  );
  if (expanded.has(key)) node.append(details(person, state));
  return node;
}

function srcLine(person) {
  const kind = person.source_kind || "";
  return person.source_key ? `${kind} · ${person.source_key}` : kind;
}

function details(person, state) {
  const rows = [
    ...(person.terms_status === "accepted"
      ? [["Terms", person.terms_accepted_at ? `Accepted ${person.terms_accepted_at}` : "Accepted"]]
      : []),
    ["Scope", person.scope || "Not on file"],
    ["Union", person.union_status || "Not on file"],
    ["Rep", person.rep || "Not on file"],
    ["Node", `#${person.source_node_id || person.output_node_id}`],
    ["Path", (person.provenance || []).join(" → ")],
  ];
  const scope = rows.map(([k, v]) =>
    el("div", { class: "plb-scope-row" }, el("dt", { text: k }), el("dd", { text: v }))
  );

  const wrap = el("div", { class: "plb-details" }, scope);

  if (person.allowed_uses.length || person.prohibited_uses.length) {
    wrap.append(
      el(
        "div",
        { class: "plb-uses" },
        el(
          "div",
          { class: "allowed" },
          el("h5", { text: "Allowed" }),
          el("ul", {}, person.allowed_uses.map((use) => el("li", { text: use })))
        ),
        el(
          "div",
          { class: "prohibited" },
          el("h5", { text: "Prohibited" }),
          el("ul", {}, person.prohibited_uses.map((use) => el("li", { text: use })))
        )
      )
    );
  }
  for (const conflict of person.conflicts || []) {
    wrap.append(el("p", { class: "plb-conflict", text: conflict }));
  }
  if (person.note) wrap.append(el("p", { class: "plb-note", text: person.note }));

  const extra = el("div", { class: "plb-actions" });
  if (person.available_actions.includes("replace") && person.replacement_asset_key) {
    extra.append(button("Replace with roster source", "secondary", () => replace(person, state)));
  }
  if (person.available_actions.includes("identify")) {
    extra.append(button("Identify source", "secondary", () => act(person, "identify")));
  }
  if (person.available_actions.includes("route")) {
    extra.append(button("Flag for review", "ghost", () => act(person, "route")));
  }
  if (extra.children.length) wrap.append(extra);
  return wrap;
}

async function act(person, kind) {
  if (!(await ensureCurrentPerson(person))) return;
  try {
    const data = await recordAction({
      kind,
      talent_id: person.talent_id,
      name: person.name || "Unknown",
      source_key: person.source_key,
    });
    toast(data.message);
  } catch (error) {
    toast(`Action failed: ${error.message}`);
  }
}

async function replace(person, state) {
  if (!(await ensureCurrentPerson(person))) return;
  const target = person.replacement_asset_key;
  try {
    await replaceSource(state.workflow, person.source_key, target);
    const changed = applyReplacementToGraph(person.source_key, target);
    toast(
      changed
        ? `Replaced ${person.source_key} with ${target}.`
        : "Replacement prepared, but no matching widget found in the open graph."
    );
    scan();
  } catch (error) {
    toast(`Replace failed: ${error.message}`);
  }
}

function footer(state) {
  if (state.scan && state.scan.workflow_name !== workflowName()) {
    return el(
      "div",
      { class: "plb-footer" },
      button("Rescan", "primary", () => scan({ syncFirst: true }))
    );
  }
  const invitable = invitablePersons();
  const inviteAll = button(
    invitable.length ? `Invite all for terms (${invitable.length})` : "Invite all for terms",
    "primary",
    () => openInviteDialog(invitable)
  );
  inviteAll.disabled = !invitable.length;
  return el(
    "div",
    { class: "plb-footer" },
    inviteAll,
    button(state.scanning ? "Scanning…" : "Rescan", "secondary", () =>
      scan({ syncFirst: true })
    )
  );
}

async function ensureCurrentPerson(person) {
  try {
    if (await personMatchesCurrentWorkflow(person)) return true;
  } catch (error) {
    console.warn("[Pluribus] could not verify workflow context", error);
  }
  toast("The workflow changed after this scan. Rescan before taking action.");
  return false;
}

function stamp(date) {
  if (!date) return "";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
