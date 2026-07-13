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

export function scanWorkflow(workflow, workflowName = "", workflowFingerprint = "") {
  return post("/pluribus/scan", {
    workflow,
    workflow_name: workflowName,
    workflow_fingerprint: workflowFingerprint,
  });
}

export async function getRoster() {
  const res = await fetch("/pluribus/roster");
  if (!res.ok) throw new Error(`/pluribus/roster failed with ${res.status}`);
  return res.json();
}

export function recordAction(payload) {
  return post("/pluribus/action", payload);
}

export function sendInvite({
  person,
  email,
  note,
  delivery,
  workflowName,
  workflowFingerprint,
  scopeStatements,
  clientRequestId,
}) {
  return post("/pluribus/action", {
    kind: "invite",
    talent_id: person.talent_id,
    name: person.name || "Unknown",
    source_key: person.source_key,
    source_kind: person.source_kind,
    workflow_name: workflowName || "",
    workflow_fingerprint: workflowFingerprint || "",
    scope_statements: scopeStatements || [],
    client_request_id: clientRequestId || "",
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

// ── Pluribus webapp connection (device pairing) ─────────────────────────

export async function getConnection() {
  const res = await fetch("/pluribus/connect");
  if (!res.ok) throw new Error(`/pluribus/connect failed with ${res.status}`);
  return res.json();
}

export function startConnect() {
  return post("/pluribus/connect/start", {});
}

export function pollConnect() {
  return post("/pluribus/connect/poll", {});
}

export function disconnectPluribus() {
  return post("/pluribus/connect/disconnect", {});
}

export function syncInvites() {
  return post("/pluribus/invites/sync", {});
}

// ── Canonical project workflow (v0.4+) ────────────────────────────────

async function requestJson(path, init = {}) {
  const response = await fetch(path, init);
  let data = {};
  try {
    data = await response.json();
  } catch {
    // Error below remains useful when an upstream proxy returned no JSON.
  }
  if (!response.ok) {
    throw new Error(data.message || `${path} failed with ${response.status}`);
  }
  return data;
}

function jsonInit(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function getPluginWorkspace() {
  return requestJson("/pluribus/workspace");
}

export function setupPluginWorkspace(payload) {
  return requestJson("/pluribus/workspace", jsonInit("POST", payload));
}

export function listProjects() {
  return requestJson("/pluribus/projects");
}

export function createProject(payload) {
  return requestJson("/pluribus/projects", jsonInit("POST", payload));
}

export function getProject(projectId, workflowRef = "") {
  const query = workflowRef ? `?workflowRef=${encodeURIComponent(workflowRef)}` : "";
  return requestJson(`/pluribus/projects/${encodeURIComponent(projectId)}${query}`);
}

export function createProjectPerson(projectId, payload) {
  return requestJson(
    `/pluribus/projects/${encodeURIComponent(projectId)}/people`,
    jsonInit("POST", payload)
  );
}

export function saveProjectSourceLinks(projectId, payload) {
  return requestJson(
    `/pluribus/projects/${encodeURIComponent(projectId)}/source-links`,
    jsonInit("PUT", payload)
  );
}

export function saveProjectUse(projectId, payload) {
  return requestJson(
    `/pluribus/projects/${encodeURIComponent(projectId)}/use`,
    jsonInit("PUT", payload)
  );
}

export function createProjectConfirmation(projectId, payload) {
  return requestJson(
    `/pluribus/projects/${encodeURIComponent(projectId)}/confirmation-requests`,
    jsonInit("POST", payload)
  );
}

export function resolveLocalWorkflow(localWorkflowKey, graphHash = "") {
  return requestJson(
    "/pluribus/workflows/resolve",
    jsonInit("POST", { localWorkflowKey, graphHash })
  );
}

export function bindLocalWorkflow(workflowRef, projectId, workflowKind) {
  return requestJson(
    `/pluribus/workflows/${encodeURIComponent(workflowRef)}`,
    jsonInit("PUT", { projectId, workflowKind })
  );
}

export function resolveLocalSource(workflowRef, localSourceKey, sourceKind) {
  return requestJson(
    `/pluribus/workflows/${encodeURIComponent(workflowRef)}/sources/resolve`,
    jsonInit("POST", { localSourceKey, sourceKind })
  );
}
