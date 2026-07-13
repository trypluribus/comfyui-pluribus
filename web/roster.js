// CS3 — docked roster. Eligible entries are draggable onto the graph canvas,
// where they land as a Pluribus · Talent Record node. Entries that still need
// scope or review are listed dimmed.

import { app } from "../../scripts/app.js";
import { getRoster } from "./api.js";
import { avatar, button, el, metaLabel, pluribusMark, toast } from "./components.js";
import { addTalentNodeAt } from "./canvas.js";

const DRAG_MIME = "application/x-pluribus-talent";

let cachedRoster = null;
let dropWired = false;

const CONSENT_TAG = {
  cleared: { label: "Scope on file · review use", cls: "cleared" },
  pending: { label: "Scope or review needed", cls: "needs_review" },
  restricted: { label: "Restricted", cls: "restricted" },
};

export function renderRosterTab(container) {
  container.replaceChildren(loadingState());
  wireCanvasDrop();
  loadRoster()
    .then((talent) => container.replaceChildren(rosterList(talent)))
    .catch((error) => {
      console.error("[Pluribus] roster load failed", error);
      container.replaceChildren(
        el(
          "div",
          { class: "plb-empty" },
          pluribusMark(20),
          el("div", { text: error.message || String(error) }),
          button("Try again", "secondary", () => renderRosterTab(container))
        )
      );
    });
}

async function loadRoster() {
  if (!cachedRoster) {
    const data = await getRoster();
    cachedRoster = data.talent || [];
  }
  return cachedRoster;
}

function loadingState() {
  return el(
    "div",
    { class: "plb-empty" },
    pluribusMark(20),
    el("div", { text: "Loading roster…" })
  );
}

function rosterList(talent) {
  const cleared = talent.filter((t) => t.clearance_status === "cleared");
  const rest = talent.filter((t) => t.clearance_status !== "cleared");

  const wrap = el("div", { class: "plb-roster" });
  wrap.append(
    el(
      "div",
      { class: "plb-roster-hint" },
      metaLabel(`${cleared.length} with scope on file · drag a roster entry into the graph`, true)
    )
  );
  const list = el("div", { class: "plb-list", style: "padding-top: 4px" });
  cleared.forEach((t) => list.append(rosterRow(t, true)));
  if (rest.length) {
    list.append(
      el("div", {
        class: "plb-ops-label",
        style: "margin: 8px 2px 0",
        text: "Scope or review needed",
      })
    );
    rest.forEach((t) => list.append(rosterRow(t, false)));
  }
  wrap.append(list);
  return wrap;
}

function rosterRow(talent, draggable) {
  const tag = CONSENT_TAG[talent.clearance_status] || CONSENT_TAG.pending;
  const row = el(
    "div",
    { class: `plb-roster-row${draggable ? " draggable" : ""}` },
    el("span", { class: "plb-roster-grip", text: "⠿" }),
    avatar({ name: talent.name, source_key: talent.asset_keys?.[0] }),
    el(
      "div",
      { class: "plb-card-id" },
      el("div", { class: "plb-card-name", text: talent.name }),
      el("div", {
        class: "plb-card-src",
        text: [talent.union_status, talent.rep].filter(Boolean).join(" · ") || "—",
      }),
      el(
        "div",
        { class: `plb-status-line plb-consent-${tag.cls}` },
        el("span", { class: "plb-dot" }),
        el("span", { text: talent.synthetic_only ? "Synthetic · licensed" : tag.label })
      )
    )
  );

  if (draggable) {
    row.draggable = true;
    row.title = "Drag onto the canvas to add as a Talent Record node";
    row.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData(DRAG_MIME, talent.name);
      event.dataTransfer.effectAllowed = "copy";
      row.classList.add("dragging");
    });
    row.addEventListener("dragend", () => row.classList.remove("dragging"));
    row.addEventListener("dblclick", () => {
      if (addTalentNodeAt(talent.name, null)) {
        toast(`${talent.name} added to the graph as a Talent Record node.`);
      }
    });
  } else {
    row.title = "Scope or review needed — invite them from a rights scan first";
  }
  return row;
}

function wireCanvasDrop() {
  if (dropWired) return;
  const canvasEl = app.canvasEl || document.querySelector("#graph-canvas, canvas.litegraph");
  if (!canvasEl) return;
  dropWired = true;

  canvasEl.addEventListener("dragover", (event) => {
    if ([...event.dataTransfer.types].includes(DRAG_MIME)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  });
  canvasEl.addEventListener("drop", (event) => {
    const name = event.dataTransfer.getData(DRAG_MIME);
    if (!name) return;
    event.preventDefault();
    event.stopPropagation();
    if (addTalentNodeAt(name, event)) {
      toast(`${name} added — rescan to review the roster record and downstream nodes.`);
    } else {
      toast("Could not add the talent node to the graph.");
    }
  });
}
