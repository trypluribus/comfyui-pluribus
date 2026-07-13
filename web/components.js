// Pure DOM builders — no state, no fetches. Everything returns elements.

export function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value; // trusted, static markup only
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  }
  for (const child of children.flat()) {
    if (child == null) continue;
    node.append(child);
  }
  return node;
}

// Three-dot Pluribus glyph (from the logo system — never a face).
export function pluribusMark(size = 14) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("viewBox", "0 0 14 14");
  svg.style.flexShrink = "0";
  svg.innerHTML =
    '<circle cx="3" cy="7" r="1.7"/><circle cx="7" cy="7" r="1.7"/><circle cx="11" cy="7" r="1.7"/>' +
    '<circle cx="7" cy="3" r="1.4" opacity="0.6"/><circle cx="7" cy="11" r="1.4" opacity="0.6"/>';
  svg.setAttribute("fill", "oklch(0.68 0.14 60)");
  return svg;
}

export const STATE_META = {
  linked: { tag: "Linked", status: "Linked to a project person" },
  review_required: { tag: "Review", status: "Source needs a human decision" },
  not_person: { tag: "Not person", status: "Marked as not a real person" },
  needs_review: { tag: "Review", status: "Known source · confirmation needed" },
  restricted: { tag: "Restricted", status: "Restriction on file" },
  synthetic_unverified: {
    tag: "Synthetic",
    status: "No known real-person source detected in graph",
  },
  unidentified: { tag: "Detected", status: "Not linked to a person" },
};

// Canvas-safe hex equivalents of the state colors (reticles draw on 2D canvas).
export const STATE_HEX = {
  needs_review: "#d9a53f",
  restricted: "#c43c3c",
  synthetic_unverified: "#5b9bd0",
  unidentified: "#c43c3c",
};

function nameHue(name) {
  let hash = 0;
  for (const ch of String(name || "?")) hash = (hash * 31 + ch.codePointAt(0)) % 360;
  return hash;
}

function initials(name) {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || "")
    .join("");
}

// Striped placeholder headshot, same aesthetic as the mockup's Headshot.
export function avatar(person) {
  const hue = nameHue(person.name || person.source_key);
  const node = el("div", { class: "plb-avatar", text: initials(person.name) });
  node.style.background =
    `repeating-linear-gradient(135deg, rgba(255,255,255,0.04) 0 8px, transparent 8px 16px), ` +
    `linear-gradient(150deg, oklch(0.34 0.045 ${hue}), oklch(0.24 0.035 ${hue}))`;
  return node;
}

// class_type → friendly operation label. null = not worth a chip.
const OP_LABELS = {
  ReActorFaceSwap: "Face swap",
  IPAdapter: "Face adapter",
  IPAdapterAdvanced: "Face adapter",
  IPAdapterApply: "Face adapter",
  LoraLoader: "Identity LoRA",
  LoraLoaderModelOnly: "Identity LoRA",
  LoadImage: "Reference image",
  GeminiImage2Node: "Reference image edit",
  FluxKontextProImageNode: "Reference image edit",
  KlingImage2VideoNode: "Image to video",
  CLIPTextEncode: "Prompt",
  KSampler: "Sampler",
  KSamplerAdvanced: "Sampler",
  ImageUpscaleWithModel: "Upscale",
  UpscaleModelLoader: null,
  CheckpointLoaderSimple: null,
  VAEDecode: null,
  VAELoader: null,
  EmptyLatentImage: null,
  SaveImage: null,
  SaveVideo: null,
  CreateVideo: null,
  PreviewImage: null,
  PluribusSourceMarker: null,
};

export function labelForClass(classType) {
  if (classType in OP_LABELS) return OP_LABELS[classType];
  return classType.replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function opLabels(provenance) {
  const labels = [];
  for (const classType of provenance || []) {
    const label = labelForClass(classType);
    if (label && !labels.includes(label)) labels.push(label);
  }
  return labels.slice(0, 6);
}

// Preferred source: person.ops (real downstream operations with node ids,
// each chip click focuses its node). Fallback: provenance class list.
export function opsChips(person, onFocusNode) {
  const ops = (person.ops || []).filter((op) => labelForClass(op.class_type));
  if (ops.length) {
    return el(
      "div",
      {},
      el("div", { class: "plb-ops-label", text: "Downstream graph nodes" }),
      el(
        "div",
        { class: "plb-ops" },
        ops.slice(0, 6).map((op) =>
          el(
            "span",
            {
              class: "plb-chip plb-chip--focusable",
              title: "Click to locate node",
              onclick: (event) => {
                event.stopPropagation();
                onFocusNode?.(op.node_id);
              },
            },
            el("span", { text: labelForClass(op.class_type) }),
            el("small", { text: `#${op.node_id}` })
          )
        )
      )
    );
  }
  const labels = opLabels(person.provenance);
  if (!labels.length) return null;
  return el(
    "div",
    {},
    el("div", { class: "plb-ops-label", text: "Downstream graph nodes" }),
    el("div", { class: "plb-ops" }, labels.map((label) => el("span", { class: "plb-chip", text: label })))
  );
}

// Proposed invite terms derived from supported graph operations.
const CLEAR_STATEMENTS = {
  "Face swap": "Use of their face / likeness in this workflow",
  "Face adapter": "Conditioning AI generation on their face",
  "Identity LoRA": "Generation from a model trained on their likeness",
  "Reference image": "Use of their reference image as a generation source",
  Prompt: "AI depiction directed by a text prompt",
};

export function clearStatements(person) {
  if (Array.isArray(person.scope_statements) && person.scope_statements.length) {
    return [...person.scope_statements];
  }
  const statements = [];
  for (const label of opLabels(person.provenance)) {
    const statement = CLEAR_STATEMENTS[label];
    if (statement && !statements.includes(statement)) statements.push(statement);
  }
  if (!statements.length) statements.push("Use of their likeness in this workflow");
  return statements;
}

export function button(label, kind, onclick) {
  return el("button", { class: `plb-btn plb-btn--${kind}`, type: "button", text: label, onclick });
}

export function metaLabel(text, accent = false) {
  return el("div", { class: `plb-meta${accent ? " plb-meta--accent" : ""}`, text });
}

export function toast(message) {
  const node = el("div", { class: "plb-toast", text: message });
  document.body.appendChild(node);
  setTimeout(() => node.remove(), 3200);
}

export function statusLine(person) {
  const meta = STATE_META[person.state] || { status: person.state };
  return el(
    "div",
    { class: "plb-status-line" },
    el("span", { class: "plb-dot" }),
    el("span", { text: meta.status })
  );
}

export function statusAxes(personState, requestState, internalState) {
  return el(
    "div",
    { class: "plb-axis-stack" },
    axis("Person", personState || "Not linked"),
    axis("Request", requestState || "Not ready"),
    axis("Internal", internalState || "Not reviewed")
  );
}

function axis(label, value) {
  return el(
    "div",
    { class: "plb-axis" },
    el("span", { class: "plb-axis-label", text: label }),
    el("span", { class: "plb-axis-value", text: value })
  );
}
