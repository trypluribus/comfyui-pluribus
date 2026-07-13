// Minimal state container. Render code subscribes and re-renders on change;
// keeping this pure-data makes a later framework migration mechanical.

const state = {
  scan: null, // { summary, persons } from /pluribus/scan
  workflow: null, // API-format snapshot the scan ran against
  scanning: false,
  error: null,
  invited: new Set(), // workflow/source/scope contexts invited this session
  scannedAt: null,
  scanEpoch: 0, // incremented when ComfyUI configures a different graph
  connection: null, // { state, account_email?, ... } from /pluribus/connect
};

const listeners = new Set();

export function getState() {
  return state;
}

export function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function setState(patch) {
  Object.assign(state, patch);
  for (const listener of listeners) listener(state);
}

export function invalidateScan() {
  setState({
    scan: null,
    workflow: null,
    scanning: false,
    error: null,
    scannedAt: null,
    scanEpoch: state.scanEpoch + 1,
  });
}

function inviteKey(person) {
  return JSON.stringify([
    person.workflow_name || "",
    person.workflow_fingerprint || "",
    person.source_kind || "",
    person.source_key || "",
    person.scope_statements || [],
  ]);
}

export function markInvited(person) {
  state.invited.add(inviteKey(person));
  setState({});
}

export function wasInvited(person) {
  return state.invited.has(inviteKey(person));
}

// Persons that can be invited and have not been this session.
export function invitablePersons() {
  const persons = state.scan?.persons || [];
  return persons.filter(
    (person) => person.available_actions.includes("invite") && !wasInvited(person)
  );
}

// Anything a producer still has to deal with.
export function needsActionCount() {
  const persons = state.scan?.persons || [];
  return persons.filter((p) =>
    ["needs_review", "restricted", "unidentified"].includes(p.state)
  ).length;
}
