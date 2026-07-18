import {
  deleteLocalPersonDraft,
  getLocalPersonDrafts,
  saveLocalPersonDraft,
} from "./api.js";
import { avatar, button, el, metaLabel, pluribusMark, toast } from "./components.js";
import { identityLinksAfterPersonRemoval } from "./identity-contract.js";
import {
  commitIdentityLinks,
  identityRevisionConflict,
  refreshIdentityLinks,
} from "./identity-analysis.js";
import { getState, projectSourceLinks, setState } from "./store.js";
import {
  draftsForSource,
  sourceDisplayLabel,
  sourceMedia,
  sourceRecordsForScan,
  sourceRefForPerson,
} from "./source-records.js";

const REPRESENTATIVE_ROLES = [
  ["manager", "Manager"],
  ["agent", "Agent"],
  ["attorney", "Attorney"],
  ["guardian", "Parent or guardian"],
  ["talent", "Talent directly"],
  ["rights_holder", "Rights holder"],
  ["other", "Other"],
];

const APPEARANCE_SEARCH_LIMIT = 12;

export function appearanceSourcesForDisclosure(
  sourceRecords = [],
  currentSourceRef = "",
  selectedSourceRefs = [],
  query = "",
  limit = APPEARANCE_SEARCH_LIMIT
) {
  const selected = new Set(selectedSourceRefs || []);
  const normalizedQuery = String(query || "").trim().toLocaleLowerCase();
  const seen = new Set();
  const elsewhere = (sourceRecords || []).filter((source) => {
    const sourceRef = String(source?.sourceRef || "");
    if (!sourceRef || sourceRef === currentSourceRef || seen.has(sourceRef)) return false;
    seen.add(sourceRef);
    return true;
  });
  const alreadyAdded = elsewhere.filter((source) => selected.has(source.sourceRef));
  const matches = normalizedQuery
    ? elsewhere.filter(
        (source, index) =>
          !selected.has(source.sourceRef)
          && sourceDisplayLabel(source, index).toLocaleLowerCase().includes(normalizedQuery)
      )
    : [];
  const safeLimit = Math.max(1, Number(limit) || APPEARANCE_SEARCH_LIMIT);
  const visibleMatches = matches.slice(0, safeLimit);
  return {
    sources: [...alreadyAdded, ...visibleMatches],
    hiddenCount: Math.max(0, matches.length - visibleMatches.length),
    selectedCount: alreadyAdded.length,
  };
}

export function ensurePersonDraftId(draftId = "", cryptoImpl = globalThis.crypto) {
  return draftId || cryptoImpl.randomUUID();
}

export function identityJobSupportsAuthoritativeLinkScrub(job = {}) {
  const jobId = String(job?.jobId || job?.job_id || "");
  const state = String(job?.state || job?.status || "").toLowerCase();
  return Boolean(jobId && ["completed", "complete", "succeeded"].includes(state));
}

export async function loadPersonDrafts(workflowRef, expectedScanEpoch = getState().scanEpoch) {
  if (!workflowRef) return [];
  const payload = await getLocalPersonDrafts(workflowRef);
  const drafts = payload.drafts || [];
  const beforeUpdate = getState();
  if (
    beforeUpdate.scanEpoch !== expectedScanEpoch
    || beforeUpdate.workflowBinding?.workflowRef !== workflowRef
  ) return beforeUpdate.personDrafts || [];
  setState({ personDrafts: drafts, manifestSynced: false });
  const state = getState();
  if (
    state.connection?.state === "connected"
    && state.activeProjectId
    && state.workflowBinding?.workflowRef === workflowRef
    && state.workflowBinding?.projectId === state.activeProjectId
    && state.scan
  ) {
    try {
      const { syncCurrentRightsManifest } = await import("./sync-manifest.js");
      await syncCurrentRightsManifest();
    } catch (error) {
      console.warn("[Pluribus] local person mapping saved; manifest sync is pending", error);
    }
  }
  return drafts;
}

export function personDraftPromotionPayload(draft, canonicalPersonId) {
  return {
    ...draft,
    representative: draft.representative ? { ...draft.representative } : undefined,
    sourceRefs: [...(draft.sourceRefs || [])],
    canonicalPersonId,
  };
}

export async function markPersonDraftPromoted(draft, canonicalPersonId) {
  const workflowRef = getState().workflowBinding?.workflowRef;
  if (!workflowRef || !draft?.draftId || !canonicalPersonId) {
    throw new Error("The local person mapping context is no longer available.");
  }
  const result = await saveLocalPersonDraft(
    workflowRef,
    personDraftPromotionPayload(draft, canonicalPersonId)
  );
  await loadPersonDrafts(workflowRef);
  return result.draft || null;
}

