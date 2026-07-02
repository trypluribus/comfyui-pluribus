// Pluribus backend calls. All routes are registered by pluribus/server.py.

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${path} failed with ${res.status}: ${text}`);
  }
  return res.json();
}

export function scanWorkflow(workflow) {
  return post("/pluribus/scan", workflow);
}

export async function getRoster() {
  const res = await fetch("/pluribus/roster");
  if (!res.ok) throw new Error(`/pluribus/roster failed with ${res.status}`);
  return res.json();
}

export function recordAction(payload) {
  return post("/pluribus/action", payload);
}

export function sendInvite({ person, email, note, delivery }) {
  return post("/pluribus/action", {
    kind: "invite",
    talent_id: person.talent_id,
    name: person.name || "Unknown",
    source_key: person.source_key,
    email,
    note,
    delivery,
  });
}

export function replaceSource(workflow, sourceKey, newAssetKey) {
  return post("/pluribus/replace", {
    workflow,
    source_key: sourceKey,
    new_asset_key: newAssetKey,
  });
}
