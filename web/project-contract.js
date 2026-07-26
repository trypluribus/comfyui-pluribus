export function projectRefreshReady(state) {
  return Boolean(
    state.connection?.state === "connected" &&
      state.workspaceReady &&
      state.workspace &&
      !state.projectLoading
  );
}

export function projectRefreshPatch(current, projects) {
  const preferred = [
    current.workflowBinding?.projectId,
    current.activeProjectId,
    projects[0]?.id,
  ].find((projectId) => projects.some((project) => project.id === projectId)) || null;
  const activeChanged = preferred !== current.activeProjectId;
  const activeSummary = projects.find((project) => project.id === preferred) || null;
  const projectContext = !preferred || activeChanged
    ? null
    : current.projectContext?.id === preferred && activeSummary
      ? { ...current.projectContext, title: activeSummary.title }
      : current.projectContext;
  return {
    projects,
    activeProjectId: preferred,
    projectContext,
    ...(activeChanged ? { manifestSynced: false } : {}),
  };
}
