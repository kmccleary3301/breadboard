import * as vscode from "vscode"
import { HostController } from "./hostController"
import { ENGINE_TOKEN_SECRET_KEY } from "./config"
import { reduceTranscriptEvents, type TranscriptRenderState } from "./transcriptReducer"
import { parseRpcReq } from "./rpcContract"
import { sanitizeConnectionState, sanitizeEventsPayload, sanitizeStatePayload } from "./rpcEvents"
import {
  parseApprovePermissionParams,
  parseAttachSessionParams,
  parseDeleteSessionParams,
  parseListFilesParams,
  parseOpenDiffParams,
  parseReadSnippetParams,
  parseSendMessageParams,
  parseStopSessionParams,
} from "./rpcParams"

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
        const payload = sanitizeConnectionState(state)
        await this.post({
          v: 1,
          kind: "evt",
          topic: "bb/connection",
          payload,
        })
      },
      onState: async (payload) => {
        const normalized = sanitizeStatePayload(payload)
        await this.post({
          v: 1,
          kind: "evt",
          topic: "bb/state",
          payload: normalized,
        })
      },
      onEvents: async (payload) => {
        const previous = this.transcriptBySession.get(payload.sessionId) ?? {
          totalEvents: 0,
          lastEventType: null,
          lines: [],
          entries: [],
        }
        const reduced = reduceTranscriptEvents(previous, payload.events, { maxLines: 200 })
        this.transcriptBySession.set(payload.sessionId, reduced)
        const normalized = sanitizeEventsPayload({ ...payload, render: reduced })
        if (!normalized) return
        await this.post({
          v: 1,
          kind: "evt",
          topic: "bb/events",
          payload: normalized,
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
    const req = parseRpcReq(message)
    if (!req) return

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
        const params = parseAttachSessionParams(req.params)
        if (!params) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId is required" },
          })
          return
        }
        await this.controller.attachToSession(this.context, params.sessionId)
        await this.respond(req.id, true, { result: { attached: true, sessionId: params.sessionId } })
        return
      }
      if (req.method === "bb.sendMessage") {
        if (!vscode.workspace.isTrusted) {
          await this.respond(req.id, false, {
            error: { code: "workspace_untrusted", message: "Workspace trust is required for sendMessage." },
          })
          return
        }
        const params = parseSendMessageParams(req.params)
        const sessionId = params?.sessionId ?? this.controller.getActiveSessionId()
        if (!sessionId || !params) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId and text are required" },
          })
          return
        }
        await this.controller.sendInput(this.context, sessionId, params.text)
        await this.respond(req.id, true, { result: { sent: true, sessionId } })
        return
      }
      if (req.method === "bb.stopSession") {
        if (!vscode.workspace.isTrusted) {
          await this.respond(req.id, false, {
            error: { code: "workspace_untrusted", message: "Workspace trust is required for stopSession." },
          })
          return
        }
        const params = parseStopSessionParams(req.params)
        const sessionId = params.sessionId ?? this.controller.getActiveSessionId()
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
      if (req.method === "bb.listFiles") {
        const params = parseListFilesParams(req.params)
        if (!params) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId is required" },
          })
          return
        }
        const files = await this.controller.listFiles(this.context, params.sessionId, params.path ?? ".")
        await this.respond(req.id, true, { result: files })
        return
      }
      if (req.method === "bb.readFileSnippet") {
        const params = parseReadSnippetParams(req.params)
        if (!params) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId and path are required" },
          })
          return
        }
        const snippet = await this.controller.readFileSnippet(this.context, params.sessionId, params.path, {
          headLines: params.headLines,
          tailLines: params.tailLines,
          maxBytes: params.maxBytes,
        })
        await this.respond(req.id, true, { result: snippet })
        return
      }
      if (req.method === "bb.openDiff") {
        const params = parseOpenDiffParams(req.params)
        if (!params) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId and filePath are required" },
          })
          return
        }
        await this.controller.openDiff(this.context, params.sessionId, params.filePath, params.artifactPath)
        await this.respond(req.id, true, { result: { opened: true } })
        return
      }
      if (req.method === "bb.approvePermission") {
        if (!vscode.workspace.isTrusted) {
          await this.respond(req.id, false, {
            error: { code: "workspace_untrusted", message: "Workspace trust is required for permission decisions." },
          })
          return
        }
        const params = parseApprovePermissionParams(req.params)
        if (!params) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId, requestId, and decision are required" },
          })
          return
        }
        await this.controller.sendCommand(this.context, params.sessionId, "permission_decision", {
          request_id: params.requestId,
          decision: params.decision,
          persist: params.decision === "allow_rule",
        })
        await this.respond(req.id, true, { result: { accepted: true } })
        return
      }
      if (req.method === "bb.deleteSession") {
        if (!vscode.workspace.isTrusted) {
          await this.respond(req.id, false, {
            error: { code: "workspace_untrusted", message: "Workspace trust is required for deleteSession." },
          })
          return
        }
        const params = parseDeleteSessionParams(req.params)
        if (!params) {
          await this.respond(req.id, false, {
            error: { code: "bad_request", message: "sessionId is required" },
          })
          return
        }
        await this.controller.deleteSession(this.context, params.sessionId)
        await this.respond(req.id, true, { result: { deleted: true, sessionId: params.sessionId } })
        return
      }

      console.warn(`[breadboard.sidebar] Unknown RPC method: ${req.method}`)
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
    body { font-family: var(--vscode-font-family); padding: 10px; }
    .muted { opacity: 0.8; }
    .status { margin-top: 8px; padding: 8px; border: 1px solid var(--vscode-panel-border); white-space: pre-wrap; font-size: 12px; }
    .row { margin-top: 8px; display: flex; gap: 8px; align-items: center; }
    .col { display: flex; flex-direction: column; gap: 8px; }
    .grow { flex: 1; min-width: 0; }
    input, textarea, button, select { font: inherit; }
    textarea { width: 100%; min-height: 56px; resize: vertical; }
    .tabs { margin-top: 10px; display: flex; gap: 6px; border-bottom: 1px solid var(--vscode-panel-border); padding-bottom: 6px; }
    .tab-btn { border: 1px solid var(--vscode-panel-border); background: transparent; padding: 4px 8px; cursor: pointer; }
    .tab-btn.active { background: var(--vscode-button-background); color: var(--vscode-button-foreground); }
    .pane { display: none; margin-top: 8px; }
    .pane.active { display: block; }
    .transcript { border: 1px solid var(--vscode-panel-border); padding: 8px; max-height: 360px; overflow: auto; font-size: 12px; display: flex; flex-direction: column; gap: 6px; }
    .card { border: 1px solid var(--vscode-panel-border); padding: 6px; border-radius: 4px; }
    .card .title { font-weight: 600; font-size: 12px; margin-bottom: 4px; }
    .card .detail { opacity: 0.92; white-space: pre-wrap; word-break: break-word; }
    .card.user { border-color: var(--vscode-focusBorder); }
    .card.assistant, .card.assistant_delta { border-color: var(--vscode-textSeparator-foreground); }
    .card.tool_call { border-color: var(--vscode-testing-iconQueued); }
    .card.tool_result { border-color: var(--vscode-testing-iconPassed); }
    .card.permission_request { border-color: var(--vscode-editorWarning-foreground); }
    .card.permission_response { border-color: var(--vscode-editorInfo-foreground); }
    .card.error { border-color: var(--vscode-editorError-foreground); }
    .card.warning { border-color: var(--vscode-editorWarning-foreground); }
    .pill { display: inline-block; font-size: 11px; border: 1px solid var(--vscode-panel-border); padding: 0 6px; margin-right: 6px; border-radius: 10px; }
    .listbox { border: 1px solid var(--vscode-panel-border); max-height: 240px; overflow: auto; padding: 4px; font-size: 12px; }
    .list-item { padding: 4px; cursor: pointer; }
    .list-item:hover { background: var(--vscode-list-hoverBackground); }
    .snippet { border: 1px solid var(--vscode-panel-border); padding: 8px; max-height: 240px; overflow: auto; white-space: pre-wrap; font-family: var(--vscode-editor-font-family); font-size: 12px; }
    .tasks { border: 1px solid var(--vscode-panel-border); padding: 8px; max-height: 260px; overflow: auto; display: flex; flex-direction: column; gap: 6px; }
    .task { border: 1px solid var(--vscode-panel-border); padding: 6px; border-radius: 4px; }
    .small { font-size: 11px; opacity: 0.85; }
    .actions { display: flex; gap: 6px; margin-top: 6px; flex-wrap: wrap; }
  </style>
