import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  clearConfirmationClientRequestId,
  confirmationRequestIdempotencyContract,
  ensureConfirmationClientRequestId,
  replaceConfirmationClientRequestIdAfterConflict,
  shouldRetainConfirmationClientRequestId,
} from "../request-idempotency.js";

const MATERIAL = Object.freeze({
  projectId: "11111111-1111-4111-8111-111111111111",
  workflowRef: "22222222-2222-4222-8222-222222222222",
  rightsManifestHash: "a".repeat(64),
  talentRecordId: "33333333-3333-4333-8333-333333333333",
  recipientEmail: "nisreen@example.com",
  recipientName: "Nisreen Salem",
  recipientRole: "talent",
  message: "Please review this exact use.",
  delivery: "email",
  expiresInDays: 14,
});

test("the exact same confirmation material reuses its UUID after a dialog/module remount", async () => {
  const storage = memoryStorage();
  const first = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(1),
  });
  const remounted = await ensureConfirmationClientRequestId({ ...MATERIAL }, {
    storage,
    cryptoApi: deterministicCrypto(2),
  });

  assert.equal(remounted.fingerprint, first.fingerprint);
  assert.equal(remounted.clientRequestId, first.clientRequestId);
});

test("changed confirmation material receives a new UUID", async () => {
  const storage = memoryStorage();
  const first = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(1),
  });
  const changed = await ensureConfirmationClientRequestId(
    { ...MATERIAL, message: "Please review the corrected use." },
    { storage, cryptoApi: deterministicCrypto(2) }
  );

  assert.notEqual(changed.fingerprint, first.fingerprint);
  assert.notEqual(changed.clientRequestId, first.clientRequestId);
});

test("a canonical response can clear the durable UUID mapping", async () => {
  const storage = memoryStorage();
  const first = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(1),
  });

  clearConfirmationClientRequestId(first.fingerprint, first.clientRequestId, { storage });

  const nextDeliberateRequest = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(2),
  });
  assert.notEqual(nextDeliberateRequest.clientRequestId, first.clientRequestId);
});

test("a canonical key conflict clears and rekeys exactly that material", async () => {
  const storage = memoryStorage();
  const first = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(1),
  });
  const replacement = await replaceConfirmationClientRequestIdAfterConflict(
    MATERIAL,
    first,
    { storage, cryptoApi: deterministicCrypto(2) }
  );
  const replay = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(3),
  });

  assert.notEqual(replacement.clientRequestId, first.clientRequestId);
  assert.equal(replay.clientRequestId, replacement.clientRequestId);
});

test("ambiguous delivery states retain the request UUID until delivery is terminal", () => {
  for (const status of ["queued", "in_flight", "failed", "pending", "future_retry_state"]) {
    assert.equal(shouldRetainConfirmationClientRequestId(status), true);
  }
  for (const status of [
    "link_ready",
    "provider_accepted",
    "delivered",
    "suppressed",
    "manual_reconciliation",
  ]) {
    assert.equal(shouldRetainConfirmationClientRequestId(status), false);
  }
});

test("an ambiguous response reuses its UUID after remount while link-ready retires it", async () => {
  const storage = memoryStorage();
  const first = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(1),
  });
  if (!shouldRetainConfirmationClientRequestId("in_flight")) {
    clearConfirmationClientRequestId(first.fingerprint, first.clientRequestId, { storage });
  }

  const remounted = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(2),
  });
  assert.equal(remounted.clientRequestId, first.clientRequestId);

  if (!shouldRetainConfirmationClientRequestId("link_ready")) {
    clearConfirmationClientRequestId(
      remounted.fingerprint,
      remounted.clientRequestId,
      { storage }
    );
  }
  const nextDeliberateRequest = await ensureConfirmationClientRequestId(MATERIAL, {
    storage,
    cryptoApi: deterministicCrypto(3),
  });
  assert.notEqual(nextDeliberateRequest.clientRequestId, first.clientRequestId);
});

