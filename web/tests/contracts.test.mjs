import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

globalThis.crypto ??= webcrypto;

import { canonicalGraphJson, workflowFingerprint } from "../fingerprint.js";
import {
  emailAttemptDisposition,
  ensureClientRequestId,
  nextClientRequestId,
  shouldAdvanceDraftQueue,
} from "../invite-contract.js";
import { markInvited, wasInvited } from "../store.js";

const GRAPH = {
  "2": {
    inputs: {
      strength: 1.0,
      ratio: 0.5,
      label: "é",
      flags: [true, false, null],
      nested: { z: 2.0, a: "x" },
    },
    class_type: "X",
  },
  "1": { inputs: {}, class_type: "Y" },
};

test("canonical graph fingerprint matches the cross-language fixture", async () => {
  assert.equal(
    canonicalGraphJson(GRAPH),
    '{"1":{"class_type":"Y","inputs":{}},"2":{"class_type":"X","inputs":{"flags":[true,false,null],"label":"é","nested":{"a":"x","z":2},"ratio":0.5,"strength":1}}}'
  );
  assert.equal(
    await workflowFingerprint(GRAPH),
    "f1eafde7905ff8c0cebf3a84d7ba45651441afd6e2f8411e877e3ad4414561ff"
  );
});

test("in-session invite state is scoped by workflow fingerprint", () => {
  const base = {
    workflow_name: "Morning People",
    source_kind: "reference",
    source_key: "marcus_ref.png",
    scope_statements: ["Use of their likeness"],
  };
  const first = { ...base, workflow_fingerprint: "a".repeat(64) };
  const changed = { ...base, workflow_fingerprint: "b".repeat(64) };

  markInvited(first);

  assert.equal(wasInvited(first), true);
  assert.equal(wasInvited(changed), false);
});

test("disconnected bulk drafting advances, connected failures do not", () => {
  const draft = { status: "draft", draft_reason: "disconnected" };
  assert.equal(shouldAdvanceDraftQueue(false, draft), true);
  assert.equal(shouldAdvanceDraftQueue(true, draft), false);
  assert.equal(
    shouldAdvanceDraftQueue(false, { status: "draft", draft_reason: "unconfirmed" }),
    false
  );
});

test("retry carries the first response request id", () => {
  const requestId = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4";
  assert.equal(nextClientRequestId("", { client_request_id: requestId }), requestId);
  assert.equal(nextClientRequestId(requestId, {}), requestId);
});

test("a thrown first attempt retains the pre-minted request id", async () => {
  const requestId = "6ccfc0e8-094a-4d69-a8df-70c864a1e9f4";
  let current = "";
  const fakeCrypto = {
    randomUUID: () => requestId,
  };

  try {
    current = ensureClientRequestId(current, fakeCrypto);
    throw new Error("local response lost");
  } catch {
    // The dialog stays open and retries with the captured value.
  }

  assert.equal(ensureClientRequestId(current, { randomUUID: () => "different" }), requestId);
});

test("email attempt state distinguishes retry from manual reconciliation", () => {
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "failed",
      email_attempt_state: "ambiguous",
    }),
    "retry_same_invite"
  );
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "failed",
      email_attempt_state: "in_flight",
    }),
    "retry_same_invite"
  );
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "failed",
      email_attempt_state: "manual_reconciliation",
      email_reconciliation_required: true,
    }),
    "manual_reconciliation"
  );
  assert.equal(
    emailAttemptDisposition({
      email_delivery: "sent",
      email_attempt_state: "sent",
    }),
    "sent"
  );
});

test("extension registers graph-load invalidation through afterConfigureGraph", async () => {
  const source = await readFile(new URL("../pluribus.js", import.meta.url), "utf8");
  assert.match(source, /async afterConfigureGraph\(\)/);
  assert.match(source, /clearReticles\(\)/);
  assert.match(source, /invalidateScan\(\)/);
});

test("safe retry freezes mutable invite controls and sends the captured id", async () => {
  const source = await readFile(new URL("../invite.js", import.meta.url), "utf8");
  assert.match(source, /emailInput\.disabled = true/);
  assert.match(source, /noteInput\.disabled = true/);
  assert.match(source, /segEmail\.disabled = true/);
  assert.match(source, /segLink\.disabled = true/);
  assert.match(source, /clientRequestId,/);
  assert.ok(source.indexOf("freezeRequest();") < source.indexOf("await sendInvite"));
});

test("launch copy stays bounded to graph topology and roster review", async () => {
  const paths = ["../components.js", "../invite.js", "../panel.js", "../pluribus.js", "../roster.js"];
  const source = (
    await Promise.all(paths.map((path) => readFile(new URL(path, import.meta.url), "utf8")))
  ).join("\n");

  assert.doesNotMatch(
    source,
    /Performance altered by|workflow is ready|CONSENT LIVE|consent scope travels|we used your likeness|credited and compensated/
  );
  assert.match(source, /Downstream graph nodes/);
  assert.match(source, /ROSTER LINKED · REVIEW SCOPE/);
  assert.match(source, /This scan does not inspect rendered pixels/);
});
