const STORAGE_KEY = "pluribus.confirmation-request-ids.v1";
const STORAGE_VERSION = 1;
const MAX_ENTRIES = 20;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const RETIRED_DELIVERY_STATES = new Set([
  "link_ready",
  "provider_accepted",
  "delivered",
  "delayed",
  "bounced",
  "complained",
  "not_configured",
  "manual_reconciliation",
  "suppressed",
  "sent",
  "already_sent",
  "closed",
]);

/**
 * Keep only an opaque material hash and its request UUID in localStorage. Unlike
 * sessionStorage, localStorage survives a ComfyUI page/browser restart, which is
 * the exact point at which a lost response otherwise becomes a duplicate send.
 * Recipient and message material is hashed and is never written to storage.
 */
export async function ensureConfirmationClientRequestId(material, options = {}) {
  const cryptoApi = options.cryptoApi ?? globalThis.crypto;
  const fingerprint = await confirmationRequestMaterialFingerprint(material, cryptoApi);
  const storage = resolveStorage(options);
  const entries = readEntries(storage);
  const existing = entries.find((entry) => entry.fingerprint === fingerprint);

  if (existing) {
    writeEntries(
      storage,
      entries.filter((entry) => entry !== existing).concat(existing)
    );
    return { fingerprint, clientRequestId: existing.clientRequestId };
  }

  if (!cryptoApi?.randomUUID) {
    throw new Error("This browser cannot create a secure confirmation request ID.");
  }
  const clientRequestId = cryptoApi.randomUUID();
  writeEntries(storage, entries.concat({ fingerprint, clientRequestId }));
  return { fingerprint, clientRequestId };
}

export function clearConfirmationClientRequestId(
  fingerprint,
  clientRequestId,
  options = {}
) {
  if (!SHA256_PATTERN.test(fingerprint || "") || !UUID_PATTERN.test(clientRequestId || "")) {
    return;
  }
  const storage = resolveStorage(options);
  const entries = readEntries(storage);
  writeEntries(
    storage,
    entries.filter(
      (entry) =>
        entry.fingerprint !== fingerprint || entry.clientRequestId !== clientRequestId
    )
  );
}

export async function replaceConfirmationClientRequestIdAfterConflict(
  material,
  prior,
  options = {}
) {
  clearConfirmationClientRequestId(prior.fingerprint, prior.clientRequestId, options);
  return ensureConfirmationClientRequestId(material, options);
}

export function shouldRetainConfirmationClientRequestId(deliveryState) {
  const normalized =
    typeof deliveryState === "string" ? deliveryState.trim().toLowerCase() : "";
  return !normalized || !RETIRED_DELIVERY_STATES.has(normalized);
}

export async function confirmationRequestMaterialFingerprint(
  material,
  cryptoApi = globalThis.crypto
) {
  if (!cryptoApi?.subtle?.digest) {
    throw new Error("This browser cannot securely fingerprint the confirmation request.");
  }
  const bytes = new TextEncoder().encode(canonicalConfirmationRequestMaterial(material));
  const digest = await cryptoApi.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")
  ).join("");
}

export function canonicalConfirmationRequestMaterial(material = {}) {
  return JSON.stringify({
    projectId: normalizedId(material.projectId),
    workflowRef: normalizedId(material.workflowRef),
    rightsManifestHash: normalizedId(material.rightsManifestHash),
    talentRecordId: normalizedId(material.talentRecordId),
    recipientEmail: normalizedEmail(material.recipientEmail),
    recipientName: normalizedText(material.recipientName),
    recipientRole: normalizedText(material.recipientRole),
    message: normalizedText(material.message),
    delivery: normalizedText(material.delivery),
    expiresInDays: Number(material.expiresInDays),
  });
}

function normalizedId(value) {
  return normalizedText(value).toLowerCase();
}

function normalizedEmail(value) {
  return normalizedText(value).toLowerCase();
}

function normalizedText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function resolveStorage(options) {
  if (Object.hasOwn(options, "storage")) return options.storage;
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

function readEntries(storage) {
  if (!storage) return [];
  try {
    const value = JSON.parse(storage.getItem(STORAGE_KEY) || "null");
    if (value?.version !== STORAGE_VERSION || !Array.isArray(value.entries)) return [];
    return value.entries
      .filter(
        (entry) =>
          entry &&
          SHA256_PATTERN.test(entry.fingerprint || "") &&
          UUID_PATTERN.test(entry.clientRequestId || "")
      )
      .slice(-MAX_ENTRIES);
  } catch {
    return [];
  }
}

function writeEntries(storage, entries) {
  if (!storage) return;
  try {
    storage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        version: STORAGE_VERSION,
        entries: entries.slice(-MAX_ENTRIES),
      })
    );
  } catch {
    // Storage can be disabled or full. The dialog still retains its in-memory ID.
  }
}

export const confirmationRequestIdempotencyContract = Object.freeze({
  storageKey: STORAGE_KEY,
  maxEntries: MAX_ENTRIES,
});
