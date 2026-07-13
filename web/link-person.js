import { createProjectPerson } from "./api.js";
import { button, el, metaLabel, pluribusMark, toast } from "./components.js";
import { personLocalKey } from "./manifest.js";
import { getState, projectPeople, projectSourceLinks } from "./store.js";
import { syncCurrentRightsManifest } from "./sync-manifest.js";

export function linkedPeopleForSource(person) {
  const sourceRef = getState().sourceRefs[personLocalKey(person)];
  if (!sourceRef) return [];
  const links = projectSourceLinks().filter((link) =>
    (link.sourceRef || link.source_ref) === sourceRef
  );
  const ids = new Set();
  for (const link of links) {
    for (const id of link.talentRecordIds || link.talent_record_ids || []) ids.add(id);
    if (link.talentRecordId || link.talent_record_id) {
      ids.add(link.talentRecordId || link.talent_record_id);
    }
  }
  const people = projectPeople();
  return people.filter((candidate) => ids.has(candidate.id || candidate.talentRecordId));
}

export async function setSourceDisposition(person, disposition) {
  const state = getState();
  if (!state.activeProjectId || !state.workflowBinding?.workflowRef) {
    toast("Choose a project before classifying this source.");
    return;
  }
  const existingIds = linkedPeopleForSource(person).map((candidate) =>
    candidate.id || candidate.talentRecordId
  );
  await saveSourceUpdate(person, disposition, disposition === "linked" ? existingIds : []);
  toast(disposition === "not_person" ? "Marked as not a person." : "Flagged for review.");
}

export function openLinkPersonDialog(person) {
  const state = getState();
  if (!state.activeProjectId || !state.workflowBinding?.workflowRef) {
    toast("Choose a project before linking people.");
    return;
  }

  const existingLinked = new Set(
    linkedPeopleForSource(person).map((candidate) => candidate.id || candidate.talentRecordId)
  );
  const people = projectPeople();
  const checks = people.map((candidate) => {
    const id = candidate.id || candidate.talentRecordId;
    const input = el("input", { type: "checkbox", value: id });
    input.checked = existingLinked.has(id);
    return {
      id,
      input,
      row: el(
        "label",
        { class: "plb-checkrow" },
        input,
        el("span", { text: candidate.displayName || candidate.name || "Unnamed person" })
      ),
    };
  });
  const name = el("input", { class: "plb-input", placeholder: "Full name", maxlength: "160" });
  const role = el("input", { class: "plb-input", placeholder: "Actor, athlete, employee…", maxlength: "120" });
  const talentEmail = el("input", { class: "plb-input", type: "email", placeholder: "Optional" });
  const repName = el("input", { class: "plb-input", placeholder: "Optional", maxlength: "160" });
  const repEmail = el("input", { class: "plb-input", type: "email", placeholder: "Optional" });
  const repRole = el(
    "select",
    { class: "plb-input" },
    option("manager", "Manager"),
    option("agent", "Agent"),
    option("attorney", "Attorney"),
    option("guardian", "Parent or guardian"),
    option("talent", "Talent directly"),
    option("other", "Other")
  );

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

  const save = button("Link people", "primary", async () => {
    save.disabled = true;
    try {
      const ids = checks.filter((item) => item.input.checked).map((item) => item.id);
      if (name.value.trim()) {
        const createdPayload = await createProjectPerson(state.activeProjectId, {
          mode: "new",
          displayName: name.value.trim(),
          role: role.value.trim() || undefined,
          talentEmail: talentEmail.value.trim() || undefined,
          representative: repName.value.trim() || repEmail.value.trim()
            ? {
                role: repRole.value,
                name: repName.value.trim() || undefined,
                email: repEmail.value.trim() || undefined,
              }
            : undefined,
        });
        const created = createdPayload.person || createdPayload.talent || createdPayload;
        ids.push(created.id || created.talentRecordId);
      }
      const uniqueIds = [...new Set(ids.filter(Boolean))];
      if (!uniqueIds.length) {
        toast("Choose an existing person or add a new one.");
        save.disabled = false;
        return;
      }
      await saveSourceUpdate(person, "linked", uniqueIds);
      close();
      toast(`Linked ${uniqueIds.length} ${uniqueIds.length === 1 ? "person" : "people"}.`);
    } catch (error) {
      toast(error.message || "Could not link this source.");
      save.disabled = false;
    }
  });

  const existing = el(
    "div",
    { class: "plb-dialog-left" },
    metaLabel("Existing project people", true),
    checks.length
      ? el("div", { class: "plb-checklist" }, checks.map((item) => item.row))
      : el("p", { class: "plb-connect-copy", text: "No people yet. Add the first person here." }),
    el("p", {
      class: "plb-dialog-note",
      text: "A single frame or reference may be linked to more than one real person.",
    })
  );
  const add = el(
    "div",
    { class: "plb-dialog-right" },
    metaLabel("Add a new person", true),
    field("Name", name),
    field("Role in this project", role),
    field("Talent email", talentEmail),
    field("Representative role", repRole),
    field("Representative name", repName),
    field("Representative email", repEmail),
    el("div", { class: "plb-dialog-actions" }, save, button("Cancel", "secondary", close))
  );
  const dialog = el(
    "div",
    { class: "plb-dialog" },
    el(
      "div",
      { class: "plb-dialog-header" },
      el(
        "div",
        {},
        el("div", { class: "plb-dialog-title" }, pluribusMark(13), el("span", { text: "Link source to people" })),
        el("div", { class: "plb-dialog-sub", text: "The source stays local; Pluribus receives an opaque reference only" })
      ),
      el("button", { class: "plb-x", type: "button", text: "×", onclick: close })
    ),
    el("div", { class: "plb-dialog-body" }, existing, add)
  );
  overlay.append(dialog);
  document.body.append(overlay);
}

async function saveSourceUpdate(person, disposition, talentRecordIds) {
  const state = getState();
  const sourceRef = state.sourceRefs[personLocalKey(person)];
  if (!sourceRef) throw new Error("The local source reference is not ready. Rescan and try again.");
  await syncCurrentRightsManifest(new Map([
    [sourceRef, { disposition, talentRecordIds }],
  ]));
}

function field(label, control) {
  return el("label", { class: "plb-field" }, metaLabel(label), control);
}

function option(value, label) {
  return el("option", { value, text: label });
}
