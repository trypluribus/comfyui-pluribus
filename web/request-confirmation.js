import { createProjectConfirmation } from "./api.js";
import { scanMatchesCurrentWorkflow } from "./canvas.js";
import { button, el, metaLabel, pluribusMark, toast } from "./components.js";
import { loadProjectContext } from "./project.js";
import {
  clearConfirmationClientRequestId,
  ensureConfirmationClientRequestId,
  replaceConfirmationClientRequestIdAfterConflict,
  shouldRetainConfirmationClientRequestId,
} from "./request-idempotency.js";
import { getState, isWorkflowContextReady, projectSourceLinks } from "./store.js";
import { aiActionRowsForLinks } from "./use-brief-contract.js";

export async function openConfirmationDialog(person) {
  const state = getState();
  const project = projectRecord();
  const scope = scopeRecord();
  const terms = person.terms || {};
  const personId = person.id || person.talentRecordId;
  const inferredAiActions = aiActionRowsForLinks(
    projectSourceLinks(),
    Boolean(scope.finalCreativeApprovalRequired)
  )
    .filter((item) => item.talentRecordId === personId);
  const openEpoch = state.scanEpoch;
  if (
    !isWorkflowContextReady() ||
    !(await scanMatchesCurrentWorkflow(state.scan)) ||
    getState().scanEpoch !== openEpoch
  ) {
    toast("Find people in the current graph before requesting confirmation.");
    return;
  }

  const recipientEmail = el("input", {
    class: "plb-input",
    type: "email",
    value: person.representativeEmail || person.talentEmail || person.email || "",
    placeholder: "name@example.com",
  });
  const recipientName = el("input", {
    class: "plb-input",
    value: person.representativeName || person.displayName || person.name || "",
  });
  const role = el(
    "select",
    { class: "plb-input" },
    option("talent", "Talent directly"),
    option("manager", "Manager"),
    option("agent", "Agent"),
    option("attorney", "Attorney"),
    option("guardian", "Parent or guardian"),
    option("rights_holder", "Rights holder"),
    option("other", "Other")
  );
  if (person.representativeEmail) role.value = "manager";
  const note = el("textarea", { class: "plb-textarea", maxlength: "3000" });
  note.value = `Please review the intended use for ${project.title || "this project"} and record your decision.`;
  const emailMode = el("button", { type: "button", class: "active", text: "Send email" });
  const linkMode = el("button", { type: "button", text: "Copy secure link" });
  let delivery = "email";
  const setDelivery = (value) => {
    delivery = value;
    emailMode.classList.toggle("active", value === "email");
    linkMode.classList.toggle("active", value === "link");
  };
  emailMode.addEventListener("click", () => setDelivery("email"));
  linkMode.addEventListener("click", () => setDelivery("link"));
  const linkCode = el("code", { text: "The secure link appears after the request is created." });
  const linkBox = el("div", { class: "plb-linkbox" }, linkCode);

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

  let clientRequestId = "";
  let requestFingerprint = "";
  const send = button("Request confirmation", "primary", async () => {
    if (!recipientEmail.value.trim() || !recipientEmail.checkValidity()) {
      recipientEmail.reportValidity();
      return;
    }
    send.disabled = true;
    recipientEmail.disabled = true;
    recipientName.disabled = true;
    role.disabled = true;
    note.disabled = true;
    emailMode.disabled = true;
    linkMode.disabled = true;
    try {
      const sendEpoch = getState().scanEpoch;
      if (
        !isWorkflowContextReady() ||
        !(await scanMatchesCurrentWorkflow(getState().scan)) ||
        getState().scanEpoch !== sendEpoch
      ) {
        throw new Error("The graph changed. Find people again before requesting confirmation.");
      }
      const rightsManifestHash = currentManifestHashForRequest();
      const requestMaterial = {
        projectId: state.activeProjectId,
        workflowRef: state.workflowBinding?.workflowRef,
        rightsManifestHash,
        talentRecordId: person.id || person.talentRecordId,
        recipientEmail: recipientEmail.value.trim(),
        recipientName: recipientName.value.trim() || undefined,
        recipientRole: role.value,
        message: note.value.trim() || undefined,
        delivery,
        expiresInDays: 14,
      };
      if (!clientRequestId) {
        const durableRequest = await ensureConfirmationClientRequestId(requestMaterial);
        clientRequestId = durableRequest.clientRequestId;
        requestFingerprint = durableRequest.fingerprint;
      }
      const payload = (requestId) => ({
        clientRequestId: requestId,
        workflowRef: requestMaterial.workflowRef,
        rightsManifestHash: requestMaterial.rightsManifestHash,
        talentRecordId: requestMaterial.talentRecordId,
        recipientEmail: requestMaterial.recipientEmail,
        recipientName: requestMaterial.recipientName,
        recipientRole: requestMaterial.recipientRole,
        message: requestMaterial.message,
        delivery: requestMaterial.delivery,
        expiresInDays: requestMaterial.expiresInDays,
      });
      let result;
      try {
        result = await createProjectConfirmation(
          state.activeProjectId,
          payload(clientRequestId)
        );
      } catch (createError) {
        if (createError?.code !== "client_request_key_conflict") throw createError;
        const replacement = await replaceConfirmationClientRequestIdAfterConflict(
          requestMaterial,
          { fingerprint: requestFingerprint, clientRequestId }
        );
        clientRequestId = replacement.clientRequestId;
        requestFingerprint = replacement.fingerprint;
        result = await createProjectConfirmation(
          state.activeProjectId,
          payload(clientRequestId)
        );
      }
      const url = result.reviewUrl || result.url || result.confirmationUrl || result.urlPath || "";
      const emailDelivery =
        result.emailDelivery || result.confirmation?.emailDelivery || result.delivery?.emailDelivery;
      const deliveryState = typeof result.delivery === "string" ? result.delivery : emailDelivery;
      // A canonical but ambiguous result still needs the durable identity so a
      // reload replays this request instead of creating a second delivery.
      if (!shouldRetainConfirmationClientRequestId(deliveryState)) {
        clearConfirmationClientRequestId(requestFingerprint, clientRequestId);
        requestFingerprint = "";
        clientRequestId = "";
      }
      if (deliveryState === "suppressed") {
        linkCode.textContent =
          "This request expired or was resolved before another email could be sent.";
        toast("The request is closed. No additional email was sent.");
        send.textContent = "Request closed";
        await loadProjectContext(state.activeProjectId);
        return;
      }
      if (url) linkCode.textContent = url;
      if (result.existing && !url) {
        if (["sent", "already_sent"].includes(deliveryState)) {
          linkCode.textContent =
            "This existing request was already sent. Its secure link cannot be shown again.";
          toast("The existing confirmation request was already sent.");
          send.textContent = "Already sent";
          await loadProjectContext(state.activeProjectId);
          return;
        }
        if (deliveryState === "pending") {
          linkCode.textContent =
            "Delivery is still pending or ambiguous. Do not create another request; reconcile this attempt in Pluribus.";
          toast("Delivery is unconfirmed. Keep this request and reconcile it before retrying.");
          send.textContent = "Reconciliation required";
          await loadProjectContext(state.activeProjectId);
          return;
        }
        linkCode.textContent =
          "The earlier request cannot reveal its secure link again and may remain valid. Choose Create replacement only if you deliberately want an additional request.";
        toast("The existing link is unavailable. A replacement requires a deliberate new request.");
        send.textContent = "Create replacement request";
        send.disabled = false;
        recipientEmail.disabled = false;
        recipientName.disabled = false;
        role.disabled = false;
        note.disabled = false;
        emailMode.disabled = false;
        linkMode.disabled = false;
        await loadProjectContext(state.activeProjectId);
        return;
      }
      if (delivery === "link" || !["sent", "already_sent"].includes(deliveryState)) {
        if (url && navigator.clipboard) {
          try {
            await navigator.clipboard.writeText(url);
            toast(
              delivery === "link"
                ? "Secure confirmation link copied."
                : "Email was not confirmed; secure link copied instead."
            );
          } catch {
            toast("Request created. Copy the secure link shown here.");
          }
        } else {
          toast("Request created. Copy the secure link shown here.");
        }
        send.textContent = "Link ready";
      } else {
        toast(`Confirmation request emailed to ${recipientEmail.value}.`);
        send.textContent = "Sent";
      }
      await loadProjectContext(state.activeProjectId);
    } catch (error) {
      toast(error.message || "The confirmation result is unconfirmed. Retry with the same request.");
      send.textContent = "Retry same request";
      send.disabled = false;
      return;
    }
  });

  const preview = el(
    "div",
    { class: "plb-dialog-left" },
    metaLabel("Exact scope preview", true),
    previewRow("Project", project.title),
    previewRow("Brand / client", project.clientName || "Not set"),
    previewRow("Person", person.displayName || person.name),
    previewRow("Intended use", scope.usageType || "Not set"),
    previewRow("Deliverables", join(scope.deliverables)),
    previewRow("Channels", join(scope.channels)),
    previewRow("Platforms", join(scope.platforms)),
    previewRow("Territory", join(scope.territory || scope.territories)),
    previewRow("Languages", join(scope.languages)),
    previewRow("Product category", scope.productCategory || scope.category),
    previewRow(
      "Term",
      [scope.usageWindowStart || scope.termStart, scope.usageWindowEnd || scope.termEnd]
        .filter(Boolean)
        .join(" – ") || "Not set"
    ),
    previewRow("Paid / organic", media(scope)),
    previewRow("Inferred AI actions", describeAiActions(inferredAiActions)),
    previewRow("Final approval", scope.finalCreativeApprovalRequired ? "Required" : "Not required"),
    previewRow("Compensation handling", handling(scope.compensationHandling, scope.compensation)),
    previewRow("Person compensation", terms.compensation),
    previewRow("Exclusivity", handling(scope.exclusivityHandling, scope.exclusivity)),
    previewRow("Person restrictions", terms.restrictions),
    previewRow("Usage comfort / caveats", terms.usageComfort),
    previewRow("Representative authority notes", terms.repAuthority || terms.representativeAuthority),
    previewRow("Revocation / takedown instructions", scope.revocationInstructions),
    previewRow("Takedown SLA", scope.takedownSla),
    previewRow("Model disablement on revocation", scope.modelDisableRequired ? "Required" : "Not required"),
    previewRow("Platform removal on revocation", scope.platformRemovalRequired ? "Required" : "Not required"),
    el("p", {
      class: "plb-dialog-note",
      text:
        "The recipient can approve, approve with a caveat, request changes, decline, exclude this person, or state that they lack authority. Their response remains separate from internal review.",
    })
  );
  const form = el(
    "div",
    { class: "plb-dialog-right" },
    field("Recipient email", recipientEmail),
    field("Recipient name", recipientName),
    field("Expected role (recipient confirms their own authority)", role),
    field("Message", note),
    field("Delivery", el("div", { class: "plb-seg" }, emailMode, linkMode)),
    linkBox,
    el("div", { class: "plb-dialog-actions" }, send, button("Close", "secondary", close))
  );
  overlay.append(
    el(
      "div",
      { class: "plb-dialog" },
      el(
        "div",
        { class: "plb-dialog-header" },
        el(
          "div",
          {},
          el("div", { class: "plb-dialog-title" }, pluribusMark(13), el("span", { text: "Request confirmation" })),
          el("div", { class: "plb-dialog-sub", text: "Preview before sending · versioned permission scope" })
        ),
        el("button", { class: "plb-x", type: "button", text: "×", onclick: close })
      ),
      el("div", { class: "plb-dialog-body" }, preview, form)
    )
  );
  document.body.append(overlay);
  recipientEmail.focus();
}

