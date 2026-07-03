// CS5 — "Invite to Pluribus" dialog. Two columns: what we detected and what
// they'll be asked to clear, then the invite itself. Local record only:
// "Send invite" hits /pluribus/action and surfaces the generated accept link.

import { sendInvite } from "./api.js";
import {
  avatar,
  button,
  clearStatements,
  el,
  metaLabel,
  opsChips,
  pluribusMark,
  statusLine,
  STATE_META,
  toast,
} from "./components.js";
import { markInvited } from "./store.js";
import { workflowName } from "./canvas.js";

// Opens the dialog for persons[0]; after a send, advances through the rest
// (the "Invite all uncleared (n)" path). onDone fires when the queue empties
// or the dialog is dismissed.
export function openInviteDialog(persons, onDone) {
  const queue = [...persons];
  if (!queue.length) return;
  showDialog(queue, 0, onDone);
}

function showDialog(queue, index, onDone) {
  const person = queue[index];
  const meta = STATE_META[person.state] || {};
  let delivery = "email";

  const overlay = el("div", { class: "plb-overlay plb-root" });
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    onDone?.();
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });

  // ── left column — what we detected ──────────────────────────────────
  const detectedCard = el(
    "div",
    { class: `plb-card ${person.state}` },
    el(
      "div",
      { class: "plb-card-top", style: "cursor: default" },
      avatar(person),
      el(
        "div",
        { class: "plb-card-id" },
        el(
          "div",
          { class: "plb-card-name-row" },
          el("div", {}, el("div", { class: "plb-card-name", text: person.name || "Unidentified source" }),
            el("div", { class: "plb-card-src", text: sourceLine(person) })),
          el("span", { class: "plb-kind-tag", text: meta.tag || person.state })
        ),
        statusLine(person)
      )
    ),
    opsChips(person)
  );

  const left = el(
    "div",
    { class: "plb-dialog-left" },
    metaLabel(`Detected in ${workflowName()}`, true),
    detectedCard,
    el(
      "div",
      {},
      el("div", { class: "plb-ops-label", text: "They'll be asked to clear" }),
      el(
        "div",
        { class: "plb-clearlist" },
        clearStatements(person).map((statement) => el("div", { class: "plb-clearitem", text: statement }))
      )
    )
  );

  // ── right column — the invite ────────────────────────────────────────
  const emailInput = el("input", {
    class: "plb-input",
    type: "email",
    placeholder: "name@example.com",
  });
  const noteInput = el("textarea", { class: "plb-textarea" });
  noteInput.value =
    `Hi ${firstName(person)} — we used your likeness in a generative workflow and want to ` +
    `make sure you're credited and compensated. Accept below to set your terms.`;

  const segEmail = el("button", { type: "button", class: "active", text: "Email invite" });
  const segLink = el("button", { type: "button", text: "Copy link" });
  const setDelivery = (mode) => {
    delivery = mode;
    segEmail.classList.toggle("active", mode === "email");
    segLink.classList.toggle("active", mode === "link");
  };
  segEmail.addEventListener("click", () => setDelivery("email"));
  segLink.addEventListener("click", () => setDelivery("link"));

  const linkCode = el("code", { text: "trypluribus.com/accept/… generated on send" });
  const linkBox = el("div", { class: "plb-linkbox" }, linkCode, el("span", { class: "plb-meta plb-meta--dim", text: "single-use" }));

  const sendBtn = button(queueLabel("Send invite", queue, index), "primary", async () => {
    sendBtn.disabled = true;
    try {
      const data = await sendInvite({
        person,
        email: emailInput.value.trim(),
        note: noteInput.value.trim(),
        delivery,
      });
      const url = data.action?.accept_url || "";
      markInvited(person.source_key);
      linkCode.textContent = url.replace(/^https:\/\//, "");
      if (delivery === "link" && navigator.clipboard && url) {
        try {
          await navigator.clipboard.writeText(url);
          toast(`Accept link copied — ${data.message}`);
        } catch {
          toast(data.message);
        }
      } else {
        toast(data.message);
      }
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      if (index + 1 < queue.length) {
        showDialog(queue, index + 1, onDone);
      } else {
        onDone?.();
      }
    } catch (error) {
      console.error("[Pluribus] invite failed", error);
      toast(`Invite failed: ${error.message}`);
      sendBtn.disabled = false;
    }
  });

  const right = el(
    "div",
    { class: "plb-dialog-right" },
    field("Recipient", el("input", { class: "plb-input", value: person.name || "", disabled: "" })),
    field("Email or handle", emailInput),
    field("Personal note", noteInput),
    field("Delivery", el("div", { class: "plb-seg" }, segEmail, segLink)),
    linkBox,
    el("div", { class: "plb-dialog-actions" }, sendBtn, button("Cancel", "secondary", close)),
    el("div", {
      class: "plb-dialog-note",
      text:
        `Recorded locally for this workspace. ${firstName(person)}'s likeness stays ` +
        `unapproved until terms are accepted.`,
    })
  );

  const dialog = el(
    "div",
    { class: "plb-dialog" },
    el(
      "div",
      { class: "plb-dialog-header" },
      el(
        "div",
        { style: "display:flex;align-items:center;gap:10px" },
        pluribusMark(16),
        el(
          "div",
          {},
          el("div", { class: "plb-dialog-title", text: "Invite to Pluribus" }),
          el("div", { class: "plb-dialog-sub", text: "Clear NIL & performance usage" })
        )
      ),
      el("button", { class: "plb-x", type: "button", text: "×", onclick: close })
    ),
    el("div", { class: "plb-dialog-body" }, left, right)
  );

  overlay.appendChild(dialog);
  document.body.appendChild(overlay);
  emailInput.focus();
}

function field(label, control) {
  return el("div", { class: "plb-field" }, metaLabel(label), control);
}

function firstName(person) {
  return (person.name || "them").split(/\s+/)[0];
}

function sourceLine(person) {
  const kind = person.source_kind || "";
  return person.source_key ? `${kind} · ${person.source_key}` : kind;
}

function queueLabel(base, queue, index) {
  return queue.length > 1 ? `${base} (${index + 1} of ${queue.length})` : base;
}
