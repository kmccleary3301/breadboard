import * as vscode from "vscode"
import { HostController } from "./hostController"
import { ENGINE_TOKEN_SECRET_KEY } from "./config"
import { reduceTranscriptEvents, type TranscriptRenderState } from "./transcriptReducer"

const SIDEBAR_VIEW_ID = "breadboard.sidebar"

class BreadboardSidebarViewProvider implements vscode.WebviewViewProvider {
  private webviewView: vscode.WebviewView | null = null
  private transcriptBySession = new Map<string, TranscriptRenderState>()

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly controller: HostController,
    private readonly context: vscode.ExtensionContext,
  ) {
    this.controller.setSink({
      onConnection: async (state) => {
        await this.post({
          v: 1,
          kind: "evt",
          topic: "bb/connection",
          payload: state,
        })
      },
      onState: async (payload) => {
        await this.post({
          v: 1,
          kind: "evt",
          topic: "bb/state",
          payload,
        })
      },
      onEvents: async (payload) => {
        const previous = this.transcriptBySession.get(payload.sessionId) ?? {
          totalEvents: 0,
          lastEventType: null,
          lines: [],
        }
        const reduced = reduceTranscriptEvents(previous, payload.events, { maxLines: 200 })
        this.transcriptBySession.set(payload.sessionId, reduced)
        await this.post({
          v: 1,
          kind: "evt",
          topic: "bb/events",
          payload: { ...payload, render: reduced },
        })
      },
    })
  }

  public resolveWebviewView(webviewView: vscode.WebviewView): void {
    this.webviewView = webviewView
    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "media")],
    }
    webviewView.webview.html = this.getHtml(webviewView.webview)
    webviewView.webview.onDidReceiveMessage((message) => {
      void this.handleWebviewRequest(message)
    })
    void this.pushConnectionState(webviewView)
  }

  private async respond(
    id: string,
    ok: boolean,
    payload: { result?: unknown; error?: { code: string; message: string; details?: unknown } },
  ): Promise<void> {
    await this.post({
      v: 1,
      kind: "res",
      id,
      ok,
      ...(ok ? { result: payload.result } : { error: payload.error }),
    })
  }

  private async handleWebviewRequest(message: unknown): Promise<void> {
    if (!message || typeof message !== "object") return
    const req = message as { v?: unknown; kind?: unknown; id?: unknown; method?: unknown; params?: unknown }
    if (req.v !== 1 || req.kind !== "req" || typeof req.id !== "string" || typeof req.method !== "string") return

    try {
      if (req.method === "bb.checkConnection") {
        const state = await this.controller.checkConnection(this.context)
        await this.respond(req.id, true, { result: state })
        return
      }
      if (req.method === "bb.listSessions") {
        const sessions = await this.controller.listSessions(this.context)
        await this.respond(req.id, true, { result: sessions })
        return
      }
      if (req.method === "bb.attachSession") {
        const params = (req.params ?? {}) as Record<string, unknown>
        const sessionId = typeof params.sessionId === "string" ? params.sessionId : null
        if (!sessionId) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId is required" },
          })
          return
        }
        await this.controller.attachToSession(this.context, sessionId)
        await this.respond(req.id, true, { result: { attached: true, sessionId } })
        return
      }
      if (req.method === "bb.sendMessage") {
        const params = (req.params ?? {}) as Record<string, unknown>
        const sessionId = typeof params.sessionId === "string" ? params.sessionId : this.controller.getActiveSessionId()
        const text = typeof params.text === "string" ? params.text : ""
        if (!sessionId || text.trim().length === 0) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId and text are required" },
          })
          return
        }
        await this.controller.sendInput(this.context, sessionId, text)
        await this.respond(req.id, true, { result: { sent: true, sessionId } })
        return
      }
      if (req.method === "bb.stopSession") {
        const params = (req.params ?? {}) as Record<string, unknown>
        const sessionId = typeof params.sessionId === "string" ? params.sessionId : this.controller.getActiveSessionId()
        if (!sessionId) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId is required" },
          })
          return
        }
        await this.controller.sendCommand(this.context, sessionId, "stop")
        await this.respond(req.id, true, { result: { stopped: true, sessionId } })
        return
      }

      await this.respond(req.id, false, {
        error: { code: "method_not_found", message: `Unknown method: ${req.method}` },
      })
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error)
      await this.respond(req.id, false, {
        error: { code: "internal_error", message: msg },
      })
    }
  }

  public async post(message: unknown): Promise<void> {
    if (!this.webviewView) return
    await this.webviewView.webview.postMessage(message)
  }

  public async pushConnectionState(webviewView: vscode.WebviewView): Promise<void> {
    const state = await this.controller.checkConnection(this.context)
    await webviewView.webview.postMessage({
      v: 1,
      kind: "evt",
      topic: "bb/connection",
      payload: state,
    })
  }

  private getHtml(webview: vscode.Webview): string {
    const nonce = String(Date.now())
    return `<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'nonce-${nonce}';" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>BreadBoard Sidebar</title>
  <style>
    body { font-family: var(--vscode-font-family); padding: 12px; }
    .muted { opacity: 0.8; }
    .status { margin-top: 8px; padding: 8px; border: 1px solid var(--vscode-panel-border); }
    .events { margin-top: 8px; white-space: pre-wrap; font-size: 12px; opacity: 0.9; }
    .transcript { margin-top: 8px; border: 1px solid var(--vscode-panel-border); padding: 8px; max-height: 260px; overflow: auto; font-size: 12px; white-space: pre-wrap; }
    .row { margin-top: 8px; display: flex; gap: 8px; }
    input, button, select { font: inherit; }
    input { flex: 1; }
  </style>
</head>
<body>
  <h3>BreadBoard Sidebar</h3>
  <div class="muted">V1 scaffold: host/controller wiring in progress.</div>
  <div class="status" id="status">Checking engine connection...</div>
  <div class="row">
    <button id="refresh">Refresh sessions</button>
    <button id="recheck">Check connection</button>
  </div>
  <div class="row">
    <select id="sessions"></select>
    <button id="attach">Attach</button>
  </div>
  <div class="row">
    <input id="message" placeholder="Send message to active session..." />
    <button id="send">Send</button>
    <button id="stop">Stop</button>
  </div>
  <div class="events" id="events">No events yet.</div>
  <div class="transcript" id="transcript">Transcript stream will appear here...</div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const statusEl = document.getElementById("status");
    const eventsEl = document.getElementById("events");
    const sessionsEl = document.getElementById("sessions");
    const msgEl = document.getElementById("message");
    const refreshBtn = document.getElementById("refresh");
    const recheckBtn = document.getElementById("recheck");
    const attachBtn = document.getElementById("attach");
    const sendBtn = document.getElementById("send");
    const stopBtn = document.getElementById("stop");
    const transcriptEl = document.getElementById("transcript");
    let totalEvents = 0;
    let activeSessionId = null;
    let reqCounter = 0;
    const pending = new Map();
    const lines = [];

    function extractSummary(evt) {
      if (!evt || typeof evt !== "object") return "";
      const payload = evt.payload && typeof evt.payload === "object" ? evt.payload : {};
      if (typeof payload.text === "string") return payload.text.slice(0, 180);
      if (typeof payload.message === "string") return payload.message.slice(0, 180);
      if (typeof payload.error === "string") return payload.error.slice(0, 180);
      if (typeof payload.tool_name === "string") return payload.tool_name;
      if (typeof payload.tool === "string") return payload.tool;
      return "";
    }

    function appendLine(line) {
      lines.push(line);
      while (lines.length > 200) lines.shift();
      transcriptEl.textContent = lines.join("\\n");
      transcriptEl.scrollTop = transcriptEl.scrollHeight;
    }

    function rpc(method, params) {
      const id = "req-" + (++reqCounter);
      vscode.postMessage({ v: 1, kind: "req", id, method, params });
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
      });
    }

    async function refreshSessions() {
      const sessions = await rpc("bb.listSessions");
      sessionsEl.innerHTML = "";
      for (const s of sessions || []) {
        if (!s || typeof s.sessionId !== "string") continue;
        const opt = document.createElement("option");
        opt.value = s.sessionId;
        opt.textContent = s.status ? s.sessionId + " (" + s.status + ")" : s.sessionId;
        sessionsEl.appendChild(opt);
      }
      if (activeSessionId) sessionsEl.value = activeSessionId;
    }

    refreshBtn.addEventListener("click", () => {
      refreshSessions().catch((err) => {
        statusEl.textContent = "Session refresh failed: " + String(err);
      });
    });

    recheckBtn.addEventListener("click", () => {
      rpc("bb.checkConnection").catch((err) => {
        statusEl.textContent = "Connection check failed: " + String(err);
      });
    });

    attachBtn.addEventListener("click", () => {
      const sessionId = sessionsEl.value;
      if (!sessionId) return;
      rpc("bb.attachSession", { sessionId }).catch((err) => {
        statusEl.textContent = "Attach failed: " + String(err);
      });
    });

    sendBtn.addEventListener("click", () => {
      const text = msgEl.value;
      if (!text || !activeSessionId) return;
      rpc("bb.sendMessage", { sessionId: activeSessionId, text }).then(() => {
        msgEl.value = "";
      }).catch((err) => {
        statusEl.textContent = "Send failed: " + String(err);
      });
    });

    stopBtn.addEventListener("click", () => {
      if (!activeSessionId) return;
      rpc("bb.stopSession", { sessionId: activeSessionId }).catch((err) => {
        statusEl.textContent = "Stop failed: " + String(err);
      });
    });

    window.addEventListener("message", (event) => {
      const message = event.data;
      if (!message) return;
      if (message.kind === "res" && typeof message.id === "string") {
        const wait = pending.get(message.id);
        if (wait) {
          pending.delete(message.id);
          if (message.ok) {
            wait.resolve(message.result);
          } else {
            const err = message.error && message.error.message ? message.error.message : "RPC failed";
            wait.reject(err);
          }
        }
        return;
      }
      if (message.kind !== "evt") return;
      if (message.topic === "bb/connection") {
        const payload = message.payload || {};
        if (payload.status === "connected") {
          const suffix = payload.sessionId ? " (session " + payload.sessionId + ")" : "";
          statusEl.textContent = "Connected to engine" + suffix;
        } else if (payload.status === "error") {
          statusEl.textContent = "Engine connection failed: " + (payload.message || "unknown error");
        } else {
          statusEl.textContent = "Connecting...";
        }
      } else if (message.topic === "bb/state") {
        const payload = message.payload || {};
        if (payload.activeSessionId && typeof payload.activeSessionId === "string") {
          activeSessionId = payload.activeSessionId;
          sessionsEl.value = activeSessionId;
        }
      } else if (message.topic === "bb/events") {
        const payload = message.payload || {};
        const events = Array.isArray(payload.events) ? payload.events : [];
        const render = payload.render && typeof payload.render === "object" ? payload.render : null;
        if (render && typeof render.totalEvents === "number") {
          totalEvents = render.totalEvents;
        } else {
          totalEvents += events.length;
        }
        let lastType = "(none)";
        if (render && typeof render.lastEventType === "string" && render.lastEventType.length > 0) {
          lastType = render.lastEventType;
        } else {
          const last = events.length > 0 ? events[events.length - 1] : null;
          lastType = last && typeof last.type === "string" ? last.type : "(none)";
        }
        if (payload.sessionId && typeof payload.sessionId === "string") {
          activeSessionId = payload.sessionId;
        }
        const active = activeSessionId ? activeSessionId : "(none)";
        eventsEl.textContent = "Active session: " + active + "\\nEvents received: " + totalEvents + "\\nLast event: " + lastType;
        if (render && Array.isArray(render.lines)) {
          lines.splice(0, lines.length);
          for (const line of render.lines) {
            lines.push(typeof line === "string" ? line : String(line));
          }
          transcriptEl.textContent = lines.join("\\n");
          transcriptEl.scrollTop = transcriptEl.scrollHeight;
        } else {
          for (const evt of events) {
            const type = evt && typeof evt.type === "string" ? evt.type : "unknown";
            const summary = extractSummary(evt);
            appendLine(summary ? ("[" + type + "] " + summary) : ("[" + type + "]"));
          }
        }
      }
    });

    refreshSessions().catch(() => {});
  </script>
</body>
</html>`
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const controller = new HostController()
  const provider = new BreadboardSidebarViewProvider(context.extensionUri, controller, context)
  context.subscriptions.push(vscode.window.registerWebviewViewProvider(SIDEBAR_VIEW_ID, provider))

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.focus", async () => {
      await vscode.commands.executeCommand(`${SIDEBAR_VIEW_ID}.focus`)
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.checkConnection", async () => {
      const state = await controller.checkConnection(context)
      if (state.status === "connected") {
        void vscode.window.showInformationMessage("BreadBoard engine connection OK.")
        return
      }
      if (state.status === "error") {
        void vscode.window.showErrorMessage(state.message)
        return
      }
      void vscode.window.showInformationMessage("BreadBoard engine connection in progress.")
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.attachSession", async () => {
      const sessions = await controller.listSessions(context)
      if (sessions.length === 0) {
        void vscode.window.showInformationMessage("No sessions found.")
        return
      }
      const picked = await vscode.window.showQuickPick(
        sessions.map((s) => ({
          label: s.sessionId,
          detail: s.status ? `status=${s.status}` : undefined,
        })),
        { title: "Attach BreadBoard sidebar stream to session" },
      )
      if (!picked) return
      await controller.attachToSession(context, picked.label)
      await provider.post({
        v: 1,
        kind: "evt",
        topic: "bb/state",
        payload: { activeSessionId: picked.label },
      })
      void vscode.window.showInformationMessage(`Attached to session ${picked.label}`)
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.sendMessage", async () => {
      const sessionId = controller.getActiveSessionId()
      if (!sessionId) {
        void vscode.window.showWarningMessage("No active session attached. Use 'Attach to Session Stream' first.")
        return
      }
      const text = await vscode.window.showInputBox({
        title: "BreadBoard Message",
        prompt: `Send message to ${sessionId}`,
        placeHolder: "Continue with implementation...",
        ignoreFocusOut: true,
      })
      if (!text) return
      try {
        await controller.sendInput(context, sessionId, text)
        void vscode.window.showInformationMessage("Message submitted.")
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error)
        void vscode.window.showErrorMessage(`Failed to send message: ${msg}`)
      }
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.stopActiveSession", async () => {
      const sessionId = controller.getActiveSessionId()
      if (!sessionId) {
        void vscode.window.showWarningMessage("No active session attached. Use 'Attach to Session Stream' first.")
        return
      }
      try {
        await controller.sendCommand(context, sessionId, "stop")
        void vscode.window.showInformationMessage(`Stop requested for ${sessionId}.`)
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error)
        void vscode.window.showErrorMessage(`Failed to stop session: ${msg}`)
      }
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.deleteSession", async () => {
      const sessions = await controller.listSessions(context)
      if (sessions.length === 0) {
        void vscode.window.showInformationMessage("No sessions found.")
        return
      }
      const picked = await vscode.window.showQuickPick(
        sessions.map((s) => ({
          label: s.sessionId,
          detail: s.status ? `status=${s.status}` : undefined,
        })),
        { title: "Delete BreadBoard session" },
      )
      if (!picked) return
      const ok = await vscode.window.showWarningMessage(
        `Delete session ${picked.label}?`,
        { modal: true },
        "Delete",
      )
      if (ok !== "Delete") return
      try {
        await controller.deleteSession(context, picked.label)
        void vscode.window.showInformationMessage(`Deleted session ${picked.label}`)
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error)
        void vscode.window.showErrorMessage(`Failed to delete session: ${msg}`)
      }
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.newSession", async () => {
      const task = await vscode.window.showInputBox({
        title: "BreadBoard New Session",
        prompt: "Initial task or prompt",
        placeHolder: "Implement feature X...",
        ignoreFocusOut: true,
      })
      if (!task) return
      try {
        const created = await controller.createSession(context, task)
        const sessionId = typeof created.session_id === "string" ? created.session_id : null
        if (!sessionId) {
          void vscode.window.showWarningMessage("Session created, but no session_id was returned.")
          return
        }
        await controller.attachToSession(context, sessionId)
        void vscode.window.showInformationMessage(`BreadBoard session created: ${sessionId}`)
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error)
        void vscode.window.showErrorMessage(`Failed to create session: ${msg}`)
      }
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.setEngineToken", async () => {
      const value = await vscode.window.showInputBox({
        title: "BreadBoard Engine Token",
        prompt: "Enter bearer token for CLI bridge (stored in VS Code SecretStorage)",
        password: true,
        ignoreFocusOut: true,
      })
      if (!value) return
      await context.secrets.store(ENGINE_TOKEN_SECRET_KEY, value)
      void vscode.window.showInformationMessage("BreadBoard engine token saved.")
    }),
  )

  context.subscriptions.push(
    vscode.commands.registerCommand("breadboard.sidebar.clearEngineToken", async () => {
      await context.secrets.delete(ENGINE_TOKEN_SECRET_KEY)
      void vscode.window.showInformationMessage("BreadBoard engine token cleared.")
    }),
  )
}

export function deactivate(): void {}