test("corrupt or unavailable browser storage is ignored safely", async () => {
  const corrupt = memoryStorage({
    [confirmationRequestIdempotencyContract.storageKey]: "not json",
  });
  const fromCorruptStorage = await ensureConfirmationClientRequestId(MATERIAL, {
    storage: corrupt,
    cryptoApi: deterministicCrypto(1),
  });
  assert.equal(fromCorruptStorage.clientRequestId, requestId(1));

  const unavailable = {
    getItem() {
      throw new Error("storage unavailable");
    },
    setItem() {
      throw new Error("storage unavailable");
    },
  };
  const withoutStorage = await ensureConfirmationClientRequestId(MATERIAL, {
    storage: unavailable,
    cryptoApi: deterministicCrypto(2),
  });
  assert.equal(withoutStorage.clientRequestId, requestId(2));
  assert.doesNotThrow(() =>
    clearConfirmationClientRequestId(
      withoutStorage.fingerprint,
      withoutStorage.clientRequestId,
      { storage: unavailable }
    )
  );
});

test("durable storage is bounded and contains no raw request material", async () => {
  const storage = memoryStorage();
  for (let index = 1; index <= confirmationRequestIdempotencyContract.maxEntries + 5; index += 1) {
    await ensureConfirmationClientRequestId(
      { ...MATERIAL, message: `Private request message ${index}` },
      { storage, cryptoApi: deterministicCrypto(index) }
    );
  }

  const serialized = storage.getItem(confirmationRequestIdempotencyContract.storageKey);
  const stored = JSON.parse(serialized);
  assert.equal(stored.entries.length, confirmationRequestIdempotencyContract.maxEntries);
  for (const rawValue of [
    MATERIAL.projectId,
    MATERIAL.workflowRef,
    MATERIAL.talentRecordId,
    MATERIAL.recipientEmail,
    MATERIAL.recipientName,
    MATERIAL.message,
    "Private request message",
  ]) {
    assert.doesNotMatch(serialized, new RegExp(escapeRegExp(rawValue), "i"));
  }
  assert.deepEqual(
    Object.keys(stored.entries[0]).sort(),
    ["clientRequestId", "fingerprint"]
  );
});

test("the confirmation dialog clears persistence only after a safe canonical delivery state", async () => {
  const source = await readFile(new URL("../request-confirmation.js", import.meta.url), "utf8");
  const requestIndex = source.indexOf("result = await createProjectConfirmation");
  const deliveryStateIndex = source.indexOf("const deliveryState =");
  const retentionIndex = source.indexOf(
    "if (!shouldRetainConfirmationClientRequestId(deliveryState))"
  );
  const clearIndex = source.indexOf("clearConfirmationClientRequestId(requestFingerprint, clientRequestId)");

  assert.ok(requestIndex >= 0);
  assert.ok(deliveryStateIndex > requestIndex);
  assert.ok(retentionIndex > deliveryStateIndex);
  assert.ok(clearIndex > requestIndex);
  assert.ok(clearIndex > retentionIndex);
  assert.match(source, /ensureConfirmationClientRequestId\(requestMaterial\)/);
  assert.match(source, /createError\?\.code !== "client_request_key_conflict"/);
  assert.equal(source.match(/replaceConfirmationClientRequestIdAfterConflict\(/g)?.length, 1);
  assert.match(source, /canonical but ambiguous result still needs the durable identity/);
});

test("a suppressed canonical request never offers or copies its expired secure link", async () => {
  const source = await readFile(new URL("../request-confirmation.js", import.meta.url), "utf8");
  const suppressedIndex = source.indexOf('deliveryState === "suppressed"');
  const linkIndex = source.indexOf("if (url) linkCode.textContent = url");
  const clipboardIndex = source.indexOf("navigator.clipboard.writeText(url)");

  assert.ok(suppressedIndex >= 0);
  assert.ok(linkIndex > suppressedIndex);
  assert.ok(clipboardIndex > suppressedIndex);
  assert.match(source, /No additional email was sent/);
});

function deterministicCrypto(index) {
  return {
    subtle: webcrypto.subtle,
    randomUUID: () => requestId(index),
  };
}

function requestId(index) {
  return `00000000-0000-4000-8000-${String(index).padStart(12, "0")}`;
}

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
  };
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