</head>
<body>
  <h3>BreadBoard Sidebar</h3>
  <div class="muted">V1 operator client over BreadBoard engine contracts.</div>
  <div class="status" id="status">Checking engine connection...</div>
  <div class="row">
    <button id="refresh">Refresh sessions</button>
    <button id="recheck">Check connection</button>
    <button id="newSession">New session</button>
  </div>
  <div class="row">
    <select id="sessions" class="grow"></select>
    <button id="attach">Attach</button>
    <button id="deleteSession">Delete</button>
  </div>
  <div class="tabs">
    <button class="tab-btn active" data-tab="chat">Chat</button>
    <button class="tab-btn" data-tab="tasks">Tasks</button>
    <button class="tab-btn" data-tab="files">Files</button>
    <button class="tab-btn" data-tab="run">Run</button>
  </div>

  <div id="pane-chat" class="pane active">
    <div class="transcript" id="transcript"></div>
    <div class="row">
      <textarea id="message" class="grow" placeholder="Send message to active session..."></textarea>
    </div>
    <div class="row">
      <button id="send">Send</button>
      <button id="stop">Stop</button>
      <span class="small" id="composerMeta"></span>
    </div>
  </div>

  <div id="pane-tasks" class="pane">
    <div class="small" id="taskSummary">No task events yet.</div>
    <div class="tasks" id="taskList"></div>
  </div>

  <div id="pane-files" class="pane">
    <div class="row">
      <input id="filePath" class="grow" value="." />
      <button id="refreshFiles">List</button>
    </div>
    <div class="row">
      <div class="listbox grow" id="fileList"></div>
    </div>
    <div class="snippet" id="snippet"></div>
    <div class="actions">
      <button id="insertRef">Insert @ref</button>
      <button id="openDiff">Open Diff</button>
    </div>
  </div>

  <div id="pane-run" class="pane">
    <div class="status" id="runSummary">No run data yet.</div>
  </div>

  <div class="row">
    <div class="status grow" id="events">No events yet.</div>
  </div>
  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();
    const statusEl = document.getElementById("status");
    const eventsEl = document.getElementById("events");
    const runSummaryEl = document.getElementById("runSummary");
    const sessionsEl = document.getElementById("sessions");
    const msgEl = document.getElementById("message");
    const composerMetaEl = document.getElementById("composerMeta");
    const refreshBtn = document.getElementById("refresh");
    const recheckBtn = document.getElementById("recheck");
    const newSessionBtn = document.getElementById("newSession");
    const deleteSessionBtn = document.getElementById("deleteSession");
    const attachBtn = document.getElementById("attach");
    const sendBtn = document.getElementById("send");
    const stopBtn = document.getElementById("stop");
    const transcriptEl = document.getElementById("transcript");
    const taskListEl = document.getElementById("taskList");
    const taskSummaryEl = document.getElementById("taskSummary");
    const filePathEl = document.getElementById("filePath");
    const refreshFilesBtn = document.getElementById("refreshFiles");
    const fileListEl = document.getElementById("fileList");
    const snippetEl = document.getElementById("snippet");
    const insertRefBtn = document.getElementById("insertRef");
    const openDiffBtn = document.getElementById("openDiff");
    const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
    const panes = {
      chat: document.getElementById("pane-chat"),
      tasks: document.getElementById("pane-tasks"),
      files: document.getElementById("pane-files"),
      run: document.getElementById("pane-run"),
    };

    let totalEvents = 0;
    let activeSessionId = null;
    let activeTab = "chat";
    let runningState = "idle";
    let selectedFilePath = null;
    let selectedArtifactPath = null;
    let followTranscript = true;
    let reqCounter = 0;
    const pending = new Map();
    let transcriptEntries = [];
    let taskEntries = [];
    let pendingPermissions = [];
    let renderTimer = null;

    function setActiveTab(tab) {
      activeTab = tab;
      for (const btn of tabButtons) {
        btn.classList.toggle("active", btn.dataset.tab === tab);
      }
      Object.keys(panes).forEach((key) => {
        const pane = panes[key];
        if (pane) pane.classList.toggle("active", key === tab);
      });
    }

    tabButtons.forEach((btn) => {
      btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
    });

    function scheduleRender() {
      if (renderTimer) return;
      renderTimer = setTimeout(() => {
        renderTimer = null;
        renderAll();
      }, 75);
    }

    transcriptEl.addEventListener("scroll", () => {
      const gap = transcriptEl.scrollHeight - (transcriptEl.scrollTop + transcriptEl.clientHeight);
      followTranscript = gap < 16;
    });

    function rpc(method, params) {
      const id = "req-" + (++reqCounter);
      vscode.postMessage({ v: 1, kind: "req", id, method, params });
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
      });
    }

    function card(entry) {
      const root = document.createElement("div");
      root.className = "card " + (entry.kind || "event");
      const title = document.createElement("div");
      title.className = "title";
      const kind = entry.kind || entry.type || "event";
      const status = entry.status ? (" [" + entry.status + "]") : "";
      title.textContent = kind + status;
      root.appendChild(title);

      const detail = document.createElement("div");
      detail.className = "detail";
      detail.textContent = entry.summary || entry.detail || "";
      root.appendChild(detail);

      if (entry.payloadPreview) {
        const pre = document.createElement("div");
        pre.className = "small";
        pre.textContent = entry.payloadPreview;
        root.appendChild(pre);
      }

      if (entry.kind === "permission_request" && entry.requestId) {
        const actions = document.createElement("div");
        actions.className = "actions";
        const mk = (label, decision) => {
          const b = document.createElement("button");
          b.textContent = label;
          b.addEventListener("click", () => {
            if (!activeSessionId) return;
            rpc("bb.approvePermission", { sessionId: activeSessionId, requestId: entry.requestId, decision })
              .catch((err) => statusEl.textContent = "Permission decision failed: " + String(err));
          });
          return b;
        };
        actions.appendChild(mk("Allow once", "allow_once"));
        actions.appendChild(mk("Deny", "deny"));
        actions.appendChild(mk("Allow rule", "allow_rule"));
        root.appendChild(actions);
      }

      if ((entry.kind === "tool_result" || entry.kind === "tool_call") && entry.filePath) {
        const actions = document.createElement("div");
        actions.className = "actions";
        const open = document.createElement("button");
        open.textContent = "Open diff";
        open.addEventListener("click", () => {
          if (!activeSessionId) return;
          rpc("bb.openDiff", {
            sessionId: activeSessionId,
            filePath: entry.filePath,
            artifactPath: entry.artifactPath || undefined,
          }).catch((err) => {
            statusEl.textContent = "Open diff failed: " + String(err);
          });
        });
        actions.appendChild(open);
        root.appendChild(actions);
      }

      return root;
    }

    function renderTranscript() {
      transcriptEl.innerHTML = "";
      const tail = transcriptEntries.slice(-120);
      for (const entry of tail) {
        transcriptEl.appendChild(card(entry));
      }
      if (followTranscript) {
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
      }
    }

    function renderTasks() {
      taskListEl.innerHTML = "";
      const tail = taskEntries.slice(-120);
      taskSummaryEl.textContent = tail.length > 0 ? (tail.length + " task events") : "No task events yet.";
      for (const t of tail.reverse()) {
        const row = document.createElement("div");
        row.className = "task";
        row.textContent = (t.status ? ("[" + t.status + "] ") : "") + (t.summary || t.detail || "task event");
        taskListEl.appendChild(row);
      }
    }

    function renderRunSummary() {
      const active = activeSessionId ? activeSessionId : "(none)";
      runSummaryEl.textContent =
        "Active session: " + active + "\\n" +
        "Events received: " + totalEvents + "\\n" +
        "Pending permissions: " + pendingPermissions.length + "\\n" +
        "Run state: " + runningState;
      composerMetaEl.textContent = "state=" + runningState + " · pending_permissions=" + pendingPermissions.length;
      sendBtn.disabled = !activeSessionId || !msgEl.value.trim();
      stopBtn.disabled = !activeSessionId;
      stopBtn.textContent = runningState === "running" ? "Stop" : "Stop (idle)";
    }

    function renderAll() {
      renderTranscript();
      renderTasks();
      renderRunSummary();
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

    async function refreshFiles() {
      if (!activeSessionId) return;
      const path = filePathEl.value || ".";
      const files = await rpc("bb.listFiles", { sessionId: activeSessionId, path });
      fileListEl.innerHTML = "";
      for (const f of files || []) {
        const pathValue = f && typeof f.path === "string" ? f.path : null;
        if (!pathValue) continue;
        const item = document.createElement("div");
        item.className = "list-item";
        const label = (f.type === "directory" ? "📁 " : "📄 ") + pathValue;
        item.textContent = label;
        item.addEventListener("click", async () => {
          selectedFilePath = pathValue;
          selectedArtifactPath = null;
          if (f.type === "directory") {
            filePathEl.value = pathValue;
            await refreshFiles();
            return;
          }
          const snippet = await rpc("bb.readFileSnippet", {
            sessionId: activeSessionId,
            path: pathValue,
            headLines: 120,
            tailLines: 50,
            maxBytes: 120000,
          });
          snippetEl.textContent = (snippet && snippet.content ? snippet.content : "") +
            (snippet && snippet.truncated ? "\\n\\n...[truncated]..." : "");
        });
        fileListEl.appendChild(item);
      }
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
    newSessionBtn.addEventListener("click", () => {
      statusEl.textContent = "Use command palette: BreadBoard: New Session";
    });
    deleteSessionBtn.addEventListener("click", () => {
      const sessionId = sessionsEl.value;
      if (!sessionId) return;
      rpc("bb.deleteSession", { sessionId }).then(() => {
        if (activeSessionId === sessionId) activeSessionId = null;
        return refreshSessions();
      }).catch((err) => {
        statusEl.textContent = "Delete failed: " + String(err);
      });
    });

    attachBtn.addEventListener("click", () => {
      const sessionId = sessionsEl.value;
      if (!sessionId) return;
      rpc("bb.attachSession", { sessionId }).then(() => {
        activeSessionId = sessionId;
        return Promise.all([refreshSessions(), refreshFiles().catch(() => {})]);
      }).catch((err) => {
        statusEl.textContent = "Attach failed: " + String(err);
      });
    });

    function sendCurrentMessage() {
      const text = msgEl.value;
      if (!text || !activeSessionId) return;
      runningState = "running";
      renderRunSummary();
      rpc("bb.sendMessage", { sessionId: activeSessionId, text }).then(() => {
        msgEl.value = "";
        renderRunSummary();
      }).catch((err) => {
        runningState = "error";
        statusEl.textContent = "Send failed: " + String(err);
        renderRunSummary();
      });
    }
    sendBtn.addEventListener("click", sendCurrentMessage);

    msgEl.addEventListener("input", () => renderRunSummary());
    msgEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        sendCurrentMessage();
      }
    });

    stopBtn.addEventListener("click", () => {
      if (!activeSessionId) return;
      runningState = "stopping";
      renderRunSummary();
      rpc("bb.stopSession", { sessionId: activeSessionId }).catch((err) => {
        runningState = "error";
        statusEl.textContent = "Stop failed: " + String(err);
        renderRunSummary();
      });
    });

    refreshFilesBtn.addEventListener("click", () => {
      refreshFiles().catch((err) => statusEl.textContent = "List files failed: " + String(err));
    });
    insertRefBtn.addEventListener("click", () => {
      if (!selectedFilePath) return;
      const value = msgEl.value;
      const sep = value.length > 0 && !value.endsWith(" ") ? " " : "";
      msgEl.value = value + sep + "@" + selectedFilePath;
      setActiveTab("chat");
      renderRunSummary();
    });
    openDiffBtn.addEventListener("click", () => {
      if (!activeSessionId || !selectedFilePath) return;
      rpc("bb.openDiff", {
        sessionId: activeSessionId,
        filePath: selectedFilePath,
        artifactPath: selectedArtifactPath || undefined,
      }).catch((err) => statusEl.textContent = "Open diff failed: " + String(err));
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
          if (payload.gapDetected) {
            statusEl.textContent += "\\nContinuity gap detected. Re-attach or restart session stream.";
          }
          runningState = "error";
        } else {
          statusEl.textContent = "Connecting...";
        }
        scheduleRender();
      } else if (message.topic === "bb/state") {
        const payload = message.payload || {};
        if (payload.activeSessionId && typeof payload.activeSessionId === "string") {
          activeSessionId = payload.activeSessionId;
          sessionsEl.value = activeSessionId;
        }
        scheduleRender();
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
        if (render && Array.isArray(render.entries)) {
          transcriptEntries = render.entries.map((entry) => (entry && typeof entry === "object" ? entry : {}));
        }
        pendingPermissions = transcriptEntries.filter((entry) => entry && entry.kind === "permission_request");
        taskEntries = transcriptEntries.filter((entry) => entry && entry.kind === "task_event");
        if (["turn_start", "assistant_message_start", "assistant_delta", "tool_call"].includes(lastType)) {
          runningState = "running";
        } else if (["run_finished", "completion", "assistant_message_end"].includes(lastType)) {
          runningState = "idle";
        }
        const tail = transcriptEntries[transcriptEntries.length - 1];
        if (tail && tail.filePath) {
          selectedFilePath = tail.filePath;
          selectedArtifactPath = tail.artifactPath || null;
        }
        scheduleRender();
      }
    });

    refreshSessions().then(() => scheduleRender()).catch(() => {});
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