export function requestStateForPerson(person) {
  if (person.requestState) return person.requestState;
  if (person.confirmationStatus) return humanize(person.confirmationStatus);
  const id = person.id || person.talentRecordId;
  const context = getState().projectContext || {};
  const matches = (context.confirmations || []).filter((confirmation) =>
    (confirmation.items || []).some((item) =>
      (item.talentRecordId || item.talent_record_id) === id
    )
  );
  if (!matches.length) return "Ready to request";
  return humanize(matches[0].status || "sent");
}

export function internalStateForPerson(person) {
  return (
    person.internalState ||
    person.internalReviewStatus ||
    getState().projectContext?.internalReview?.status ||
    "Not reviewed"
  );
}

function projectRecord() {
  const context = getState().projectContext || {};
  return context.project || context.campaign || context;
}

function scopeRecord() {
  const context = getState().projectContext || {};
  return context.useBrief || context.scope || context.project?.useBrief || context.project?.scope || {};
}

function currentManifestHashForRequest() {
  const context = getState().projectContext || {};
  const manifestHash = context.workflow?.manifestHash || context.manifestHash;
  if (!/^[a-f0-9]{64}$/.test(manifestHash || "")) {
    throw new Error("The current rights manifest is unavailable. Find people again before requesting confirmation.");
  }
  return manifestHash;
}

function field(label, control) {
  return el("label", { class: "plb-field" }, metaLabel(label), control);
}

function option(value, label) {
  return el("option", { value, text: label });
}

function previewRow(label, value) {
  return el("div", { class: "plb-scope-row" }, el("dt", { text: label }), el("dd", { text: value || "Not set" }));
}

function join(value) {
  return Array.isArray(value) && value.length ? value.join(", ") : "Not set";
}

function media(scope) {
  const values = [];
  if (scope.organicMediaAllowed) values.push("Organic")
  if (scope.paidMediaAllowed) values.push("Paid")
  return values.join(" and ") || "Neither selected";
}

function handling(kind, summary) {
  if (kind === "handled_separately") return summary ? `Handled separately · ${summary}` : "Handled separately";
  if (kind === "not_part_of_request") return "Not part of this request";
  return summary || "Included";
}

function describeAiActions(actions) {
  if (!actions.length) return "No supported downstream action inferred";
  return actions
    .map((item) => `${humanize(item.modality)} — ${humanize(item.action)}`)
    .join(", ");
}

function humanize(value) {
  return String(value || "").replaceAll("_", " ");
}
