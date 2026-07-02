// On-canvas presence: detection reticles on flagged nodes + click-to-focus.
// Draws via per-node onDrawForeground chaining so we never patch LiteGraph
// globals; clearReticles() restores the original handlers.

import { app } from "../../scripts/app.js";
import { STATE_HEX } from "./components.js";

const decorated = new Set();

function liveNode(person) {
  const id = person.source_node_id || person.output_node_id;
  if (!id || !app.graph) return null;
  return app.graph.getNodeById(Number(id)) || app.graph.getNodeById(id) || null;
}

function drawReticle(ctx, node, color) {
  const titleHeight = window.LiteGraph?.NODE_TITLE_HEIGHT ?? 30;
  const pad = 5;
  const arm = 13;
  const x0 = -pad;
  const y0 = (node.flags?.collapsed ? 0 : -titleHeight) - pad;
  const x1 = (node.flags?.collapsed ? node._collapsed_width || 80 : node.size[0]) + pad;
  const y1 = (node.flags?.collapsed ? 0 : node.size[1]) + pad;

  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.shadowColor = color;
  ctx.shadowBlur = 6;
  const corner = (cx, cy, dx, dy) => {
    ctx.beginPath();
    ctx.moveTo(cx + dx * arm, cy);
    ctx.lineTo(cx, cy);
    ctx.lineTo(cx, cy + dy * arm);
    ctx.stroke();
  };
  corner(x0, y0, 1, 1);
  corner(x1, y0, -1, 1);
  corner(x0, y1, 1, -1);
  corner(x1, y1, -1, -1);
  ctx.restore();
}

export function applyReticles(persons) {
  clearReticles();
  for (const person of persons || []) {
    const node = liveNode(person);
    if (!node || decorated.has(node)) continue;
    const color = STATE_HEX[person.state] || STATE_HEX.unidentified;
    const previous = node.onDrawForeground;
    node.__plbPrevOnDrawForeground = previous;
    node.onDrawForeground = function (ctx, ...rest) {
      previous?.call(this, ctx, ...rest);
      drawReticle(ctx, this, color);
    };
    decorated.add(node);
  }
  app.graph?.setDirtyCanvas(true, true);
}

export function clearReticles() {
  for (const node of decorated) {
    node.onDrawForeground = node.__plbPrevOnDrawForeground;
    delete node.__plbPrevOnDrawForeground;
  }
  decorated.clear();
  app.graph?.setDirtyCanvas(true, true);
}

export function focusPerson(person) {
  return focusNodeById(person.source_node_id || person.output_node_id);
}

export function focusNodeById(id) {
  if (!id || !app.graph) return false;
  const node = app.graph.getNodeById(Number(id)) || app.graph.getNodeById(id);
  if (!node) return false;
  const canvas = app.canvas;
  if (!canvas) return false;
  if (typeof canvas.centerOnNode === "function") canvas.centerOnNode(node);
  if (typeof canvas.selectNodes === "function") {
    canvas.selectNodes([node]);
  } else if (typeof canvas.selectNode === "function") {
    canvas.selectNode(node);
  }
  canvas.setDirty?.(true, true);
  return true;
}

// Insert a Cleared Talent node at drop coordinates (client-space event).
export function addTalentNodeAt(talentName, clientEvent) {
  const LG = window.LiteGraph;
  if (!LG || !app.graph) return false;
  const node = LG.createNode("PluribusClearedTalent");
  if (!node) return false;
  const canvas = app.canvas;
  let pos = [80, 80];
  if (canvas?.convertEventToCanvasOffset && clientEvent) {
    const offset = canvas.convertEventToCanvasOffset(clientEvent);
    pos = [offset[0] ?? offset.x ?? 80, offset[1] ?? offset.y ?? 80];
  }
  node.pos = pos;
  app.graph.add(node);
  const widget = (node.widgets || []).find((w) => w.name === "talent");
  if (widget) {
    widget.value = talentName;
    widget.callback?.(talentName);
  }
  canvas?.selectNodes?.([node]);
  app.graph.setDirtyCanvas(true, true);
  return true;
}

// Replace a lora/image widget value on the live graph after a server-side
// replacement, so the open workflow matches what the backend returned.
export function applyReplacementToGraph(sourceKey, target) {
  const graph = app.graph;
  const nodes = graph?._nodes;
  if (!nodes) return false;
  let changed = false;
  nodes.forEach((node) => {
    (node.widgets || []).forEach((widget) => {
      if ((widget.name === "lora_name" || widget.name === "image") && widget.value === sourceKey) {
        widget.value = target;
        changed = true;
      }
    });
  });
  if (changed) graph.setDirtyCanvas(true, true);
  return changed;
}

export async function snapshotWorkflow() {
  if (!app?.graphToPrompt) {
    throw new Error("ComfyUI graph API is not ready yet.");
  }
  const { output } = await app.graphToPrompt();
  return output;
}

export function workflowName() {
  return (
    app.extensionManager?.workflow?.activeWorkflow?.filename ||
    app.workflowManager?.activeWorkflow?.name ||
    "current graph"
  );
}
