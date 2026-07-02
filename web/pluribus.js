// Pluribus for ComfyUI — entry point.
// Registers the Rights scan panel as a native sidebar tab (floating fallback
// for older frontends) and gives the marker node the amber identity.

import { app } from "../../scripts/app.js";
import { mountPanel } from "./panel.js";
import { el } from "./components.js";

const FONTS_HREF =
  "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600;700&display=swap";

// Amber node identity (LiteGraph colors — hex only).
const NODE_COLORS = {
  header: "#3a2a16",
  body: "#15110e",
  box: "#c97a2b",
};

app.registerExtension({
  name: "pluribus.rightsScan",

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
    if (nodeData?.name === "PluribusClearedTalent") {
      decorateTalentNode(nodeType);
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
  if (!document.getElementById("pluribus-fonts")) {
    const fonts = document.createElement("link");
    fonts.id = "pluribus-fonts";
    fonts.rel = "stylesheet";
    fonts.href = FONTS_HREF;
    document.head.appendChild(fonts);
  }
}

function whenReady(callback) {
  if (document.body) callback();
  else window.addEventListener("DOMContentLoaded", callback, { once: true });
}

// ── Cleared Talent node — CS6 "standard" density, drawn with canvas
//    primitives: striped likeness thumb, name, live "twin tracked" footer. ──
const AMBER = "#c97a2b";
const TALENT_MIN_SIZE = [232, 182];

function talentHue(name) {
  let hash = 0;
  for (const ch of String(name || "?")) hash = (hash * 31 + ch.codePointAt(0)) % 360;
  return hash;
}

function decorateTalentNode(nodeType) {
  const onCreated = nodeType.prototype.onNodeCreated;
  nodeType.prototype.onNodeCreated = function (...args) {
    const result = onCreated?.apply(this, args);
    this.color = NODE_COLORS.header;
    this.bgcolor = NODE_COLORS.body;
    this.boxcolor = NODE_COLORS.box;
    if (this.size[0] < TALENT_MIN_SIZE[0]) this.size[0] = TALENT_MIN_SIZE[0];
    if (this.size[1] < TALENT_MIN_SIZE[1]) this.size[1] = TALENT_MIN_SIZE[1];
    return result;
  };

  const onDraw = nodeType.prototype.onDrawForeground;
  nodeType.prototype.onDrawForeground = function (ctx, ...rest) {
    onDraw?.call(this, ctx, ...rest);
    if (this.flags?.collapsed) return;

    const name = (this.widgets || []).find((w) => w.name === "talent")?.value || "Talent";
    const pad = 10;
    const width = this.size[0] - pad * 2;
    // Start below the last laid-out widget (LiteGraph records widget.last_y).
    const lastWidgetY = Math.max(0, ...(this.widgets || []).map((w) => w.last_y || 0));
    const widgetBottom = (lastWidgetY || 40) + 28;
    const footerH = 22;
    const thumbH = Math.max(40, this.size[1] - widgetBottom - footerH - pad);
    const x = pad;
    const y = widgetBottom;

    // amber node outline (LiteGraph draws its own border; we add the glow edge)
    ctx.save();
    ctx.strokeStyle = AMBER;
    ctx.lineWidth = 1;
    ctx.globalAlpha = 0.9;
    ctx.strokeRect(0.5, 0.5, this.size[0] - 1, this.size[1] - 1);
    ctx.globalAlpha = 1;

    // striped likeness thumb
    const hue = talentHue(name);
    const gradient = ctx.createLinearGradient(x, y, x, y + thumbH);
    gradient.addColorStop(0, `hsl(${hue} 22% 30%)`);
    gradient.addColorStop(1, `hsl(${(hue + 40) % 360} 20% 18%)`);
    ctx.fillStyle = gradient;
    ctx.fillRect(x, y, width, thumbH);
    ctx.strokeStyle = "rgba(255,255,255,0.10)";
    ctx.lineWidth = 5;
    ctx.beginPath();
    for (let sx = -thumbH; sx < width; sx += 18) {
      ctx.moveTo(x + sx, y + thumbH);
      ctx.lineTo(x + sx + thumbH, y);
    }
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, width, thumbH);
    ctx.clip();
    ctx.globalAlpha = 0.35;
    ctx.stroke();
    ctx.restore();

    // name over the thumb
    ctx.fillStyle = "rgba(0,0,0,0.45)";
    ctx.fillRect(x, y + thumbH - 18, width, 18);
    ctx.fillStyle = "#fff";
    ctx.font = "700 11px 'Space Grotesk', 'Inter', sans-serif";
    ctx.textBaseline = "middle";
    ctx.fillText(name, x + 6, y + thumbH - 9, width - 12);

    // footer — twin tracked · consent live
    const fy = y + thumbH + footerH / 2 + 2;
    ctx.strokeStyle = "rgba(255,255,255,0.08)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, y + thumbH + 4);
    ctx.lineTo(x + width, y + thumbH + 4);
    ctx.stroke();
    ctx.fillStyle = AMBER;
    ctx.beginPath();
    ctx.arc(x + 3, fy, 2.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.font = "600 8px 'Inter', sans-serif";
    ctx.fillText("TWIN TRACKED · CONSENT LIVE", x + 10, fy, width - 12);
    ctx.restore();
  };
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
