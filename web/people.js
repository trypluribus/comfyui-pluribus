import { avatar, button, el, metaLabel, pluribusMark, statusAxes } from "./components.js";
import { draftPersonCards, renderDraftPeople } from "./person-drafts.js";
import { internalStateForPerson, openConfirmationDialog, requestStateForPerson } from "./request-confirmation.js";
import { isWorkflowContextReady, projectPeople, projectSourceLinks } from "./store.js";
import { isUseBriefReady } from "./use-brief.js";

export function renderPeople(container) {
  if (!isWorkflowContextReady()) {
    if (renderDraftPeople(container)) return;
    container.replaceChildren(
      el(
        "div",
        { class: "plb-empty" },
        pluribusMark(20),
        metaLabel("No people added yet", true),
        el("div", { text: "Open Sources and add details for everyone who appears." })
      )
    );
    return;
  }
  const linkedIds = new Set(
    projectSourceLinks()
      .filter((source) => source.disposition === "linked")
      .flatMap((source) =>
        source.talentRecordIds ||
        source.talent_record_ids ||
        [source.talentRecordId || source.talent_record_id].filter(Boolean)
      )
  );
  const people = projectPeople().filter((person) =>
    linkedIds.has(person.id || person.talentRecordId)
  );
  const draftCards = draftPersonCards();
  if (!people.length && !draftCards.length) {
    container.replaceChildren(
      el(
        "div",
        { class: "plb-empty" },
        pluribusMark(20),
        metaLabel("Project people", true),
        el("div", {
          text: "No one is linked yet. Return to Sources and link a detected source to a real person.",
        })
      )
    );
    return;
  }
  container.replaceChildren(
    el(
      "div",
      { class: "plb-list" },
      people.map((person) => personCard(person)),
      draftCards
    )
  );
}

function personCard(person) {
  const requestState = requestStateForPerson(person);
  const internalState = internalStateForPerson(person);
  const request = button("Request confirmation", "primary", () => void openConfirmationDialog(person));
  request.disabled =
    !isUseBriefReady() || !/ready|scope changed|changes requested|expired|cancelled/i.test(requestState);
  return el(
    "section",
    { class: "plb-card linked" },
    el(
      "div",
      { class: "plb-card-top" },
      avatar({ name: person.displayName || person.name }),
      el(
        "div",
        { class: "plb-card-id" },
        el("div", { class: "plb-card-name", text: person.displayName || person.name || "Unnamed person" }),
        el("div", {
          class: "plb-card-src",
          text: [person.role, person.representativeName].filter(Boolean).join(" · ") || "Project person",
        })
      )
    ),
    statusAxes("Linked", requestState, internalState),
    el("div", { class: "plb-actions" }, request)
  );
}
