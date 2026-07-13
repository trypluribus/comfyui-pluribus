export function shouldAdvanceDraftQueue(connected, action) {
  return (
    !connected && action?.status === "draft" && action?.draft_reason === "disconnected"
  );
}

export function nextClientRequestId(current, action) {
  return action?.client_request_id || current || "";
}

export function ensureClientRequestId(current, cryptoApi = globalThis.crypto) {
  if (current) return current;
  if (!cryptoApi?.randomUUID) {
    throw new Error("This browser cannot create a secure invite request ID.");
  }
  return cryptoApi.randomUUID();
}

export function emailAttemptDisposition(action) {
  const state = action?.email_attempt_state || "";
  if (
    action?.email_reconciliation_required === true ||
    state === "manual_reconciliation"
  ) {
    return "manual_reconciliation";
  }
  if (state === "ambiguous" || state === "in_flight") {
    return "retry_same_invite";
  }
  if (state === "sent" || action?.email_delivery === "sent") {
    return "sent";
  }
  return "link_only";
}