export function linkedCanonicalPersonIds(sourceLinks = []) {
  const ids = new Set();
  for (const link of sourceLinks || []) {
    if (link.disposition !== "linked") continue;
    for (const id of link.talentRecordIds || link.talent_record_ids || []) ids.add(id);
    const singular = link.talentRecordId || link.talent_record_id;
    if (singular) ids.add(singular);
  }
  return ids;
}

export function visiblePersonDrafts(drafts = [], canonicalIds = new Set()) {
  const linkedIds = canonicalIds instanceof Set ? canonicalIds : new Set(canonicalIds || []);
  return (drafts || []).filter(
    (draft) => !draft.canonicalPersonId || !linkedIds.has(draft.canonicalPersonId)
  );
}

export function localDraftsForPerson(person, state = getState()) {
  return draftsForSource(
    person.sourceRef || sourceRefForPerson(person, state.sourceRefs),
    state.personDrafts
  );
}

export function openPersonDraftDialog(person, initialDraftId = "") {
  const state = getState();
  const workflowRef = state.workflowBinding?.workflowRef;
  const openedScanEpoch = state.scanEpoch;
  const openedScan = state.scan;
  const currentSourceRef = person.sourceRef || sourceRefForPerson(person, state.sourceRefs);
  if (!workflowRef || !currentSourceRef) {
    toast("Find people again before adding details.");
    return;
  }
  const reviewContextIsCurrent = () => {
    const latest = getState();
    return latest.scanEpoch === openedScanEpoch
      && latest.workflowBinding?.workflowRef === workflowRef;
  };
  async function ensureReviewContextCurrent() {
    if (!reviewContextIsCurrent()) {
      close();
      toast("The workflow changed. Reopen person details from the current Sources view.");
      return false;
    }
    try {
      const { scanMatchesCurrentWorkflow } = await import("./canvas.js");
      if (!(await scanMatchesCurrentWorkflow(openedScan)) || !reviewContextIsCurrent()) {
        close();
        toast("The graph changed after people were found. Find people again before saving person details.");
        return false;
      }
    } catch (error) {
      toast(error.message || "Could not verify the current graph. Try again when ComfyUI is ready.");
      return false;
    }
    return true;
  }

  const sourceRecords = sourceRecordsForScan(state.scan?.persons || [], state.sourceRefs)
    .filter((source) => source.sourceRef);
  if (!sourceRecords.some((source) => source.sourceRef === currentSourceRef)) {
    sourceRecords.unshift({ ...person, sourceRef: currentSourceRef });
  }
  let selectedDraftId = initialDraftId || localDraftsForPerson(person, state)[0]?.draftId || "";
  let pendingNewDraftId = selectedDraftId ? "" : ensurePersonDraftId();

  const titleId = `plb-person-dialog-${globalThis.crypto.randomUUID()}`;
  const previousFocus = document.activeElement;
  const overlay = el("div", { class: "plb-overlay plb-root" });
  const body = el("div", { class: "plb-dialog-body" });
  const close = () => {
    overlay.remove();
    document.removeEventListener("keydown", onKey);
    previousFocus?.focus?.();
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
    if (event.key !== "Tab") return;
    const focusable = [...dialog.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'
    )].filter((node) => node.getClientRects().length > 0);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });

  function renderBody() {
    const latest = getState();
    const drafts = latest.personDrafts || [];
    const selected = drafts.find((draft) => draft.draftId === selectedDraftId) || null;
    const linkedHere = new Set(draftsForSource(currentSourceRef, drafts).map((draft) => draft.draftId));

    const name = input(selected?.displayName || "", "Full name");
    name.setAttribute("data-autofocus", "true");
    const role = input(selected?.role || "", "Actor, athlete, employee…", "text", 120);
    const talentEmail = input(selected?.talentEmail || "", "Optional", "email", 320);
    const repRole = select(
      REPRESENTATIVE_ROLES,
      selected?.representative?.role || "manager"
    );
    const repName = input(selected?.representative?.name || "", "Optional");
    const repEmail = input(selected?.representative?.email || "", "Optional", "email", 320);
    const notes = el("textarea", {
      class: "plb-textarea",
      maxlength: "3000",
      placeholder: "Character, scene, relationship, or anything the producer should know",
    });
    notes.value = selected?.notes || "";

    const sourceChecks = sourceRecords.map((source, index) => {
      const checkbox = el("input", { type: "checkbox", value: source.sourceRef });
      const isCurrentSource = source.sourceRef === currentSourceRef;
      checkbox.checked = isCurrentSource || (selected?.sourceRefs || []).includes(source.sourceRef);
      checkbox.disabled = isCurrentSource;
      const label = sourceDisplayLabel(source, index);
      return {
        source,
        label,
        checkbox,
        row: el(
          "label",
          { class: "plb-checkrow" },
          checkbox,
          el("span", { text: label })
        ),
      };
    });
    const currentSourceCheck = sourceChecks.find(
      ({ source }) => source.sourceRef === currentSourceRef
    );
    const checksBySourceRef = new Map(
      sourceChecks.map((entry) => [entry.source.sourceRef, entry])
    );
    const sourceSearch = input("", "Search scenes, characters, or filenames");
    sourceSearch.setAttribute("aria-label", "Search for another source where this person appears");
    const sourceResults = el("div", { class: "plb-checklist" });
    const sourceResultNote = el("p", { class: "plb-connect-copy" });
    const elsewhereSummary = el("span");

    function refreshElsewhereSources() {
      const selectedRefs = sourceChecks
        .filter(({ checkbox }) => checkbox.checked)
        .map(({ source }) => source.sourceRef);
      const disclosure = appearanceSourcesForDisclosure(
        sourceRecords,
        currentSourceRef,
        selectedRefs,
        sourceSearch.value
      );
      const rows = disclosure.sources
        .map((source) => checksBySourceRef.get(source.sourceRef)?.row)
        .filter(Boolean);
      if (rows.length) {
        sourceResults.replaceChildren(...rows);
      } else {
        sourceResults.replaceChildren(
          el("p", {
            class: "plb-connect-copy",
            text: sourceSearch.value.trim()
              ? "No matching sources. Try a character, scene, or filename."
              : "Search when you want to add this person to another source.",
          })
        );
      }
      elsewhereSummary.textContent = disclosure.selectedCount
        ? `${disclosure.selectedCount} other ${disclosure.selectedCount === 1 ? "source" : "sources"} added`
        : "Search and add sources";
      sourceResultNote.textContent = disclosure.hiddenCount
        ? `Refine the search to see ${disclosure.hiddenCount} more matching ${disclosure.hiddenCount === 1 ? "source" : "sources"}.`
        : "Only add sources where this same person appears.";
    }

    sourceSearch.addEventListener("input", refreshElsewhereSources);
    sourceChecks
      .filter(({ source }) => source.sourceRef !== currentSourceRef)
      .forEach(({ checkbox }) => checkbox.addEventListener("change", refreshElsewhereSources));
    refreshElsewhereSources();

    let saving = false;
    const save = button(selected ? "Save details" : "Add person", "primary", async () => {
      if (saving) return;
      if (!(await ensureReviewContextCurrent())) return;
      const visibleSourceRefs = new Set(sourceRecords.map((source) => source.sourceRef));
      const preservedSourceRefs = (selected?.sourceRefs || []).filter(
        (sourceRef) => !visibleSourceRefs.has(sourceRef)
      );
      const sourceRefs = [...new Set([
        ...preservedSourceRefs,
        ...sourceChecks
        .filter(({ checkbox }) => checkbox.checked)
        .map(({ checkbox }) => checkbox.value),
      ])];
      if (!sourceRefs.length) {
        toast("Choose at least one source where this person appears.");
        return;
      }
      saving = true;
      save.disabled = true;
      try {
        const representative = repName.value.trim() || repEmail.value.trim()
          ? {
              role: repRole.value,
              name: repName.value.trim() || undefined,
              email: repEmail.value.trim() || undefined,
            }
          : undefined;
        await saveLocalPersonDraft(workflowRef, {
          draftId: selected?.draftId || pendingNewDraftId,
          displayName: name.value.trim(),
          role: role.value.trim() || undefined,
          talentEmail: talentEmail.value.trim() || undefined,
          representative,
          notes: notes.value.trim() || undefined,
          sourceRefs,
          canonicalPersonId: selected?.canonicalPersonId || undefined,
        });
        if (!reviewContextIsCurrent()) {
          close();
          toast("The workflow changed. The local draft was saved to its original workflow.");
          return;
        }
        await loadPersonDrafts(workflowRef, openedScanEpoch);
        close();
        toast(selected ? "Person details updated." : "Person added to this workflow.");
      } catch (error) {
        toast(error.message || "Could not save person details.");
        saving = false;
        save.disabled = false;
      }
    });

    const deleteButton = selected
      ? button("Delete", "ghost", async () => {
          if (!(await ensureReviewContextCurrent())) return;
          if (!window.confirm(`Delete ${selected.displayName || "this person"} from this workflow? Any confirmed visual appearances will return to review.`)) return;
          let visualAssignmentsRemoved = false;
          try {
            const latest = getState();
            const personIds = [selected.draftId, selected.canonicalPersonId].filter(Boolean);
            if (!identityJobSupportsAuthoritativeLinkScrub(latest.identityJob)) {
              toast("Run identity analysis to completion, then reopen Person details and delete. Pluribus must check durable visual assignments before removing a person.");
              return;
            }
            const jobId = String(latest.identityJob.jobId || latest.identityJob.job_id);
            const snapshot = await refreshIdentityLinks(jobId);
            if (
              !snapshot
              || !Array.isArray(snapshot.links)
              || !Number.isInteger(snapshot.revision)
            ) throw new Error("Could not load the current visual assignments and revision.");
            if (!(await ensureReviewContextCurrent())) return;
            const links = identityLinksAfterPersonRemoval(snapshot.links, personIds);
            const changed = links.length !== snapshot.links.length
              || links.some((link, index) => link !== snapshot.links[index]);
            // Always perform the revision-checked write, even when there is no
            // link to remove. Otherwise another window could add an assignment
            // after our refresh and before the local draft deletion.
            await commitIdentityLinks(jobId, links, snapshot.revision);
            visualAssignmentsRemoved = changed;
            if (!(await ensureReviewContextCurrent())) return;
            await deleteLocalPersonDraft(workflowRef, selected.draftId);
            if (!reviewContextIsCurrent()) {
              close();
              toast("The workflow changed. The draft was removed from its original workflow.");
              return;
            }
            await loadPersonDrafts(workflowRef, openedScanEpoch);
            selectedDraftId = "";
            pendingNewDraftId = ensurePersonDraftId();
            renderBody();
            toast("Person removed.");
          } catch (error) {
            if (identityRevisionConflict(error)) {
              close();
              toast("Visual review changed in another window. Reopen person details and try deleting again.");
            } else if (visualAssignmentsRemoved) {
              toast("Visual appearances returned to review, but the person record could not be deleted. Try deleting again.");
            } else {
              toast(error.message || "Could not remove this person.");
            }
          }
        })
      : null;

    const draftRows = drafts.map((draft) => {
      const attached = linkedHere.has(draft.draftId);
      return el(
        "button",
        {
          class: `plb-draft-row${draft.draftId === selectedDraftId ? " active" : ""}`,
          type: "button",
          onclick: () => {
            selectedDraftId = draft.draftId;
            pendingNewDraftId = "";
            renderBody();
          },
        },
        el("strong", { text: draft.displayName || "Unnamed person" }),
        el("small", {
          text: attached ? "Appears in this source" : "Add to this source",
        })
      );
    });

    const left = el(
      "div",
      { class: "plb-dialog-left" },
      el(
        "div",
        { class: "plb-draft-source" },
        avatar(
          { ...person, name: selected?.displayName || person.name || "?" },
          sourceMedia(person)
        ),
        el(
          "div",
          {},
          metaLabel("People in this workflow", true),
          el("strong", { text: sourceDisplayLabel(person) }),
          el("p", {
            class: "plb-connect-copy",
            text: "Add everyone visible in this source. One person can also appear in several sources.",
          })
        )
      ),
      draftRows.length
        ? el("div", { class: "plb-draft-list" }, draftRows)
        : el("p", { class: "plb-connect-copy", text: "No person details yet." }),
      button("+ Add another person", "secondary", () => {
        selectedDraftId = "";
        pendingNewDraftId = ensurePersonDraftId();
        renderBody();
      })
    );

    const optionalDetails = el(
      "details",
      { class: "plb-contact-details" },
      el("summary", {
        text: [
          "Optional contact, representative, and notes",
          [
            selected?.talentEmail,
            selected?.representative?.name || selected?.representative?.email,
            selected?.notes,
          ].filter(Boolean).length
            ? "details saved"
            : "",
        ].filter(Boolean).join(" · "),
      }),
      field("Talent email", talentEmail),
      field("Representative role", repRole),
      field("Representative name", repName),
      field("Representative email", repEmail),
      field("Notes", notes)
    );
    const elsewhereDetails = el(
      "details",
      { class: "plb-contact-details" },
      el(
        "summary",
        {},
        el("strong", { text: "Appears elsewhere" }),
        el("span", { text: " · " }),
        elsewhereSummary
      ),
      field("Find another source", sourceSearch),
      sourceResults,
      sourceResultNote
    );

    const right = el(
      "div",
      { class: "plb-dialog-right" },
      el(
        "div",
        { class: "plb-section-intro" },
        metaLabel(selected ? "Edit person" : "New person", true),
        el("strong", { text: "Who is visible in this source?" }),
        el("p", {
          text: "Start with a name and project role. Contact details and appearances elsewhere are optional.",
        })
      ),
      field("Name", name),
      field("Role in this project", role),
      field(
        "Current source",
        currentSourceCheck?.row || el("div", { class: "plb-checkrow", text: sourceDisplayLabel(person) })
      ),
      optionalDetails,
      elsewhereDetails,
      el(
        "p",
        { class: "plb-dialog-note" },
        "These details stay available in this ComfyUI workflow. Connect only when you want to link records or request permission."
      ),
      el("div", { class: "plb-dialog-actions" }, deleteButton, save, button("Cancel", "secondary", close))
    );
    body.replaceChildren(left, right);
    setTimeout(() => body.querySelector("[data-autofocus]")?.focus(), 0);
  }

  const dialog = el(
    "div",
    {
      class: "plb-dialog plb-draft-dialog",
      role: "dialog",
      "aria-modal": "true",
      "aria-labelledby": titleId,
    },
    el(
      "div",
      { class: "plb-dialog-header" },
      el(
        "div",
        {},
        el("div", { class: "plb-dialog-title", id: titleId }, pluribusMark(13), el("span", { text: "Person details" })),
        el("div", { class: "plb-dialog-sub", text: "Add details before connecting Pluribus" })
      ),
      el("button", { class: "plb-x", type: "button", text: "×", onclick: close, "aria-label": "Close person details" })
    ),
    body
  );
  overlay.append(dialog);
  document.body.append(overlay);
  renderBody();
}

