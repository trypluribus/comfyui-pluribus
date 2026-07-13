// Canonical execution-graph fingerprint shared with pluribus/api.py.
// Object keys are sorted recursively and the UTF-8 JSON bytes are SHA-256'd.

export function canonicalGraphJson(value) {
  if (value === null || typeof value !== "object") {
    const encoded = JSON.stringify(value);
    return encoded === undefined ? "null" : encoded;
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => canonicalGraphJson(item)).join(",")}]`;
  }
  const entries = Object.keys(value)
    .filter((key) => value[key] !== undefined)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalGraphJson(value[key])}`);
  return `{${entries.join(",")}}`;
}

export async function workflowFingerprint(workflow) {
  if (!globalThis.crypto?.subtle) {
    throw new Error("This browser cannot compute a secure workflow fingerprint.");
  }
  const bytes = new TextEncoder().encode(canonicalGraphJson(workflow));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
