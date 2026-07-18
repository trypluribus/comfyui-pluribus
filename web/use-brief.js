import { saveProjectUse } from "./api.js";
import { button, el, metaLabel, toast } from "./components.js";
import { requirePluribusConnection } from "./connect.js";
import { scanMatchesCurrentWorkflow } from "./canvas.js";
import { loadProjectContext } from "./project.js";
import {
  aiActionRowsForLinks,
  hasRevocationPath,
  revocationPathRequired,
} from "./use-brief-contract.js";
import {
  getState,
  isWorkflowContextReady,
  projectPeople,
  projectSourceLinks,
} from "./store.js";

export function renderUseBrief(container) {
  const state = getState();
  if (!isWorkflowContextReady()) {
    if (state.connection?.state !== "connected") {
      container.replaceChildren(
        empty(
          "Connect when you're ready to save intended use and request permission.",
          button("Connect to Pluribus", "primary", () => requirePluribusConnection())
        )
      );
    } else {
      container.replaceChildren(empty("Choose a project and link people before defining intended use."));
    }
    return;
  }
  const current = currentScope();
  const personTerms = personTermControls();
  const usageType = input(current.usageType || "AI-assisted advertising video");
  const deliverables = input(join(current.deliverables));
  const channels = input(join(current.channels));
  const platforms = input(join(current.platforms));
  const territory = input(join(current.territory || current.territories));
  const languages = input(join(current.languages) || "English");
  const category = input(current.category || current.productCategory || "");
  const termStart = input(current.usageWindowStart || current.termStart || "", "date");
  const termEnd = input(current.usageWindowEnd || current.termEnd || "", "date");
  const paid = checkbox(Boolean(current.paidMediaAllowed));
  const organic = checkbox(Boolean(current.organicMediaAllowed));
  const finalApproval = checkbox(Boolean(current.finalCreativeApprovalRequired));
  const compensationHandling = handlingSelect(current.compensationHandling || "handled_separately");
  const compensation = input(current.compensation || "");
  const exclusivityHandling = handlingSelect(current.exclusivityHandling || "not_part_of_request");
  const exclusivity = input(current.exclusivity || "");
  const revocationInstructions = textarea(current.revocationInstructions || "", 1000);
  const takedownSla = input(current.takedownSla || "");
  const modelDisableRequired = checkbox(Boolean(current.modelDisableRequired));
  const platformRemovalRequired = checkbox(Boolean(current.platformRemovalRequired));

  const save = button("Save intended use", "primary", async () => {
    if (!usageType.value.trim() || !deliverables.value.trim() || !channels.value.trim()) {
      toast("Add the intended use, deliverable, and at least one channel.");
      return;
    }
    save.disabled = true;
    try {
      const scanEpoch = getState().scanEpoch;
      if (!(await scanMatchesCurrentWorkflow(getState().scan)) || getState().scanEpoch !== scanEpoch) {
        throw new Error("The graph changed. Find people again before saving intended use.");
      }
      const manifestHash = await currentManifestHash();
      if (compensationHandling.value === "included" && !compensation.value.trim()) {
        toast("Describe compensation or mark it as handled separately / outside this request.");
        save.disabled = false;
        return;
      }
      if (exclusivityHandling.value === "included" && !exclusivity.value.trim()) {
        toast("Describe exclusivity or mark it as handled separately / outside this request.");
        save.disabled = false;
        return;
      }
      if (!category.value.trim()) {
        toast("Add the product category this creative will advertise.");
        save.disabled = false;
        return;
      }
      const channelValues = split(channels.value);
      const platformValues = split(platforms.value);
      if (
        revocationPathRequired(paid.checked, channelValues, platformValues) &&
        !hasRevocationPath(
          revocationInstructions.value,
          modelDisableRequired.checked,
          platformRemovalRequired.checked
        )
      ) {
        toast("Add revocation instructions or require model disablement / platform removal for paid or external activation.");
        save.disabled = false;
        return;
      }
      const people = peopleUseRows(personTerms);
      await saveProjectUse(state.activeProjectId, {
        workflowRef: state.workflowBinding.workflowRef,
        rightsManifestHash: manifestHash,
        usageType: usageType.value.trim(),
        deliverables: split(deliverables.value),
        channels: channelValues,
        platforms: platformValues,
        territories: split(territory.value),
        languages: split(languages.value),
        usageWindowStart: termStart.value || null,
        usageWindowEnd: termEnd.value || null,
        productCategory: category.value.trim(),
        paidMediaAllowed: paid.checked,
        organicMediaAllowed: organic.checked,
        compensationHandling: compensationHandling.value,
        compensation: compensation.value.trim() || null,
        exclusivityHandling: exclusivityHandling.value,
        exclusivity: exclusivity.value.trim() || null,
        finalCreativeApprovalRequired: finalApproval.checked,
        aiActions: aiActionRows(finalApproval.checked),
        people,
        revocationInstructions: revocationInstructions.value.trim() || null,
        takedownSla: takedownSla.value.trim() || null,
        modelDisableRequired: modelDisableRequired.checked,
        platformRemovalRequired: platformRemovalRequired.checked,
      });
      await loadProjectContext(state.activeProjectId);
      toast("Intended use saved as a versioned permission scope.");
    } catch (error) {
      toast(error.message || "Could not save intended use.");
    } finally {
      save.disabled = false;
    }
  });

  container.replaceChildren(
    el(
      "div",
      { class: "plb-form-scroll" },
      el(
        "div",
        { class: "plb-section-intro" },
        metaLabel("Versioned use brief", true),
        el("strong", { text: "What are you asking people to approve?" }),
        el("p", {
          text:
            "Only rights-relevant changes create a new scope version. Moving nodes or changing a sampler does not.",
        })
      ),
      field("Intended use", usageType),
      field("Deliverables (comma separated)", deliverables),
      field("Channels", channels),
      field("Platforms", platforms),
      toggleRow("Organic media", organic),
      toggleRow("Paid media", paid),
      field("Territory", territory),
      field("Languages", languages),
      el("div", { class: "plb-field-grid" }, field("Start", termStart), field("End", termEnd)),
      field("Product category", category),
      toggleRow("Final creative approval required", finalApproval),
      field("Compensation handling", compensationHandling),
      field("Compensation summary", compensation),
      field("Exclusivity handling", exclusivityHandling),
      field("Exclusivity summary", exclusivity),
      el("p", {
        class: "plb-dialog-note",
        text:
          "Choose handled separately or not part of request when compensation or exclusivity is intentionally outside this confirmation.",
      }),
      sectionIntro(
        "Per-person terms",
        "These values come from each person's canonical record. Edit a person explicitly; differing terms are never flattened into one project-wide value."
      ),
      ...personTerms.map(renderPersonTerms),
      sectionIntro(
        "Revocation and takedown",
        "Paid or external activation needs an operational path. State who to contact and what happens, then record any response-time or removal requirements."
      ),
      field("Revocation / takedown instructions", revocationInstructions),
      field("Takedown SLA", takedownSla),
      toggleRow("Disable person-specific model or adapter when revoked", modelDisableRequired),
      toggleRow("Remove published creative from named platforms when revoked", platformRemovalRequired),
      el("div", { class: "plb-sticky-actions" }, save)
    )
  );
}

