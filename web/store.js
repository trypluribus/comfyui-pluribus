// Minimal state container. Render code subscribes and re-renders on change;
// keeping this pure-data makes a later framework migration mechanical.

const state = {
  scan: null, // { summary, persons } from /pluribus/scan
  workflow: null, // API-format snapshot the scan ran against
  scanning: false,
  error: null,
  invited: new Set(), // source_keys invited this session
  scannedAt: null,
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

export function markInvited(sourceKey) {
  state.invited.add(sourceKey || "");
  setState({});
}

// Persons that can be invited and have not been this session.
export function invitablePersons() {
  const persons = state.scan?.persons || [];
  return persons.filter(
    (p) => p.available_actions.includes("invite") && !state.invited.has(p.source_key || "")
  );
}

// Anything a producer still has to deal with.
export function needsActionCount() {
  const persons = state.scan?.persons || [];
  return persons.filter((p) =>
    ["needs_review", "restricted", "unidentified"].includes(p.state)
  ).length;
}
