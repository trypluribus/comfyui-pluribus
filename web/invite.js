// CS5 — "Invite to Pluribus" dialog. Two columns: what we detected and the
// proposed terms, then the invite itself. A disconnected attempt
// saves only a local draft; only a successful connected response has a link.

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
import { getState, markInvited } from "./store.js";
import { personMatchesCurrentWorkflow, workflowName } from "./canvas.js";
import {
  emailAttemptDisposition,
  ensureClientRequestId,
  nextClientRequestId,
  shouldAdvanceDraftQueue,
} from "./invite-contract.js";

// Opens the dialog for persons[0]; after a send, advances through the rest
// (the "Invite all for terms (n)" path). onDone fires when the queue empties
// or the dialog is dismissed.
export function openInviteDialog(persons, onDone) {
  const queue = [...persons];
  if (!queue.length) return;
  showDialog(queue, 0, onDone);
}

function showDialog(queue, index, onDone) {
  const person = queue[index];
  const meta = STATE_META[person.state] || {};
  const connected = getState().connection?.state === "connected";
  let delivery = "email";
  let clientRequestId = "";

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
  const advance = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    if (index + 1 < queue.length) {
      showDialog(queue, index + 1, onDone);
    } else {
      onDone?.();
    }
  };

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
    metaLabel(`Detected in ${person.workflow_name || workflowName()}`, true),
    detectedCard,
    el(
      "div",
      {},
      el("div", { class: "plb-ops-label", text: "Proposed terms for this invitation" }),
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
    `Hi ${firstName(person)} — a workflow we're reviewing references a source associated with ` +
    `your likeness. Please review the proposed terms for this specific invitation.`;

  const segEmail = el("button", { type: "button", class: "active", text: "Email invite" });
  const segLink = el("button", { type: "button", text: "Copy link" });
  const setDelivery = (mode) => {
    delivery = mode;
    emailInput.required = connected && mode === "email";
    segEmail.classList.toggle("active", mode === "email");
    segLink.classList.toggle("active", mode === "link");
  };
  segEmail.addEventListener("click", () => setDelivery("email"));
  segLink.addEventListener("click", () => setDelivery("link"));
  setDelivery("email");

  const freezeRequest = () => {
    emailInput.disabled = true;
    noteInput.disabled = true;
    segEmail.disabled = true;
    segLink.disabled = true;
  };

  const linkCode = el("code", { text: "Accept link appears after a successful send" });
  const linkBox = el("div", { class: "plb-linkbox" }, linkCode, el("span", { class: "plb-meta plb-meta--dim", text: "single-use" }));

  const sendBtn = button(
    queueLabel(connected ? "Send invite" : "Save local draft", queue, index),
    "primary",
    async () => {
      sendBtn.disabled = true;
      try {
        if (!(await personMatchesCurrentWorkflow(person))) {
          toast("The workflow changed after this scan. Rescan before sending an invite.");
          sendBtn.disabled = false;
          return;
        }
      } catch (error) {
        console.warn("[Pluribus] could not verify workflow context", error);
        toast("Could not verify the current workflow. Rescan before sending an invite.");
        sendBtn.disabled = false;
        return;
      }
      const email = emailInput.value.trim();
      const validEmail = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email);
      if (
        connected &&
        delivery === "email" &&
        (!emailInput.checkValidity() || !validEmail)
      ) {
        emailInput.setCustomValidity("Enter a valid email address.");
        emailInput.reportValidity();
        emailInput.setCustomValidity("");
        toast("Enter a valid email address for email delivery.");
        sendBtn.disabled = false;
        return;
      }
      try {
        clientRequestId = ensureClientRequestId(clientRequestId);
      } catch (error) {
        toast(error.message);
        sendBtn.disabled = false;
        return;
      }
      // Freeze the exact idempotent request before the first remote attempt.
      // A lost local response must not let edits reuse this UUID for new data.
      freezeRequest();
      try {
        const data = await sendInvite({
          person,
          email,
          note: noteInput.value.trim(),
          delivery,
          workflowName: person.workflow_name || workflowName(),
          workflowFingerprint: person.workflow_fingerprint,
          scopeStatements: clearStatements(person),
          clientRequestId,
        });
        clientRequestId = nextClientRequestId(clientRequestId, data.action);
        const url = data.action?.accept_url || "";
        const sent = data.action?.status === "invited" && Boolean(url);
        if (sent) {
          markInvited(person);
          linkCode.textContent = url;
        }
        const emailDisposition = emailAttemptDisposition(data.action);
        const retrySameInvite =
          sent && delivery === "email" && emailDisposition === "retry_same_invite";
        const manualReconciliation =
          sent && delivery === "email" && emailDisposition === "manual_reconciliation";
        const needsLink =
          sent && (delivery === "link" || emailDisposition !== "sent");
        let copied = false;
        if (needsLink && navigator.clipboard) {
          try {
            await navigator.clipboard.writeText(url);
            copied = true;
          } catch {
            // Keep the dialog open below with the full selectable URL.
          }
        }
        if (retrySameInvite) {
          toast(`${copied ? "Accept link copied — " : ""}${data.message}`);
          sendBtn.textContent = "Retry same invite";
          sendBtn.disabled = false;
          return;
        }
        if (manualReconciliation) {
          toast(`${copied ? "Accept link copied — " : ""}${data.message}`);
          sendBtn.textContent = "Provider review required";
          sendBtn.disabled = true;
          return;
        }
        if (needsLink && !copied) {
          toast("Invite created. Copy the visible accept link manually.");
          sendBtn.textContent = "Link ready";
          sendBtn.disabled = true;
          return;
        }
        if (needsLink && copied) {
          toast(`Accept link copied — ${data.message}`);
        }
        if (!needsLink) {
          toast(data.message);
        }

        if (!sent) {
          if (data.action?.draft_reason === "unconfirmed") {
            sendBtn.textContent = "Retry safely";
            sendBtn.disabled = false;
            return;
          }
          if (shouldAdvanceDraftQueue(connected, data.action)) {
            advance();
            return;
          }
          close();
          return;
        }
        advance();
      } catch (error) {
        console.error("[Pluribus] invite result unconfirmed", error);
        toast("Could not confirm the invite. Retry is safe with the same request ID.");
        sendBtn.textContent = "Retry safely";
        sendBtn.disabled = false;
      }
    }
  );

  const deliveryField = connected
    ? field("Delivery", el("div", { class: "plb-seg" }, segEmail, segLink))
    : null;

  const right = el(
    "div",
    { class: "plb-dialog-right" },
    field("Recipient", el("input", { class: "plb-input", value: person.name || "", disabled: "" })),
    field(connected ? "Email (for email delivery)" : "Email for the draft", emailInput),
    field("Personal note", noteInput),
    deliveryField,
    connected ? linkBox : null,
    el("div", { class: "plb-dialog-actions" }, sendBtn, button("Cancel", "secondary", close)),
    el("div", {
      class: "plb-dialog-note",
      text:
        connected
          ? `Pluribus sends the email or creates the link only after this request succeeds. ` +
            `Acceptance appears as Terms accepted on the next scan.`
          : `Local draft only. This will not email ${firstName(person)} or create an accept ` +
            `link. Connect to Pluribus when you're ready to send.`,
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
          el("div", { class: "plb-dialog-sub", text: "Proposed NIL & performance terms" })
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
