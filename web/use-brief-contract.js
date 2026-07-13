export function revocationPathRequired(paidMediaAllowed, channels, platforms) {
  if (paidMediaAllowed || platforms.length) return true;
  return channels.some((value) => !/^(internal|private review|concept|development)$/i.test(value.trim()));
}

export function hasRevocationPath(instructions, modelDisableRequired, platformRemovalRequired) {
  return Boolean(String(instructions || "").trim() || modelDisableRequired || platformRemovalRequired);
}

export function aiActionRowsForLinks(links, requiresFinalApproval) {
  const byPerson = new Map();
  for (const link of links) {
    const ids =
      link.talentRecordIds ||
      link.talent_record_ids ||
      [link.talentRecordId || link.talent_record_id].filter(Boolean);
    for (const id of ids) {
      const values = byPerson.get(id) || new Map();
      for (const operation of link.operations || []) {
        const classType = operation.classType || operation.class_type;
        const pair = operationConsent(classType);
        if (pair) values.set(`${pair.modality}|${pair.action}`, pair);
      }
      byPerson.set(id, values);
    }
  }
  const rows = [];
  for (const [talentRecordId, values] of byPerson) {
    for (const pair of values.values()) {
      rows.push({
        talentRecordId,
        ...pair,
        requiresFinalApproval,
      });
    }
  }
  return rows;
}

function operationConsent(classType) {
  const map = {
    ReActorFaceSwap: { modality: "face", action: "edit" },
    IPAdapter: { modality: "face", action: "generate" },
    IPAdapterAdvanced: { modality: "face", action: "generate" },
    IPAdapterApply: { modality: "face", action: "generate" },
    LoraLoader: { modality: "digital_replica", action: "generate" },
    LoraLoaderModelOnly: { modality: "digital_replica", action: "generate" },
    LoadImage: { modality: "biometric_input", action: "process" },
    GeminiImage2Node: { modality: "face", action: "edit" },
    FluxKontextProImageNode: { modality: "face", action: "edit" },
    KlingImage2VideoNode: { modality: "synthetic_performance", action: "render" },
    CLIPTextEncode: { modality: "nil", action: "generate" },
  };
  return map[classType] || null;
}
