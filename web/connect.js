// "Connect to Pluribus" dialog — device pairing against the Pluribus webapp.
// The Python side owns the network and the stored token; this dialog drives
// the pairing UX: show the code, poll until approved, reflect the result.
// Everything else in the plugin keeps working if the user never connects.

import { disconnectPluribus, getConnection, pollConnect, startConnect } from "./api.js";
import { button, el, metaLabel, pluribusMark, toast } from "./components.js";
import { getState, setState } from "./store.js";

export async function refreshConnection() {
  try {
    const connection = await getConnection();
    setState({ connection });
    return connection;
  } catch {
    // Local route unavailable (very old ComfyUI); leave connection unknown.
    return null;
  }
}

export function openConnectDialog(connection, onConnected = null) {
  let pollTimer = null;

  const overlay = el("div", { class: "plb-overlay plb-root" });
  const close = () => {
    if (pollTimer) clearInterval(pollTimer);
    overlay.remove();
    document.removeEventListener("keydown", onKey);
  };
  const onKey = (event) => {
    if (event.key === "Escape") close();
  };
  document.addEventListener("keydown", onKey);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });

  const body = el("div", { class: "plb-dialog-body plb-connect-body" });
  const dialog = el(
    "div",
    { class: "plb-dialog plb-connect-dialog" },
    el(
      "div",
      { class: "plb-dialog-header" },
      el("div", { class: "plb-dialog-title" }, pluribusMark(13), el("span", { text: "Connect to Pluribus" })),
      el("button", { class: "plb-x", text: "×", onclick: close })
    ),
    body
  );
  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  const stopPolling = () => {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  };

  function renderIntro(offline = false) {
    stopPolling();
    const children = [
      el("p", {
        class: "plb-connect-copy",
        text:
          "Link this ComfyUI to your Pluribus account to keep project people, intended use, " +
          "confirmation requests, and decisions in one place. Scanning works without it.",
      }),
    ];
    if (offline) {
      children.push(
        el("p", {
          class: "plb-connect-warn",
          text: "Pluribus is unreachable right now. Check your connection and try again.",
        })
      );
    }
    children.push(
      el(
        "div",
        { class: "plb-connect-actions" },
        button(offline ? "Try again" : "Start pairing", "primary", begin)
      )
    );
    body.replaceChildren(...children);
  }

  function renderPairing(pairing) {
    body.replaceChildren(
      el("p", {
        class: "plb-connect-copy",
        text: "Enter this code on the Pluribus site to approve the connection:",
      }),
      el("div", { class: "plb-pairing-code", text: pairing.user_code || "…" }),
      el(
        "p",
        { class: "plb-connect-copy" },
        el("span", { text: "Open " }),
        el("a", {
          class: "plb-connect-link",
          href: pairing.verification_url,
          target: "_blank",
          rel: "noreferrer",
          text: (pairing.verification_url || "").replace(/^https?:\/\//, ""),
        }),
        el("span", { text: " and sign in with your email — the code stays visible here." })
      ),
      el(
        "div",
        { class: "plb-connect-status" },
        el("span", { class: "plb-dot plb-dot--wait" }),
        el("span", { text: "Waiting for approval…" })
      )
    );

    stopPolling();
    const intervalMs = Math.max(3, pairing.interval || 5) * 1000;
    pollTimer = setInterval(async () => {
      let result;
      try {
        result = await pollConnect();
      } catch {
        return; // Local route hiccup; try again next tick.
      }
      if (result.state === "connected") {
        stopPolling();
        await refreshConnection();
        toast(`Connected to Pluribus as ${result.account_email || "your account"}.`);
        if (onConnected) {
          close();
          await runContinuation(onConnected);
        } else {
          renderConnected({ account_email: result.account_email });
        }
      } else if (result.state === "failed") {
        stopPolling();
        await refreshConnection();
        renderFailed(result.reason);
      }
      // "pairing" and "offline" keep waiting; the code remains valid.
    }, intervalMs);
  }

  function renderFailed(reason) {
    const messages = {
      denied: "The connection was denied on the Pluribus site.",
      expired: "The code expired before it was approved.",
      consumed: "This pairing was already completed elsewhere.",
      revoked: "This pairing was revoked.",
    };
    body.replaceChildren(
      el("p", { class: "plb-connect-warn", text: messages[reason] || "Pairing did not complete." }),
      el("div", { class: "plb-connect-actions" }, button("Start over", "primary", begin))
    );
  }

  function renderConnected(current) {
    stopPolling();
    body.replaceChildren(
      el(
        "div",
        { class: "plb-connect-status plb-connect-status--ok" },
        el("span", { class: "plb-dot" }),
        el("span", { text: `Connected as ${current.account_email || "your account"}` })
      ),
      el("p", {
        class: "plb-connect-copy",
        text:
          "Project state syncs when the panel loads or you choose Find people. Recipient decisions " +
          "stay separate from your team's internal review.",
      }),
      el(
        "div",
        { class: "plb-connect-actions" },
        button("Disconnect", "secondary", async () => {
          let result;
          try {
            result = await disconnectPluribus();
          } catch {
            toast("Could not confirm revocation. The local token was kept; try again.");
            return;
          }
          if (result?.state !== "disconnected") {
            toast(
              result?.message ||
                "Could not confirm revocation. The local token was kept; try again."
            );
            return;
          }
          await refreshConnection();
          setState({
            workspace: null,
            workspaceReady: false,
            projects: [],
            activeProjectId: null,
            projectContext: null,
          });
          toast("Disconnected from Pluribus.");
          renderIntro();
        })
      )
    );
  }

  async function begin() {
    body.replaceChildren(
      metaLabel("Contacting Pluribus…", true)
    );
    let result;
    try {
      result = await startConnect();
    } catch {
      renderIntro(true);
      return;
    }
    if (result.state === "pairing") {
      await refreshConnection();
      renderPairing(result);
    } else if (result.state === "offline") {
      renderIntro(true);
    } else {
      toast(result.message || "Pairing failed.");
      renderIntro();
    }
  }

  if (connection?.state === "connected") {
    renderConnected(connection);
  } else if (connection?.state === "pairing") {
    renderPairing(connection);
  } else {
    renderIntro();
  }
}

export function requirePluribusConnection(action) {
  const connection = getState().connection;
  if (connection?.state === "connected") {
    void runContinuation(action);
    return true;
  }
  openConnectDialog(connection, action);
  return false;
}

async function runContinuation(action) {
  try {
    await action?.();
  } catch (error) {
    toast(error.message || "Could not continue after connecting.");
  }
}