export function renderDraftPeople(container) {
  const cards = draftPersonCards();
  if (!cards.length) return false;
  container.replaceChildren(el("div", { class: "plb-list" }, cards));
  return true;
}

export function draftPersonCards(draftOverride = null) {
  const state = getState();
  const drafts = Array.isArray(draftOverride)
    ? draftOverride
    : visiblePersonDrafts(
        state.personDrafts || [],
        linkedCanonicalPersonIds(projectSourceLinks())
      );
  const sources = sourceRecordsForScan(state.scan?.persons || [], state.sourceRefs);
  return drafts.map((draft) => {
        const source = sources.find((candidate) => (draft.sourceRefs || []).includes(candidate.sourceRef));
        const fallbackSourceRef = (draft.sourceRefs || [])[0];
        const editableSource = source || (fallbackSourceRef
          ? {
              sourceRef: fallbackSourceRef,
              source_kind: "source",
              source_key: "Source not in current scan",
            }
          : null);
        return el(
          "section",
          { class: "plb-card" },
          el(
            "div",
            { class: "plb-card-top" },
            avatar({ name: draft.displayName }, sourceMedia(source)),
            el(
              "div",
              { class: "plb-card-id" },
              el(
                "div",
                { class: "plb-card-name-row" },
                el("div", { class: "plb-card-name", text: draft.displayName || "Unnamed person" }),
                el("span", { class: "plb-kind-tag", text: "Details added" })
              ),
              el("div", {
                class: "plb-card-src",
                text: [draft.role, `${(draft.sourceRefs || []).length} ${(draft.sourceRefs || []).length === 1 ? "source" : "sources"}`]
                  .filter(Boolean)
                  .join(" · "),
              })
            )
          ),
          el("p", {
            class: "plb-note",
            text: draft.notes || "Details added. Connect to link this person to a Pluribus record.",
          }),
          el(
            "div",
            { class: "plb-actions" },
            editableSource
              ? button("Edit details", "secondary", () => openPersonDraftDialog(editableSource, draft.draftId))
              : el("span", { class: "plb-meta plb-meta--dim", text: "Source not in current scan" }),
            el("span", {
              class: "plb-note",
              text: source
                ? "Open Sources to link this person before requesting permission."
                : "Restore or rescan the source before linking this person.",
            })
          )
        );
      });
}

function input(value = "", placeholder = "", type = "text", maxlength = 160) {
  const control = el("input", {
    class: "plb-input",
    type,
    placeholder,
    maxlength: String(maxlength),
  });
  control.value = value;
  return control;
}

function select(options, current) {
  return el(
    "select",
    { class: "plb-input" },
    options.map(([value, label]) => {
      const option = el("option", { value, text: label });
      option.selected = value === current;
      return option;
    })
  );
}

function field(label, control) {
  return el("label", { class: "plb-field" }, metaLabel(label), control);
}