export function isUseBriefReady() {
  if (!isWorkflowContextReady()) return false;
  const scope = currentScope();
  const workflow = getState().projectContext?.workflow || {};
  return Boolean(
    workflow.manifestHash &&
      workflow.useBriefManifestHash === workflow.manifestHash &&
      workflow.useBriefScopeVersion === scope.versionNumber &&
      scope.usageType &&
      (scope.deliverables || []).length &&
      (scope.channels || []).length &&
      (scope.territory || scope.territories || []).length &&
      (scope.usageWindowEnd || scope.termEnd)
  );
}

function currentScope() {
  const context = getState().projectContext || {};
  return context.useBrief || context.scope || context.project?.useBrief || context.project?.scope || {};
}

async function currentManifestHash() {
  const state = getState();
  const existing =
    state.workflowBinding?.manifestHash ||
    state.projectContext?.workflow?.manifestHash ||
    state.projectContext?.manifestHash;
  if (/^[a-f0-9]{64}$/.test(existing || "")) return existing;
  throw new Error("Link or classify at least one detected source before saving intended use.");
}

function peopleUseRows(controls) {
  return controls.map(({ person, compensation, usageComfort, restrictions, representativeAuthority }) => {
    return {
      talentRecordId: person.id || person.talentRecordId,
      restrictions: restrictions.value.trim() || null,
      compensation: compensation.value.trim() || null,
      usageComfort: usageComfort.value.trim() || null,
      representativeAuthority: representativeAuthority.value.trim() || null,
    };
  });
}

function personTermControls() {
  return projectPeople().map((person) => {
    const terms = person.terms || {};
    return {
      person,
      compensation: input(terms.compensation || ""),
      usageComfort: textarea(terms.usageComfort || "", 1000),
      restrictions: textarea(terms.restrictions || "", 1000),
      representativeAuthority: textarea(terms.repAuthority || terms.representativeAuthority || "", 1000),
    };
  });
}

function renderPersonTerms(controls) {
  const name = controls.person.displayName || controls.person.name || "Project person";
  return el(
    "section",
    { class: "plb-card plb-person-terms" },
    el("strong", { class: "plb-card-name", text: name }),
    field("Restrictions / prohibited contexts", controls.restrictions),
    field("Compensation for this person", controls.compensation),
    field("Usage comfort / caveats", controls.usageComfort),
    field("Representative authority notes", controls.representativeAuthority)
  );
}

function sectionIntro(title, description) {
  return el(
    "div",
    { class: "plb-section-intro" },
    el("strong", { text: title }),
    el("p", { text: description })
  );
}

function aiActionRows(requiresFinalApproval) {
  return aiActionRowsForLinks(projectSourceLinks(), requiresFinalApproval);
}

function split(value) {
  return [...new Set(value.split(",").map((item) => item.trim()).filter(Boolean))];
}

function join(value) {
  return Array.isArray(value) ? value.join(", ") : "";
}

function input(value = "", type = "text") {
  return el("input", { class: "plb-input", type, value });
}

function textarea(value = "", maxlength = 1000) {
  const control = el("textarea", { class: "plb-textarea", maxlength: String(maxlength) });
  control.value = value;
  return control;
}

function checkbox(checked) {
  const control = el("input", { type: "checkbox" });
  control.checked = checked;
  return control;
}

function field(label, control) {
  return el("label", { class: "plb-field" }, metaLabel(label), control);
}

function toggleRow(label, control) {
  return el("label", { class: "plb-checkrow" }, control, el("span", { text: label }));
}

function handlingSelect(current) {
  return el(
    "select",
    { class: "plb-input" },
    selectedOption("included", "Included in this request", current),
    selectedOption("handled_separately", "Handled separately", current),
    selectedOption("not_part_of_request", "Not part of this request", current)
  );
}

function selectedOption(value, label, current) {
  const node = el("option", { value, text: label });
  node.selected = value === current;
  return node;
}

function empty(message, action = null) {
  return el("div", { class: "plb-empty" }, el("div", { text: message }), action);
}
