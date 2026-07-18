// Pluribus for ComfyUI — entry point.
// Registers the Rights scan panel as a native sidebar tab (floating fallback
// for older frontends) and gives the marker node the amber identity.

import { app } from "../../scripts/app.js";
import { mountPanel } from "./panel.js";
import { el } from "./components.js";
import { clearReticles } from "./canvas.js";
import { cancelIdentityAnalysis } from "./identity-analysis.js";
import { invalidateScan } from "./store.js";

// Amber node identity (LiteGraph colors — hex only).
const NODE_COLORS = {
  header: "#3a2a16",
  body: "#15110e",
  box: "#c97a2b",
};

app.registerExtension({
  name: "pluribus.rightsScan",

  async afterConfigureGraph() {
    // ComfyUI has loaded/configured a graph. Results from the previous graph
    // must never remain actionable while a new graph is on canvas.
    clearReticles();
    void cancelIdentityAnalysis({ remove: true });
    invalidateScan();
  },

  async setup() {
    addStyles();
    if (app.extensionManager?.registerSidebarTab) {
      app.extensionManager.registerSidebarTab({
        id: "pluribus",
        icon: "pi pi-id-card",
        title: "Pluribus",
        tooltip: "Pluribus · Rights scan",
        type: "custom",
        render: (container) => {
          container.style.height = "100%";
          mountPanel(container);
        },
      });
    } else {
      whenReady(buildFloatingFallback);
    }
  },

  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData?.name === "PluribusSourceMarker") {
      const original = nodeType.prototype.onNodeCreated;
      nodeType.prototype.onNodeCreated = function (...args) {
        const result = original?.apply(this, args);
        this.color = NODE_COLORS.header;
        this.bgcolor = NODE_COLORS.body;
        this.boxcolor = NODE_COLORS.box;
        return result;
      };
    }
  },
});

function addStyles() {
  if (!document.getElementById("pluribus-stylesheet")) {
    const link = document.createElement("link");
    link.id = "pluribus-stylesheet";
    link.rel = "stylesheet";
    link.href = "/extensions/comfyui-pluribus/pluribus.css";
    document.head.appendChild(link);
  }
}

function whenReady(callback) {
  if (document.body) callback();
  else window.addEventListener("DOMContentLoaded", callback, { once: true });
}

// Older frontends without the sidebar API get a launcher + slide-in panel.
function buildFloatingFallback() {
  if (document.getElementById("pluribus-float")) return;

  const float = el("div", { id: "pluribus-float", class: "plb-float" });
  document.body.appendChild(float);
  mountPanel(float);

  const launcher = el("button", {
    class: "plb-launcher",
    type: "button",
    text: "Pluribus",
    onclick: () => float.classList.toggle("open"),
  });
  document.body.appendChild(launcher);
}
